# Copyright (C) 2022  Vincent Pelletier <plr.vincent@gmail.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""
sn8f2288 to FunctionFS bridge

To get Linux to talk to the emulated sn8f2288 over USB.

TODO: test endpoint (non-zero) traffic (which requires some support to be added
in asmlib/usb.asm).
"""
import argparse
import binascii
from collections import defaultdict
import ctypes
import functools
import select
import struct
import functionfs
import functionfs.gadget
import functionfs.ch9
from . import libsimsn8
from .simsn8 import EndpointStall, SN8F2288

_DESCRIPTOR_TYPE_DICT = {
    (ctypes.sizeof(klass), klass._bDescriptorType): klass
    for klass in (
        functionfs.ch9.USBDeviceDescriptor,
        functionfs.ch9.USBConfigDescriptor,
        #functionfs.ch9.USBOtherSpeedConfig,
        #functionfs.ch9.USBStringDescriptor,
        functionfs.ch9.USBInterfaceDescriptor,
        functionfs.ch9.USBEndpointDescriptorNoAudio,
        functionfs.ch9.USBEndpointDescriptor,
        #functionfs.ch9.USBQualifierDescriptor,
        #functionfs.ch9.USBInterfaceAssocDescriptor,
    )
}

def _parseAsDescriptorList(buf):
    result = []
    while buf:
        length, descriptor_type = struct.unpack('BB', buf[:2])
        result.append(_DESCRIPTOR_TYPE_DICT[
            (length, descriptor_type)
        ].from_buffer_copy(buf))
        buf = buf[length:]
    return result

_DEVICE_DESCRIPTOR_STRING_KEY_MAP = {
    'manufacturer': 'iManufacturer',
    'product': 'iProduct',
    'serialnumber': 'iSerialNumber',
}

_CONFIGURATION_DESCRIPTOR_STRING_KEY_MAP = {
    'configuration': 'iConfiguration',
}

_DESCRIPTOR_CLASS_TO_STRING_INDEX_ATTRIBUTE_ID_LIST_DICT = {
    functionfs.ch9.USBInterfaceDescriptor: (
        'iInterface',
    ),
    functionfs.ch9.USBEndpointDescriptorNoAudio: (),
    functionfs.ch9.USBEndpointDescriptor: (),
}

class EndpointINFile(functionfs.EndpointINFile):
    def __init__(self, sn8_usb_device, descriptor, **kw):
        super().__init__(**kw)
        _ = sn8_usb_device
        self.__bEndpointAddress = descriptor.bEndpointAddress & 0x7f
        self.is_interrupt = (
            descriptor.bmAttributes & functionfs.ch9.USB_ENDPOINT_XFERTYPE_MASK == functionfs.ch9.USB_ENDPOINT_XFER_INT
        )

    @property
    def sn8_address(self):
        return self.__bEndpointAddress

class EndpointOUTFile(functionfs.EndpointOUTFile):
    def __init__(self, sn8_usb_device, descriptor, **kw):
        super().__init__(**kw)
        self.__usb_device = sn8_usb_device
        self.__bEndpointAddress = descriptor.bEndpointAddress
        self.__wMaxPacketSize = descriptor.wMaxPacketSize

    @property
    def sn8_address(self):
        return self.__bEndpointAddress

    def onComplete(self, data, status):
        if status == 0:
            self.__usb_device.writeEP(
                endpoint=self.__bEndpointAddress,
                data=data,
                max_packet_size=self.__wMaxPacketSize,
            )

class SN8Function(functionfs.Function):
    def __init__(self, gadget, sn8_usb_device, sn8_configuration, **kw):
        super().__init__(**kw)
        self.__usb_device = sn8_usb_device
        self.__gadget = gadget
        self.__sn8_configuration = sn8_configuration

    def getEndpointClass(self, is_in, descriptor):
        return functools.partial(
            EndpointINFile if is_in else EndpointOUTFile,
            sn8_usb_device=self.__usb_device,
            descriptor=descriptor,
        )

    def onBind(self):
        print('onBind')
        if self.config0_setup:
            self.__gadget.onFunctionEnable([(0, self.ep0)])

    def onUnbind(self):
        print('onUnbind')
        if self.config0_setup:
            self.__gadget.onFunctionDisable([(0, self.ep0)])

    def __getControlledEndpointList(self):
        return [
            (
                endpoint.sn8_address,
                endpoint,
            )
            for endpoint in self._ep_list[1:]
        ]

    def onEnable(self):
        print('onEnable')
        super().onEnable()
        if self.__sn8_configuration is not None:
            self.__usb_device.setConfiguration(
                configuration=self.__sn8_configuration,
            )
        self.__gadget.onFunctionEnable(self.__getControlledEndpointList())

    def onDisable(self):
        print('onDisable')
        if self.__sn8_configuration is not None:
            self.__usb_device.setConfiguration(
                configuration=0,
            )
        self.__gadget.onFunctionDisable(self.__getControlledEndpointList())

    def onSetup(self, request_type, request, value, index, length):
        print(
            'onSetup request_type=%#04x, request=%#04x, value=%#06x, index=%#06x, length=%#06x' % (
                request_type, request, value, index, length
            ),
        )
        try:
            if request_type & functionfs.ch9.USB_DIR_IN:
                self.ep0.write(
                    self.__usb_device.controlRead(
                        request_type=request_type,
                        request=request,
                        value=value,
                        index=index,
                        length=length,
                    ),
                )
            else:
                self.__usb_device.controlWrite(
                    request_type=request_type,
                    request=request,
                    value=value,
                    index=index,
                    data=self.ep0.read(length),
                )
        except EndpointStall:
            self.ep0.halt(request_type)

    def onSuspend(self):
        print('onSuspend')
        if self.config0_setup:
            self.__usb_device.suspend()

    def onResume(self):
        print('onResume')
        if self.config0_setup:
            self.__usb_device.resume()

class SN8Gadget(functionfs.gadget.Gadget):
    __usb_is_enabled = False
    trace = False

    def __init__(self, cpu, step=None, name=None, udc=None):
        """
        Retrieves needed USB descriptors from the emulated CPU, and populates a
        configfs usb gadget with retrieved values.
        Note: this relies on the firmware behaving in non-surprising ways:
        - its descriptors are fetched only initially
        - it will be enumerated only once
        - string descriptors not referenced in descriptors are inqccessible
        - some indexes will be rewritten by the kernel (endpoint addresses,
          string indexes, ...).
        - configuration descriptors beyond the bNumConfigurations first ones
          are not accessible.
        - explicit data toggle is ignored
        - CRC errors never happen
        - packet error never happen
        - SOF events never happen
        - firmware should not disable USB once enabled
        - (and certainly much more)
        """
        self.__active_endpoint_dict = {}
        self.__cpu = cpu
        if step is None:
            step = cpu.step
        self.__step = step
        step = self.step
        sn8_usb_device = libsimsn8.USBDevice(
            cpu=cpu,
            step=step,
            #on_wake_signaling=,
            on_enable_change=self.__onUSBEnableChange,
            on_ep_enable_change=self.__onEndpointEnableChange,
        )
        print('Waiting for USB to be enabled...')
        while not self.__usb_is_enabled and cpu.run_time < 200: # XXX: hardcoded
            step()
        if not self.__usb_is_enabled:
            raise ValueError('Firmware did not enable USB after 200ms')
        print('  USB enabled at %.6fms' % cpu.run_time)
        print('USB reset...')
        sn8_usb_device.reset()
        print('  done at %.6fms' % cpu.run_time)
        # Based on linux enumeration sequence, only so firmware sees a
        # realistic enumeration:
        # Get first 8 bytes of the device descriptor. The intent in Linux is to
        # get bMaxPacketSize0. Here we do not need to (and cannot) do anything
        # with it.
        print('Requesting DT_DEVICE first 8 bytes...')
        partial_device_descriptor = sn8_usb_device.getDescriptor(
            functionfs.ch9.USB_DT_DEVICE,
            8,
        )
        assert len(partial_device_descriptor) == 8, repr(
            partial_device_descriptor
        )
        print('  done at %.6fms: %s' % (cpu.run_time, binascii.b2a_hex(partial_device_descriptor)))
        # Set device address.
        print('Setting device address...')
        sn8_usb_device.setAddress(1) # Any address will do.
        print('  done at %.6fms' % cpu.run_time)
        # Get entire device descriptor.
        print('Requesting full DT_DEVICE...')
        device_descriptor = sn8_usb_device.getDescriptor(
            functionfs.ch9.USB_DT_DEVICE,
            ctypes.sizeof(functionfs.ch9.USBDeviceDescriptor),
        )
        print('  done at %.6fms: %s' % (cpu.run_time, binascii.b2a_hex(device_descriptor)))
        device_descriptor, = _parseAsDescriptorList(device_descriptor)
        assert isinstance(
            device_descriptor,
            functionfs.ch9.USBDeviceDescriptor,
        )
        # Get device qualifier descriptor.
        print('Requesting DT_DEVICE_QUALIFIER...')
        try:
            device_qualifier_descriptor = sn8_usb_device.getDescriptor(
                functionfs.ch9.USB_DT_DEVICE_QUALIFIER,
                ctypes.sizeof(functionfs.ch9.USBQualifierDescriptor),
            )
        except EndpointStall:
            device_qualifier_descriptor = None
        print('  done at %.6fms: %s' % (
            cpu.run_time,
            (
                device_qualifier_descriptor
                if device_qualifier_descriptor is None else
                binascii.b2a_hex(device_qualifier_descriptor)
            ),
        ))
        if device_qualifier_descriptor is not None:
            device_qualifier_descriptor, = _parseAsDescriptorList(
                device_qualifier_descriptor,
            )
            assert isinstance(
                device_qualifier_descriptor,
                functionfs.ch9.USBQualifierDescriptor,
            ), repr(device_qualifier_descriptor)
            # TODO: do something with this descriptor
            _ = device_qualifier_descriptor
        # XXX: here, diverge from standard enumeration order to be ready to
        # fetch string descriptors.
        # Get the list of advertised string descriptor languages.
        print('Requesting DT_STRING 0...')
        try:
            language_descriptor = sn8_usb_device.getDescriptor(
                functionfs.ch9.USB_DT_STRING,
                255,
            )[2:]
        except EndpointStall:
            language_list = ()
        else:
            language_descriptor_len = len(language_descriptor)
            assert language_descriptor_len & 1 == 0
            language_list = struct.unpack(
                '<H' * (len(language_descriptor) >> 1),
                language_descriptor,
            )
        print(
            '  done at %.6fms: %s' % (
                cpu.run_time,
                ', '.join('%#06x' % x for x in language_list)
            ),
        )
        # string_language_dict is a cache, in case a given string is referenced
        # in multiple places.
        string_language_dict = defaultdict(dict)
        def getRawString(string_index, language):
            string_dict = string_language_dict[language]
            try:
                value = string_dict[string_index]
            except KeyError:
                # If this raises EndpointStall, device has inconsistent
                # descriptors (non-zero string index in a descriptor, but no
                # string at such index).
                print('Requesting DT_STRING %i...' % (string_index, ))
                value = string_dict[string_index] = sn8_usb_device.getDescriptor(
                    functionfs.ch9.USB_DT_STRING,
                    255,
                    string_index,
                    language=language,
                )[2:]
                print('  done at %.6fms: %s' % (cpu.run_time, binascii.b2a_hex(value)))
            return value
        def getString(string_index, language):
            return getRawString(string_index, language).decode('utf-16')
        def getDescriptorStringDict(descriptor, key_map):
            result = defaultdict(dict)
            for result_key, descriptor_attribute_id in key_map.items():
                string_index = getattr(descriptor, descriptor_attribute_id)
                if string_index:
                    for language in language_list:
                        result[language][result_key] = getString(
                            string_index=string_index,
                            language=language,
                        )
            return result
        # Fetch Microsoft's OS Descriptor
        os_desc = None
        #if language_list:
        #    try:
        #        os_desc_string = getRawString(
        #            string_index=0xee,
        #            language=language_list[0], # Whatever
        #        )
        #    except EndpointStall:
        #        pass
        #    else:
        #        b_vendor_code = os_desc_string[14]
        #        os_desc = {
        #            'qw_sign': os_desc_string[:14],
        #            'b_vendor_code': b_vendor_code,
        #        }
        # Fetch all configuration descriptors.
        configuration_list = []
        for configuration_index in range(device_descriptor.bNumConfigurations):
            configuration_index += 1
            # Get configuration descriptor alone.
            print('Requesting DT_CONFIG %i...' % (configuration_index, ))
            configuration_descriptor = sn8_usb_device.getDescriptor(
                functionfs.ch9.USB_DT_CONFIG,
                ctypes.sizeof(functionfs.ch9.USBConfigDescriptor),
                index=configuration_index,
            )
            print('  done at %.6fms: %s' % (cpu.run_time, binascii.b2a_hex(configuration_descriptor)))
            configuration_descriptor, = _parseAsDescriptorList(
                configuration_descriptor,
            )
            assert isinstance(
                configuration_descriptor,
                functionfs.ch9.USBConfigDescriptor,
            ), repr(configuration_descriptor)
            # Get the inner configuration descriptors (all consecutive
            # descriptors within current configuration).
            print('Requesting DT_CONFIG %i and following...' % (configuration_index, ), )
            descriptors_list = sn8_usb_device.getDescriptor(
                functionfs.ch9.USB_DT_CONFIG,
                configuration_descriptor.wTotalLength,
                index=configuration_index,
            )
            print('  done at %.6fms: %s' % (cpu.run_time, binascii.b2a_hex(descriptors_list)))
            descriptors_list = _parseAsDescriptorList(
                descriptors_list,
            )[1:]
            lang_dict = defaultdict(list)
            configuration_string_index_dict = {}
            os_list = []
            has_interface_descriptor = False
            function_list = []
            interface_descriptor_list = []
            in_interface = False
            for descriptor in descriptors_list:
                is_interface_descriptor = isinstance(
                    descriptor,
                    functionfs.ch9.USBInterfaceDescriptor,
                )
                has_interface_descriptor |= is_interface_descriptor
                if is_interface_descriptor:
                    if in_interface:
                        function_list.append((
                            interface_descriptor_list,
                            lang_dict,
                            os_list,
                        ))
                        interface_descriptor_list = []
                        lang_dict.clear()
                        os_list = []
                    else:
                        in_interface = True
                elif not in_interface:
                    raise ValueError(
                        'First descriptor in configuration %i is not a function descriptor' % (configuration_index, ),
                    )
                interface_descriptor_list.append(descriptor)
                #if os_desc is not None and is_interface_descriptor:
                #    for os_descriptor_type in (
                #        4, # Extended compat ID
                #        5, # Extended properties
                #    ):
                #        for index in count():
                #            data = sn8_usb_device.controlRead(
                #                request_type=0xc0, # TODO: use named constants
                #                request=b_vendor_code,
                #                value=(descriptor.bInterfaceNumber << 8) | index,
                #                index=os_descriptor_type,
                #                length=0xffff,
                #            )
                #            os_list.append(data)
                #            if len(data) < 0xffff:
                #                break
                for attribute_id in (
                    _DESCRIPTOR_CLASS_TO_STRING_INDEX_ATTRIBUTE_ID_LIST_DICT[
                        descriptor.__class__
                    ]
                ):
                    original_index = getattr(descriptor, attribute_id)
                    if original_index:
                        try:
                            remapped_index = configuration_string_index_dict[
                                original_index
                            ]
                        except KeyError:
                            assert language_list
                            for language in language_list:
                                language_string_list = lang_dict[language]
                                language_string_list.append(
                                    getString(
                                        string_index=original_index,
                                        language=language
                                    )
                                )
                            # Any is fine, all have the same length
                            remapped_index = configuration_string_index_dict[
                                original_index
                            ] = len(language_string_list)
                        setattr(
                            descriptor,
                            attribute_id,
                            remapped_index,
                        )
            if not has_interface_descriptor:
                raise ValueError(
                    'No interface descriptor found in configuration %i' % (
                        configuration_index,
                    )
                )
            # Add the last function
            function_list.append((
                interface_descriptor_list,
                lang_dict,
                os_list,
            ))
            configuration_list.append(
                (
                    configuration_descriptor,
                    function_list,
                ),
            )
        if not configuration_list:
            raise ValueError('No configuration found')

        super().__init__(
            config_list=[
                {
                    'function_list': [
                        functionfs.gadget.ConfigFunctionFFS(
                            name='sn8_%i_%i' % (
                                configuration_index,
                                interface_index,
                            ),
                            getFunction=functools.partial(
                                SN8Function,
                                gadget=self,
                                sn8_usb_device=sn8_usb_device,
                                sn8_configuration=(
                                    configuration_descriptor.bConfigurationValue
                                    if interface_index == 0 else
                                    None
                                ),
                                hs_list=hs_list,
                                os_list=os_list,
                                all_ctrl_recip=interface_index == 0,
                                config0_setup=(
                                    configuration_index == 1 and
                                    interface_index == 0
                                ),
                                lang_dict=lang_dict,
                            ),
                        )
                        for (
                            interface_index,
                            (
                                hs_list,
                                lang_dict,
                                os_list,
                            ),
                        ) in enumerate(function_list)
                    ],
                    'bmAttributes': configuration_descriptor.bmAttributes,
                    'MaxPower': configuration_descriptor.bMaxPower,
                    'lang_dict': getDescriptorStringDict(
                        descriptor=configuration_descriptor,
                        key_map=_CONFIGURATION_DESCRIPTOR_STRING_KEY_MAP,
                    )
                }
                for (
                    configuration_index,
                    (
                        configuration_descriptor,
                        function_list,
                    ),
                ) in enumerate(configuration_list, 1)
            ],
            bcdUSB=device_descriptor.bcdUSB,
            bDeviceClass=device_descriptor.bDeviceClass,
            bDeviceSubClass=device_descriptor.bDeviceSubClass,
            bDeviceProtocol=device_descriptor.bDeviceProtocol,
            idVendor=device_descriptor.idVendor,
            idProduct=device_descriptor.idProduct,
            bcdDevice=device_descriptor.bcdDevice,
            lang_dict=getDescriptorStringDict(
                descriptor=device_descriptor,
                key_map=_DEVICE_DESCRIPTOR_STRING_KEY_MAP,
            ),
            name=name,
            udc=udc,
            os_desc=os_desc,
        )

    def step(self):
        if self.trace:
            cpu = self.__cpu
            print('%r %s' % (
                cpu,
                (
                    (
                        ' FEP0OUT'
                        if cpu.usb.FEP0OUT else
                        ''
                    ) +
                    (
                        ' FEP0IN'
                        if cpu.usb.FEP0IN else
                        ''
                    ) +
                    (
                        ' FEP0SETUP'
                        if cpu.usb.FEP0SETUP else
                        ''
                    )
                ),
            ))
        self.__step()

    def __onUSBEnableChange(self, is_enabled):
        self.__usb_is_enabled = is_enabled

    def __onEndpointEnableChange(self, endpoint, stall, ack):
        if endpoint == 0:
            return
        active_endpoint_file = self.__active_endpoint_dict[endpoint]
        if stall:
            active_endpoint_file.halt()
        elif ack and active_endpoint_file.writable():
            usb = self.__cpu.usb
            active_endpoint_file.write(usb.recv(endpoint))
            if active_endpoint_file.is_interrupt:
                usb.ackInterruptIN(endpoint)

    def onFunctionEnable(self, endpoint_list):
        active_endpoint_dict = self.__active_endpoint_dict
        for endpoint_address, endpoint in endpoint_list:
            active_endpoint = active_endpoint_dict.setdefault(
                endpoint_address,
                endpoint,
            )
            assert active_endpoint is endpoint

    def onFunctionDisable(self, endpoint_list):
        active_endpoint_dict = self.__active_endpoint_dict
        for endpoint_address, endpoint in endpoint_list:
            inactive_endpoint = active_endpoint_dict.pop(endpoint_address)
            assert inactive_endpoint is endpoint

    def __sleep(self, duration):
        cpu = self.__cpu
        deadline = cpu.run_time + duration
        step = self.step
        while cpu.run_time < deadline:
            step()

    def iterFunctions(self):
        return self._iterFunctions()

def main():
    parser = argparse.ArgumentParser(
        description='Stand-alone SN8F2288 emulator operating as '
        'Linux USB gadget',
    )
    parser.add_argument('--udc', help='UDC controller to use')
    parser.add_argument('firmware', help='Binary firmware image')
    args = parser.parse_args()
    with open(args.firmware, 'rb') as firmware:
        cpu = SN8F2288(firmware)
    with SN8Gadget(
        cpu=cpu,
        udc=args.udc,
    ) as gadget:
        step = gadget.step
        epoll = select.epoll()
        handler_dict = {}
        for function in gadget.iterFunctions():
            function = function.function
            fileno = function.eventfd.fileno()
            epoll.register(fileno, select.EPOLLIN)
            handler_dict[fileno] = function.processEvents
        # poll timeout, in seconds
        DELAY_POLL = 0.1 # XXX: arbitrary
        # artificially slow the microcontroller down so it does not burn cpu cycles
        MICRO_RATIO = 0.1
        # microcontroller simulated time slice, in milliseconds
        DELAY_MICRO = DELAY_POLL * 1000 * MICRO_RATIO
        try:
            while True:
                # TODO: poll with some (small) timeout, to not eat an entire cpu
                # and maybe call sleep on the µC with the same duration
                for fd, event in epoll.poll(DELAY_POLL):
                    handler_dict[fd]()
                deadline = cpu.run_time + DELAY_MICRO
                while cpu.run_time < deadline:
                    step()
        except KeyboardInterrupt:
            pass

if __name__ == '__main__':
    main()
