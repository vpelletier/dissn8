#!/usr/bin/env python3
# Copyright (C) 2019  Vincent Pelletier <plr.vincent@gmail.com>
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

import argparse
from collections import defaultdict
from functools import partial
from struct import unpack
import warnings
from sn8.simsn8 import SN8F2288, INF, EndpointStall, EndpointNAK, RESET_SOURCE_LOW_VOLTAGE
from sn8.libsimsn8 import BitBanging8bitsI2C

def hexdump(value):
    return ' '.join('%02x' % x for x in value)

MOUSE_IDLE = 0
MOUSE_INIT1 = 1
MOUSE_INITIALISED = 2
USB_RECIPIENT_DEVICE = 0x00
USB_RECIPIENT_INTERFACE = 0x01
USB_RECIPIENT_ENDPOINT = 0x02

class Timeout(Exception):
    pass

class KU1255:
    def __init__(self, firmware):
        self.cpu = cpu = SN8F2288(firmware)
        # Key matrix emulation
        self.matrix = [
            partial(cpu.p0.getInternalAsLoad, 3),
            partial(cpu.p0.getInternalAsLoad, 4),
            partial(cpu.p0.getInternalAsLoad, 5),
            partial(cpu.p0.getInternalAsLoad, 6),
            partial(cpu.p2.getInternalAsLoad, 0),
            partial(cpu.p2.getInternalAsLoad, 1),
            partial(cpu.p2.getInternalAsLoad, 2),
            partial(cpu.p2.getInternalAsLoad, 3),
            partial(cpu.p4.getInternalAsLoad, 0),
            partial(cpu.p4.getInternalAsLoad, 1),
            partial(cpu.p4.getInternalAsLoad, 2),
            partial(cpu.p4.getInternalAsLoad, 3),
            partial(cpu.p4.getInternalAsLoad, 4),
            partial(cpu.p4.getInternalAsLoad, 5),
            partial(cpu.p4.getInternalAsLoad, 6),
            partial(cpu.p4.getInternalAsLoad, 7),
        ]
        for column in range(8):
            cpu.p1.setLoad(column, partial(self.getKeyLoad, column))
        # USB host emulation
        usb = cpu.usb
        usb.on_wake_signaling = self.onUSBWakeupRequest
        usb.on_enable_change = self.onUSBEnableChange
        usb.on_ep_enable_change = self.onUSBEPEnableChange
        # Bit-banging I2C device emulation (mouse)
        self._i2c_mouse = BitBanging8bitsI2C(
            address=0x2a,
            onAddressed=self._onI2CAddressed,
            onDataByteReceived=self._onI2CDataByteReceived,
            getNextDataByte=self._getNextI2CDataByte,
        )
        cpu.p2.setLoad(4, self.getI2CSCLLoad)
        cpu.p2.setLoad(5, self.getI2CSDALoad)
        cpu.p2.setLoad(6, self.getMouseATTNLoad)
        self.scl_pull_up = self.sda_pull_up = self.mouse_attn_pull_up = 3300 # 3.3k Ohms
        self._trace = False
        self._reset()

    @property
    def trace(self):
        return self._trace

    @trace.setter
    def trace(self, value):
        if value and not self._trace:
            self._tracer()
        self._trace = value

    def _tracer(self):
        print(repr(self.cpu))

    def __repr__(self):
        mouse_buttons = []
        mouse_buttons_byte = self.i2c_buffer[1]
        if mouse_buttons_byte & 1:
            mouse_buttons.append('left')
        if mouse_buttons_byte & 4:
            mouse_buttons.append('middle')
        if mouse_buttons_byte & 2:
            mouse_buttons.append('right')
        return '<%s@%08x usb=%s keys=%s mouse=%s>' % (
            self.__class__.__name__,
            id(self),
            (
                (
                    'suspended' if self.cpu.usb.FSUSPEND else 'on'
                ) + ',' + ','.join(
                    'ep%i' % x
                    for x, y in enumerate(self.usb_is_endpoint_enabled)
                    if y
                )
            ) if self.usb_is_enabled else 'off',
            ';'.join(
                '%i,%i' % (x, y)
                for x, y_list in enumerate(self.row_list_by_column)
                for y in y_list
            ),
            ','.join(
                mouse_buttons + [
                    'x=%i' % unpack('b', chr(self.i2c_buffer[2])),
                    'y=%i' % unpack('b', chr(self.i2c_buffer[3])),
                ]
            ),
        )

    def reset(self):
        self.cpu.reset(RESET_SOURCE_LOW_VOLTAGE)
        self._reset()

    def _reset(self):
        # Key matrix
        self.row_list_by_column = [
            []
            for x in range(8)
        ]
        self.column_list_by_row = [
            []
            for x in range(len(self.matrix))
        ]
        # USB
        self.usb_is_enabled = False
        self.usb_is_endpoint_enabled = [True, False, False, False, False]
        self.usb_is_wakeup_requested = False
        # I2C
        self.mouse_attn_float = True
        self.mouse_initialisation_state = MOUSE_IDLE
        self.i2c_buffer = [
            0x80, # Value known, meaning unknown
            0x00, # Buttons
            0x00, # x, two's complement
            0x00, # y, two's complement
            0x00, # Value unknown, meaning unknown
        ]
        self.i2c_buffer_index = 0
        self.i2c_in_buffer = []
        self._i2c_mouse.reset()

    def step(self):
        cpu = self.cpu
        if self._trace:
            self._tracer()
        cpu.step()
        # Assume CPU agrees with device on bus state
        p2 = cpu.p2.read()
        self._i2c_mouse.step(p2 & 0x10, p2 & 0x20)

    def _onI2CAddressed(self, read):
        if read:
            self.i2c_buffer_index = 0
        else:
            self.i2c_in_buffer = []
        return True

    def _onI2CDataByteReceived(self, data_byte):
        #print 'Received data byte %#04x' % data_byte
        self.i2c_in_buffer.append(data_byte)
        if self.i2c_in_buffer == [0xfc]:
            self.mouse_initialisation_state = MOUSE_INIT1
            return True
        if self.i2c_in_buffer == [0xc4]:
            self.mouse_initialisation_state = MOUSE_INITIALISED
            #self.mouse_attn_float = False
            return True
        warnings.warn(
            'Mouse received unknown byte sequence received from '
            'cpu: %s' % ','.join(
                '%#03x' % x for x in self.i2c_in_buffer
            ),
        )
        return False

    def _getNextI2CDataByte(self):
        # Prepare first bit of next byte
        try:
            result = self.i2c_buffer[self.i2c_buffer_index]
        except IndexError:
            return None
        self.i2c_buffer_index += 1
        if self.i2c_buffer_index > 3:
            # CPU has received buttons, x and y.
            # No need for further attention.
            self.mouse_attn_float = True
        return result

    def getKeyLoad(self, column):
        previous_column_count = 0
        previous_row_count = 0
        column_set = set([column])
        row_set = set()
        # Find all rows reachable from current set of key presses,
        # which also means finding all columns reachable (just to find further
        # rows). This loop should iterate at most 8 times (shortest key matrix
        # dimension).
        while (
            previous_column_count != len(column_set) or
            previous_row_count != len(row_set)
        ):
            previous_column_count = len(column_set)
            previous_row_count = len(row_set)
            for connected_column in column_set:
                row_set.update(self.row_list_by_column[connected_column])
            for connected_row in row_set:
                column_set.update(self.column_list_by_row[connected_row])
        impedance_by_voltage = defaultdict(list)
        for load_row in row_set:
            voltage, impedance = self.matrix[load_row]()
            if impedance != INF:
                impedance_by_voltage[voltage].append(impedance)
        getP1InternalLoad = self.cpu.p1.getInternalAsLoad
        for load_column in column_set:
            if load_column == column:
                continue
            voltage, impedance = getP1InternalLoad(load_column)
            if impedance != INF:
                impedance_by_voltage[voltage].append(impedance)
        for key, value in impedance_by_voltage.items():
            impedance_by_voltage[key] = 1 / sum(1 / x for x in value)
        if impedance_by_voltage:
            voltage, impedance = impedance_by_voltage.popitem()
            while impedance_by_voltage:
                other_voltage, other_impedance = impedance_by_voltage.popitem()
                if voltage < other_voltage:
                    voltage, other_voltage = other_voltage, voltage
                    impedance, other_impedance = other_impedance, impedance
                voltage = other_voltage + (voltage - other_voltage) * other_impedance / (impedance + other_impedance)
                impedance = 1 / (1 / impedance + 1 / other_impedance)
        else:
            voltage = 0
            impedance = INF
        return voltage, impedance

    def getI2CSCLLoad(self):
        if self._i2c_mouse.scl_float:
            return self.cpu.p1.vdd, self.scl_pull_up
        return 0, 0 # Assume perfect short circuit to ground

    def getI2CSDALoad(self):
        if self._i2c_mouse.sda_float:
            return self.cpu.p1.vdd, self.sda_pull_up
        return 0, 0 # Assume perfect short circuit to ground

    def getMouseATTNLoad(self):
        if self.mouse_attn_float:
            return self.cpu.p1.vdd, self.mouse_attn_pull_up
        return 0, 0 # Assume perfect short circuit to ground

    # External API, to used in tests

    def pressKey(self, row, column):
        assert row >= 0
        assert column >= 0
        if row in self.row_list_by_column[column]:
            raise ValueError('Key at %ix%i already pressed' % (row, column))
        self.row_list_by_column[column].append(row)
        self.column_list_by_row[row].append(column)

    def releaseKey(self, row, column):
        assert row >= 0
        assert column >= 0
        if row not in self.row_list_by_column[column]:
            raise ValueError('Key at %ix%i already released' % (row, column))
        self.row_list_by_column[column].remove(row)
        self.column_list_by_row[row].remove(column)

    def setMouseState(self, x, y, left, middle, right):
        assert -128 <= x <= 127
        assert -128 <= y <= 127
        button_byte = 0
        if left:
            button_byte |= 1
        if right:
            button_byte |= 2
        if middle:
            button_byte |= 4
        self.i2c_buffer[1] = button_byte
        self.i2c_buffer[2] = x & 0xff
        self.i2c_buffer[3] = y & 0xff
        self.mouse_attn_float = False

    def _waitForEP0EventsHandled(self, deadline):
        cpu = self.cpu
        step = self.step
        while (cpu.FEP0SETUP or cpu.FEP0IN or cpu.FEP0OUT) and cpu.run_time < deadline:
            step()

    def controlRead(self, request_type, request, value, index, length, timeout):
        cpu = self.cpu
        deadline = cpu.run_time + timeout
        self._waitForEP0EventsHandled(deadline)
        cpu.usb.sendSETUP(request_type, request, value, index, length)
        self._waitForEP0EventsHandled(deadline)
        # Hardcoded max packet size, as it is fixed by spu for endpoint 0
        return self._readEP(0, length, 8, deadline)

    def controlWrite(self, request_type, request, value, index, data, timeout):
        cpu = self.cpu
        deadline = cpu.run_time + timeout
        self._waitForEP0EventsHandled(deadline)
        cpu.usb.sendSETUP(request_type, request, value, index, len(data))
        # Hardcoded max packet size, as it is fixed by cpu for endpoint 0
        self._waitForEP0EventsHandled(deadline)
        self._writeEP(0, data, 8, deadline)

    def readEP(self, endpoint, length, max_packet_size, timeout=5):
        return self._readEP(endpoint, length, max_packet_size, self.cpu.run_time + timeout)

    def writeEP(self, endpoint, data, max_packet_size, timeout=5):
        self._writeEP(endpoint, data, max_packet_size, self.cpu.run_time + timeout)

    def _waitForAckOrStall(self, endpoint, deadline):
        cpu = self.cpu
        step = self.step
        stall_attr_name = (
            'FUE0M1',
            'FUE1M1',
            'FUE2M1',
            'FUE3M1',
            'FUE4M1',
        )[endpoint]
        ack_attr_name = (
            'FUE0M0',
            'FUE1M0',
            'FUE2M0',
            'FUE3M0',
            'FUE4M0',
        )[endpoint]
        while (
            not getattr(cpu, stall_attr_name) and
            not getattr(cpu, ack_attr_name) and
            cpu.run_time < deadline
        ):
            step()

    def _readEP(self, endpoint, length, max_packet_size, deadline):
        recv = self.cpu.usb.recv
        result = b''
        while True:
            # Wait for data to be available in endpoint buffer.
            self._waitForAckOrStall(endpoint, deadline)
            chunk = recv(endpoint)
            result += chunk
            if len(result) == length or len(chunk) < max_packet_size:
                break
        return result

    def _writeEP(self, endpoint, data, max_packet_size, deadline):
        send = self.cpu.usb.send
        while data:
            # Wait for room to be available in endpoint buffer.
            self._waitForAckOrStall(endpoint, deadline)
            send(endpoint, data[:max_packet_size])
            data = data[max_packet_size:]

    def clearFeature(self, recipient, feature, index=0, timeout=5):
        self.controlWrite(
            recipient,
            1,
            feature,
            index,
            '',
            timeout,
        )

    def setFeature(self, recipient, feature, index=0, timeout=5):
        self.controlWrite(
            recipient,
            3,
            feature,
            index,
            '',
            timeout,
        )

    def getStatus(self, recipient, index, timeout=5):
        return unpack('<H', self.controlRead(
            0x80 | recipient,
            0,
            0,
            index,
            2,
            timeout,
        ))

    def getConfiguration(self, timeout=5):
        return ord(self.controlRead(
            0x80,
            8,
            0,
            0,
            1,
            timeout,
        ))

    def setConfiguration(self, configuration, timeout=5):
        self.controlWrite(
            0,
            9,
            configuration,
            0,
            '',
            timeout,
        )

    def getInterface(self, interface, timeout=5):
        return ord(self.controlRead(
            0x81,
            10,
            0,
            interface,
            1,
            timeout,
        ))

    def setInterface(self, interface, alt_setting, timeout=5):
        self.controlWrite(
            1,
            11,
            alt_setting,
            interface,
            '',
            timeout,
        )

    def getDescriptor(self, descriptor_type, length, index=0, language=0, timeout=5):
        return self.controlRead(
            0x80,
            6,
            (descriptor_type << 8) | index,
            language,
            length,
            timeout,
        )

    def setAddress(self, address, timeout=5):
        self.controlWrite(
            0,
            5,
            address,
            0,
            '',
            timeout,
        )

    def getHIDReport(self, report_type, report_id, interface, length, timeout=5):
        return self.controlRead(
            0b10100001,
            1,
            (report_type << 8) | report_id,
            interface,
            length,
            timeout,
        )

    def getHIDIdle(self, report_id, interface, timeout=5):
        return ord(self.controlRead(
            0b10100001,
            2,
            report_id,
            interface,
            1,
            timeout,
        ))

    def getHIDProtocol(self, interface, timeout=5):
        return ord(self.controlRead(
            0b10100001,
            3,
            0,
            interface,
            1,
            timeout,
        ))

    def setHIDReport(self, report_type, report_id, interface, data, timeout=5):
        self.controlWrite(
            0b00100001,
            9,
            (report_type << 8) | report_id,
            interface,
            data,
            timeout,
        )

    def setHIDIdle(self, report_id, interface, duration, timeout=5):
        self.controlWrite(
            0b00100001,
            10,
            (duration << 8) | report_id,
            interface,
            '',
            timeout,
        )

    def setHIDProtocol(self, interface, protocol, timeout=5):
        self.controlWrite(
            0b00100001,
            11,
            protocol,
            interface,
            '',
            timeout,
        )

    def onUSBWakeupRequest(self):
        self.usb_is_wakeup_requested = True

    def onUSBEnableChange(self, is_enabled):
        self.usb_is_enabled = is_enabled

    def onUSBEPEnableChange(self, endpoint, is_enabled):
        self.usb_is_endpoint_enabled[endpoint] = is_enabled

    def getSavedFnLock(self):
        return bool(self.cpu.flash[0x2800] & 0x0002)

    def getSavedMouseSpeed(self):
        return (self.cpu.flash[0x2800] >> 8) & 0x0f

    def setSavedFnLock(self, is_locked):
        if is_locked:
            self.cpu.flash[0x2800] |= 0x0002
        else:
            self.cpu.flash[0x2800] &= 0xfffd

    def setSavedMouseSpeed(self, speed):
        self.cpu.flash[0x2800] = (
            self.cpu.flash[0x2800] & 0xf0ff
        ) | (
            (speed & 0xf) << 8
        )

def main():
    parser = argparse.ArgumentParser(
        description='KU1255 simulator',
    )
    parser.add_argument('firmware', help='Binary firmware image')
    # XXX: add save-state support ?
    args = parser.parse_args()
    with open(args.firmware, 'rb') as firmware:
        device = KU1255(firmware)

    def sleep(duration):
        deadline = device.cpu.run_time + duration
        while device.cpu.run_time < deadline:
            device.step()

    # Must have enabled usb subsystem by 200ms (arbitrary)
    while not device.usb_is_enabled and device.cpu.run_time < 200:
        device.step()
    if not device.usb_is_enabled:
        raise Timeout('Not on USB bus')
    # Reset
    device.cpu.usb.reset = True
    sleep(10) # Reset lasts 10ms
    device.cpu.usb.reset = False
    sleep(100) # Wait some more
    # Based on linux enumeration sequence
    device_descriptor = device.getDescriptor(1, 8)
    sleep(1)
    print('pre-address device desc:', hexdump(device_descriptor))
    device.setAddress(1)
    sleep(1)
    print('address set')
    total_length = device_descriptor[0]
    device_descriptor = device.getDescriptor(1, total_length)
    sleep(1)
    print('full device desc:', hexdump(device_descriptor))
    for _ in range(3):
        try:
            device_qualifier = device.getDescriptor(6, 0x0a)
        except EndpointStall:
            continue
        else:
            print('device qualifier:', hexdump(device_qualifier))
            break
        finally:
            sleep(1)
    config_descriptor_head = device.getDescriptor(2, 9)
    sleep(1)
    print('config desc head:', hexdump(config_descriptor_head))
    total_length, = unpack('<H', config_descriptor_head[2:4])
    print('len', total_length)
    config_descriptor = device.getDescriptor(2, total_length)
    sleep(1)
    print('config desc:', hexdump(config_descriptor))
    first_supported_language, = unpack('<H', device.getDescriptor(3, 255)[2:4])
    sleep(1)
    print('string desc 2:', device.getDescriptor(3, 255, 2, language=first_supported_language, timeout=10)[2:].decode('utf-16'))
    sleep(1)
    print('string desc 1:', device.getDescriptor(3, 255, 1, language=first_supported_language)[2:].decode('utf-16'))
    sleep(1)
    device.setConfiguration(1)
    sleep(1)
    device.setHIDIdle(0, 0, 0)
    sleep(1)
    hid_descriptor_ep1 = device.getDescriptor(0x22, 0x51, language=0) # XXX: should parse config_descriptor
    sleep(1)
    print('HID descr interface 0:', hexdump(hid_descriptor_ep1))
    device.setHIDReport(2, 0, 0, b'\x00')
    sleep(1)
    report_0_length = (
        1 * 8 + # modifier keys
        1 * 8 + # padding
        5 * 1 + # leds
        3 * 1 + # padding
        6 * 8 + # keys ?
        8 * 8   # ?
    ) / 8 # XXX: should HID config_descriptor
    assert int(report_0_length) == report_0_length, report_0_length
    report_0_length = int(report_0_length)
    try:
        device.readEP(1, report_0_length, 63)
    except EndpointNAK:
        pass
    sleep(1)
    device.setHIDIdle(0, 1, 0)
    sleep(1)
    hid_descriptor_ep2 = device.getDescriptor(0x22, 0xd3, language=1, timeout=15) # XXX: should parse config_descriptor
    sleep(1)
    print('HID descr interface 1:', hexdump(hid_descriptor_ep2))
    report_1_length = (
        1 * 5 + # buttons
        3 * 1 + # padding
        2 * 8 + # axes
        1 * 8 + # wheel
        1 * 8   # hwheel
    ) / 8 # XXX: should HID config_descriptor
    assert int(report_1_length) == report_1_length, report_1_length
    report_1_length = int(report_1_length)
    try:
        device.readEP(2, report_1_length, 63)
    except EndpointNAK:
        pass
    sleep(1)
    device.setHIDReport(3, 0x13, 1, b'\x13\x01\x03')
    sleep(1)
    device.setHIDReport(3, 0x13, 1, b'\x13\x05\x01')
    sleep(1)
    device.setHIDReport(3, 0x13, 1, b'\x13\x02\x05')
    sleep(1)

    # Exercising other standard requests
    print('active configuration:', device.getConfiguration())
    sleep(1)
    print('interface 0 active alt setting:', device.getInterface(0))
    sleep(1)
    print('interface 1 active alt setting:', device.getInterface(1))
    sleep(1)
    print('HID protocol interface 0:', device.getHIDProtocol(0))
    sleep(1)
    print('HID idle interface 0 report 0:', device.getHIDIdle(0, 0) * 4, '(ms, 0=when needed)')
    sleep(1)
    print('HID protocol interface 1:', device.getHIDProtocol(1))
    sleep(1)
    print('HID idle interface 1 report 1:', device.getHIDIdle(1, 1) * 4, '(ms, 0=when needed)')
    sleep(1)

    print('saved fnLock state:', device.getSavedFnLock())
    print('saved mouse speed:', device.getSavedMouseSpeed())
    deadline = device.cpu.run_time + 200
    while device.mouse_initialisation_state != MOUSE_INITIALISED and device.cpu.run_time < deadline:
        device.step()
    if device.mouse_initialisation_state != MOUSE_INITIALISED:
        raise Timeout('Mouse not initialised')
    device.setMouseState(1, -1, True, False, False)
    report_ep2 = device.readEP(2, report_1_length, 63) # XXX: should parse config_descriptor
    sleep(1)
    print('report    interface 1:', hexdump(report_ep2))
    try:
        device.readEP(2, report_1_length, 63)
    except EndpointNAK:
        pass
    else:
        raise AssertionError('EP2 is not NAKing ?')
    sleep(1)
    device.setMouseState(0, 0, False, False, False)
    report_ep2 = device.readEP(2, report_1_length, 63) # XXX: should parse config_descriptor
    sleep(1)
    print('report    interface 1:', hexdump(report_ep2))
    try:
        device.readEP(2, report_1_length, 63)
    except EndpointNAK:
        pass
    else:
        raise AssertionError('EP2 is not NAKing ?')
    sleep(1)

    EMPTY_KEY_REPORT = b'\x00' * 8
    MODIFIER_KEY_DICT = {
        0x01: 'LCTRL',
        0x02: 'LSHIFT',
        0x04: 'LALT',
        0x08: 'LGUI',
        0x10: 'RCTRL',
        0x20: 'RSHIFT',
        0x40: 'RALT',
        0x80: 'RGUI',
    }
    KEY_DICT = {
        0x01: '(rollover)',
        0x04: 'A',
        0x05: 'B',
        0x06: 'C',
        0x07: 'D',
        0x08: 'E',
        0x09: 'F',
        0x0a: 'G',
        0x0b: 'H',
        0x0c: 'I',
        0x0d: 'J',
        0x0e: 'K',
        0x0f: 'L',
        0x10: 'M',
        0x11: 'N',
        0x12: 'O',
        0x13: 'P',
        0x14: 'Q',
        0x15: 'R',
        0x16: 'S',
        0x17: 'T',
        0x18: 'U',
        0x19: 'V',
        0x1a: 'W',
        0x1b: 'X',
        0x1c: 'Y',
        0x1d: 'Z',
        0x1e: '1',
        0x1f: '2',
        0x20: '3',
        0x21: '4',
        0x22: '5',
        0x23: '6',
        0x24: '7',
        0x25: '8',
        0x26: '9',
        0x27: '0',
        0x28: 'RETURN',
        0x29: 'ESCAPE',
        0x2a: 'BACKSPACE',
        0x2b: 'TAB',
        0x2c: 'SPACE',
        0x2d: 'MINUS',
        0x2e: 'EQUALS',
        0x2f: 'LEFTBRACKET',
        0x30: 'RIGHTBRACKET',
        0x31: 'BACKSLASH',
        0x32: 'NONUSHASH',
        0x33: 'SEMICOLON',
        0x34: 'APOSTROPHE',
        0x35: 'GRAVE',
        0x36: 'COMMA',
        0x37: 'PERIOD',
        0x38: 'SLASH',
        0x39: 'CAPSLOCK',
        0x3a: 'F1',
        0x3b: 'F2',
        0x3c: 'F3',
        0x3d: 'F4',
        0x3e: 'F5',
        0x3f: 'F6',
        0x40: 'F7',
        0x41: 'F8',
        0x42: 'F9',
        0x43: 'F10',
        0x44: 'F11',
        0x45: 'F12',
        0x46: 'PRINTSCREEN',
        0x48: 'PAUSE',
        0x49: 'INSERT',
        0x4a: 'HOME',
        0x4b: 'PAGEUP',
        0x4c: 'DELETE',
        0x4d: 'END',
        0x4e: 'PAGEDOWN',
        0x4f: 'RIGHT',
        0x50: 'LEFT',
        0x51: 'DOWN',
        0x52: 'UP',
        0x64: 'NONUSBACKSLASH',
        0x87: 'INTERNATIONAL1',
        0x88: 'INTERNATIONAL2',
        0x89: 'INTERNATIONAL3',
        0x8a: 'INTERNATIONAL4',
        0x8b: 'INTERNATIONAL5',
        0xd0: 'KP_MEMSTORE',
        0xd2: 'KP_MEMCLEAR',
        0xd4: 'KP_MEMSUBTRACT',
    }

    for y in range(16):
        for x in range(8):
            device.pressKey(y, x)
            report = device.readEP(1, report_0_length, 63, timeout=500)
            sleep(1)
            assert report[1] == 0x00, hexdump(report)
            assert report[3:] == b'\x00' * 5, hexdump(report)
            if report[0]:
                assert not report[2]
                print('%14s' % MODIFIER_KEY_DICT[report[0]], end=' ')
            else:
                print('%14s' % KEY_DICT.get(report[2], '(none)'), end=' ')
            device.releaseKey(y, x)
            report = device.readEP(1, report_0_length, 63, timeout=500)
            sleep(1)
            assert report == EMPTY_KEY_REPORT, hexdump(report)
        print()
    device.pressKey(13, 1) # LCTRL
    device.readEP(1, report_0_length, 63, timeout=500)
    sleep(1)
    device.pressKey(4, 5) # C
    report = device.readEP(1, report_0_length, 63, timeout=500)
    sleep(1)
    print(hexdump(report), MODIFIER_KEY_DICT.get(report[0], '(nothing)'), '+', KEY_DICT.get(report[2], '(nothing)'))
    return
    device.trace = True
    while True:
        import pdb; pdb.set_trace()
        device.step()

if __name__ == '__main__':
    main()
