#!/usr/bin/env python
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
from __future__ import division
import argparse
from collections import defaultdict
from functools import partial
from struct import unpack
import warnings
from simsn8 import SN8F2288, INF

try:
    _ = ord(b'\x00'[0])
except TypeError:
    # Python 3
    byte_ord = lambda x: x
else:
    byte_ord = ord

def hexdump(value):
    return ' '.join('%02x' % byte_ord(x) for x in value)

I2C_IDLE = 0
I2C_ADDRESS = 1
I2C_DATA = 2
I2C_IGNORE = 3
MOUSE_IDLE = 0
MOUSE_INIT1 = 1
MOUSE_INITIALISED = 2
USB_RECIPIENT_DEVICE = 0x00
USB_RECIPIENT_INTERFACE = 0x01
USB_RECIPIENT_ENDPOINT = 0x02

class Timeout(Exception):
    pass

class KU1255(object):
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
        for column in xrange(8):
            cpu.p1.setLoad(column, partial(self.getKeyLoad, column))
        # USB host emulation
        usb = cpu.usb
        usb.on_wake_signaling = self.onUSBWakeupRequest
        usb.on_enable_change = self.onUSBEnableChange
        usb.on_ep_enable_change = self.onUSBEPEnableChange
        usb.on_ep_event_available = self.onUSBEPEventAvailable
        usb.on_setup_read = self.onUSBSETUPRead
        # Bit-banging I2C device emulation (mouse)
        cpu.p2.setLoad(4, self.getI2CSCLLoad)
        cpu.p2.setLoad(5, self.getI2CSDALoad)
        cpu.p2.setLoad(6, self.getMouseATTNLoad)
        self.scl_pull_up = self.sda_pull_up = self.mouse_attn_pull_up = 3300 # 3.3k Ohms
        self.i2c_read_address = 0x55
        self.i2c_write_address = 0x54
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
        print repr(self.cpu)

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
            sefl.__class__.__name__,
            id(self),
            (
                (
                    'suspended' if self.cpu.usb.suspend else 'on'
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
        self.cpu.reset()
        self._reset()

    def _reset(self):
        # Key matrix
        self.row_list_by_column = [
            []
            for x in xrange(8)
        ]
        self.column_list_by_row = [
            []
            for x in xrange(len(self.matrix))
        ]
        # USB
        self.usb_is_setup_read = False
        self.usb_is_enabled = False
        self.usb_ep_has_event = [False] * 5
        self.usb_is_endpoint_enabled = [True, False, False, False, False]
        self.usb_is_wakeup_requested = False
        # I2C
        self.scl_float = True
        self.sda_float = True
        self.mouse_attn_float = True
        self.mouse_initialisation_state = MOUSE_IDLE
        self.i2c_state = I2C_IDLE
        self.i2c_buffer = [
            0x80, # Value known, meaning unknown
            0x00, # Buttons
            0x00, # x, two's complement
            0x00, # y, two's complement
            0x00, # Value unknown, meaning unknown
        ]
        self.i2c_buffer_index = 0
        self.i2c_in_buffer = []
        self.i2c_previous_sda = 1
        self.i2c_previous_scl = 1
        self.i2c_current_byte = 0x00
        self.i2c_bit_count = 0
        self.i2c_sending_next = False
        self.i2c_sending = False

    def step(self):
        cpu = self.cpu
        if self._trace:
            self._tracer()
        cpu.step()
        # Assume CPU agrees with device on bus state
        p2 = cpu.p2.read()
        scl = (p2 >> 4) & 1
        sda = (p2 >> 5) & 1
        scl_changed = self.i2c_previous_scl != scl
        sda_changed = self.i2c_previous_sda != sda
        if scl_changed and sda_changed:
            raise ValueError('SCL and SDA changed during same step')
        if scl_changed:
            self.i2c_previous_scl = scl
            if self.i2c_state != I2C_IGNORE:
                if scl:
                    #print '%10.3fms SCL rising  edge sda=%i bit_count=%i byte=%#04x' % (self.cpu.run_time, sda, self.i2c_bit_count, self.i2c_current_byte)
                    # Rising clock edge
                    if self.i2c_bit_count < 8:
                        if not self.i2c_sending:
                            # Data bit
                            self.i2c_current_byte = ((self.i2c_current_byte << 1) | sda) & 0xff
                    else:
                        assert self.i2c_bit_count == 8, self.i2c_bit_count
                        # ack/nack bit
                        if self.i2c_sending:
                            if sda:
                                print 'CPU NACK'
                                self.i2c_state = I2C_IGNORE
                            else:
                                print 'CPU ACK'
                else:
                    #print '%10.3fms SCL falling edge sda=%i bit_count=%i byte=%#04x' % (self.cpu.run_time, sda, self.i2c_bit_count, self.i2c_current_byte)
                    # Falling clock edge
                    if self.i2c_bit_count < 7:
                        if self.i2c_sending:
                            self.sda_float = bool(self.i2c_current_byte & 0x80)
                            #print 'Sending bit %i, sda_float=%i' % (self.i2c_bit_count, self.sda_float)
                            self.i2c_current_byte = (self.i2c_current_byte << 1) & 0xff
                        self.i2c_bit_count += 1
                    elif self.i2c_bit_count == 7:
                        if self.i2c_sending:
                            # Release SDA so CPU may NAK
                            self.sda_float = True
                        else:
                            self.i2c_onByteReceived()
                            self.i2c_current_byte = 0x00
                        self.i2c_bit_count += 1
                    else:
                        assert self.i2c_bit_count == 8, self.i2c_bit_count
                        # Ack bit time finished
                        self.i2c_bit_count = 0
                        self.i2c_sending = self.i2c_sending_next
                        if self.i2c_sending:
                            # Prepare first bit of next byte
                            try:
                                self.i2c_current_byte = self.i2c_buffer[self.i2c_buffer_index]
                            except IndexError:
                                # Master reads more than is available.
                                self.i2c_state = I2C_IGNORE
                            else:
                                self.i2c_buffer_index += 1
                                print 'Sending %#04x' % self.i2c_current_byte
                                self.sda_float = bool(self.i2c_current_byte & 0x80)
                                #print 'Sending bit %i, sda_float=%i' % (self.i2c_bit_count, self.sda_float)
                                self.i2c_current_byte = (self.i2c_current_byte << 1) & 0xff
                        else:
                            if not self.sda_float:
                                #print 'Releasing SDA'
                                self.sda_float = True
        if sda_changed:
            self.i2c_previous_sda = sda
            if scl:
                if sda:
                    print '%10.3fms stop condition' % (self.cpu.run_time, )
                    self.i2c_state = I2C_IDLE
                else:
                    print '%10.3fms start condition' % (self.cpu.run_time, )
                    self.i2c_state = I2C_ADDRESS
                # Stop/start/restart condition
                self.i2c_bit_count = -1
                self.i2c_sending = False
                self.i2c_sending_next = False

    def i2c_onByteReceived(self):
        if self.i2c_state == I2C_ADDRESS:
            if self.i2c_current_byte == self.i2c_read_address:
                print 'Received read address, asserting SDA'
                self.sda_float = False # ACK
                self.i2c_sending_next = True
                self.i2c_buffer_index = 0
                self.i2c_state = I2C_DATA
            elif self.i2c_current_byte == self.i2c_write_address:
                print 'Received write address, asserting SDA'
                self.sda_float = False # ACK
                self.i2c_in_buffer = []
                self.i2c_state = I2C_DATA
            else:
                #print 'Received another address, ignoring until next stop condition'
                self.i2c_state = I2C_IGNORE
        elif self.i2c_state == I2C_DATA:
            print 'Received data byte %#04x' % self.i2c_current_byte
            self.i2c_in_buffer.append(self.i2c_current_byte)
            if self.i2c_in_buffer == [0xfc]:
                self.mouse_initialisation_state = MOUSE_INIT1
                #print 'Received mouse init 0 byte, asserting SDA'
                self.sda_float = False # ACK
            elif self.i2c_in_buffer == [0xc4]:
                self.mouse_initialisation_state = MOUSE_INITIALISED
                #print 'Received mouse init 1 byte, asserting SDA'
                self.sda_float = False # ACK
            else:
                warnings.warn(
                    'Mouse received unknown byte sequence received from '
                    'cpu: %s' % ','.join(
                        '%#03x' % x for x in self.i2c_in_buffer
                    ),
                )
                # Left SDA float: NAK
        else:
            raise ValueError('i2c_onByteReceived should not be called in state %r' % self.i2c_state)

    def getKeyLoad(self, column):
        impedance_by_voltage = defaultdict(list)
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
            for column in column_set:
                row_set.update(self.row_list_by_column[column])
            for row in row_set:
                column_set.update(self.column_list_by_row[row])
        for getInternalAsLoad in self.row_list_by_column[column]:
            voltage, impedance = getInternalAsLoad()
            if impedance != INF:
                impedance_by_voltage[voltage].append(impedance)
        for key, value in impedance_by_voltage.items():
            impedance_by_voltage[key] = sum(value) / len(value)
        source_count = len(impedance_by_voltage)
        if source_count == 0:
            voltage = 0
            impedance = INF
        elif source_count == 1:
            (voltage, impedance), = impedance_by_voltage.iteritems()
        elif source_count == 2:
            (v0, imp0), (v1, imp1) = impedance_by_voltage.iteritems()
            if v0 > v1:
                voltage = (v0 - v1) * imp1 / (imp0 + imp1)
            else:
                voltage = (v1 - v0) * imp0 / (imp0 + imp1)
            impedance = (imp0 + imp1) / 2
        else:
            ValueError('I have not be taught multi-source voltage divider')
        return voltage, impedance

    def getI2CSCLLoad(self):
        if self.scl_float:
            return self.cpu.p1.vdd, self.scl_pull_up
        return 0, 0 # Assume perfect short circuit to ground

    def getI2CSDALoad(self):
        if self.sda_float:
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
        self.row_list_by_column[column].add(self.matrix[row])
        self.column_list_by_row[row].append(column)

    def releaseKey(self, row, column):
        assert row >= 0
        assert column >= 0
        self.row_list_by_column[column].discard(self.matrix[row])
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

    def controlRead(self, request_type, request, value, index, length, timeout):
        cpu = self.cpu
        usb = cpu.usb
        deadline = cpu.run_time + timeout
        step = self.step
        self.usb_is_setup_read = False
        waitRETI(self)
        usb.sendSETUP(request_type, request, value, index, length)
        while not self.usb_is_setup_read and cpu.run_time < deadline:
            step()
        if not self.usb_is_setup_read:
            raise Timeout('SETUP not read by cpu')
        # Hardcoded max packet size, as it is fixed by spu for endpoint 0
        return self._readEP(0, length, 8, deadline)

    def controlWrite(self, request_type, request, value, index, data, timeout):
        cpu = self.cpu
        usb = cpu.usb
        deadline = cpu.run_time + timeout
        step = self.step
        self.usb_is_setup_read = False
        waitRETI(self)
        usb.sendSETUP(request_type, request, value, index, len(data))
        while not self.usb_is_setup_read and cpu.run_time < deadline:
            step()
        if not self.usb_is_setup_read:
            raise Timeout('SETUP not read by cpu')
        # Hardcoded max packet size, as it is fixed by spu for endpoint 0
        self._writeEP(0, data, 8, deadline)

    def readEP(self, endpoint, length, max_packet_size, timeout=5):
        return self._readEP(endpoint, length, max_packet_size, self.cpu.run_time + timeout)

    def writeEP(self, endpoint, data, max_packet_size, timeout=5):
        self._writeEP(endpoint, data, max_packet_size, self.cpu.run_time + timeout)

    def _readEP(self, endpoint, length, max_packet_size, deadline):
        cpu = self.cpu
        usb = cpu.usb
        step = self.step
        result = b''
        while True:
            self.usb_ep_has_event[endpoint] = False
            while not self.usb_ep_has_event[endpoint] and cpu.run_time < deadline:
                step()
            if not self.usb_ep_has_event[endpoint]:
                raise Timeout('IN still NAKed by cpu')
            waitRETI(self)
            chunk = usb.recv(endpoint)
            result += chunk
            if len(result) == length or len(chunk) < max_packet_size:
                break
        return result

    def _writeEP(self, endpoint, data, max_packet_size, deadline):
        cpu = self.cpu
        usb = cpu.usb
        step = self.step
        while data:
            self.usb_ep_has_event[endpoint] = False
            waitRETI(self)
            usb.send(endpoint, data[:max_packet_size])
            while not self.usb_ep_has_event[endpoint] and cpu.run_time < deadline:
                step()
            if not self.usb_ep_has_event[endpoint]:
                raise Timeout('OUT still NAKed by cpu')
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
        return byte_ord(self.controlRead(
            0x80 | recipient,
            0,
            0,
            index,
            2,
            timeout,
        ))

    def getConfiguration(self, timeout=5):
        return byte_ord(self.controlRead(
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
        return byte_ord(self.controlRead(
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

    def onUSBEPEventAvailable(self, endpoint):
        cpu = self.cpu
        if [
            cpu.FUE0M1,
            cpu.FUE1M1,
            cpu.FUE2M1,
            cpu.FUE3M1,
            cpu.FUE4M1,
        ][endpoint]:
            raise EndpointStall
        self.usb_ep_has_event[endpoint] = True

    def onUSBSETUPRead(self):
        self.usb_is_setup_read = True

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

def waitRETI(device):
    # XXX: this is a hack: either CPU should queue interrupts while they are
    # disabled and trigger then on interrupt re-enable (from spec it does not
    # seem to be the case), or firmware should re-check interrupt sources
    # before exiting interrupt handler (but potentially risking watchdog
    # overflow if, as per specs, it is only cleared in main loop and not in
    # interrupt handler).
    # Using this function makes assumption that firmware uses interrupts for
    # specific actions, which may not be always the case.
    deadline = device.cpu.run_time + 5
    while not device.cpu.FGIE and device.cpu.run_time < deadline:
        device.step()
    if not device.cpu.FGIE:
        raise RuntimeError('Still in interrupt handler ?')

def main():
    parser = argparse.ArgumentParser(
        description='KU1255 simulator',
    )
    parser.add_argument('firmware', help='Binary firmware image')
    # XXX: add save-state support ?
    args = parser.parse_args()
    with open(args.firmware) as firmware:
        device = KU1255(firmware)
    def watchRead(cpu, address):
        print '%#05x read    %r' % (address, cpu)
    def watchWrite(cpu, address, value):
        print '%#05x = %#04x %r' % (address, value, cpu)
    #device.cpu.onRead(, watchRead)
    #device.cpu.onWrite(, watchWrite)
    #while device.cpu.run_time < 150.1:
    #while device.cpu.cycle_count < 542340:
    #while device.cpu.pc != 0x09c3:
    #    device.step()

    # Must have enabled usb subsystem by 200ms (arbitrary)
    while not device.usb_is_enabled and device.cpu.run_time < 200:
        device.step()
    if not device.usb_is_enabled:
        raise Timeout('Not on USB bus')
    # Reset
    device.cpu.usb.reset = True
    deadline = device.cpu.run_time + 10 # Reset lasts 10ms
    while device.cpu.run_time < deadline:
        device.step()
    device.cpu.usb.reset = False
    device_descriptor_head = device.getDescriptor(1, 8)
    print 'pre-address device desc:', hexdump(device_descriptor_head)
    device.setAddress(1)
    device_descriptor_head = device.getDescriptor(1, 8)
    print 'post-address device desc:', hexdump(device_descriptor_head)
    total_length = byte_ord(device_descriptor_head[0])
    device_descriptor = device.getDescriptor(1, total_length)
    print 'full device desc:', hexdump(device_descriptor)
    config_descriptor_head = device.getDescriptor(2, 8)
    print 'config desc head:', hexdump(config_descriptor_head)
    total_length, = unpack('<H', config_descriptor_head[2:4])
    print 'len', total_length
    config_descriptor = device.getDescriptor(2, total_length)
    print 'config desc:', hexdump(config_descriptor)
    device.setConfiguration(1)
    print 'active configuration:', device.getConfiguration()
    print 'interface 0 active alt setting:', device.getInterface(0)
    print 'interface 1 active alt setting:', device.getInterface(1)
    print 'HID protocol interface 0:', device.getHIDProtocol(0)
    print 'HID idle interface 0 report 0:', device.getHIDIdle(0, 0) * 4, '(ms, 0=when needed)'
    print 'HID protocol interface 1:', device.getHIDProtocol(1)
    print 'HID idle interface 1 report 1:', device.getHIDIdle(1, 1) * 4, '(ms, 0=when needed)'
    hid_descriptor_ep1 = device.getDescriptor(0x22, 0x51, language=0) # XXX: should parse config_descriptor
    print 'HID descr interface 0:', hexdump(hid_descriptor_ep1)
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
    hid_descriptor_ep2 = device.getDescriptor(0x22, 0xd3, language=1) # XXX: should parse config_descriptor
    print 'HID descr interface 1:', hexdump(hid_descriptor_ep2)
    report_1_length = (
        1 * 5 + # buttons
        3 * 1 + # padding
        2 * 8 + # axes
        1 * 8 + # wheel
        1 * 8   # hwheel
    ) / 8 # XXX: should HID config_descriptor
    assert int(report_1_length) == report_1_length, report_1_length
    report_1_length = int(report_1_length)
    print 'saved fnLock state:', device.getSavedFnLock()
    print 'saved mouse speed:', device.getSavedMouseSpeed()
    deadline = device.cpu.run_time + 200
    while device.mouse_initialisation_state != MOUSE_INITIALISED and device.cpu.run_time < deadline:
        device.step()
    if device.mouse_initialisation_state != MOUSE_INITIALISED:
        raise Timeout('Mouse not initialised')
    return
    device.trace = True
    while True:
        import pdb; pdb.set_trace()
        device.step()

if __name__ == '__main__':
    main()
