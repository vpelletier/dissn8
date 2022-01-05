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

"""
Peripheral simulation.
"""
from struct import unpack

I2C_IDLE = 0
I2C_ADDRESS = 1
I2C_DATA = 2
I2C_IGNORE = 3
class BitBanging8bitsI2C:
    def __init__(
        self,
        address,
        speed,
        onAddressed=lambda x: False,
        onStop=lambda: None,
        onDataByteReceived=lambda _: False,
        getNextDataByte=lambda: None,
    ):
        """
        address (int)
            7-bits I2C address.
        speed (int)
            Device speed in kbps.
            Standard values are 100kbps, 400kbps, 1Mbps, 1.7Mbps, 3.4Mbps,
            5Mbps.
            SCL changes less than 1 / speed apart are ignored.
        onAddressed (callable(read))
            Called when bus address matches the value above.
            Argument tells whether host requests a read (True) or a write
            (False).
            Returns whether the address should be ACKed (true) or NAKed
            (false).
        onStop (callable)
            Called when a stop condition is detected.
            Only called if this device was addressed.
        onDataByteReceived (callable(int) -> bool)
            Called when 8 data bits (not address) have been received.
            Received byte is given as parameter.
            Returns whether the byte should be ACKed (true) or NAKed (false).
        getNextDataByte (callable -> byte or None)
            Called when starting to send 8 data bits to the bus.
            Returns the byte to send, or None if there is nothing more to send.
        """
        assert address.bit_length() < 8
        address <<= 1
        self._read_address = address | 1
        self._write_address = address
        self._min_change_period = .5 / speed
        self.onAddressed = onAddressed
        self.onStop = onStop
        self.onDataByteReceived = onDataByteReceived
        self.getNextDataByte = getNextDataByte
        self.reset()

    def reset(self):
        self._next_scl_time = 0
        self._previous_scl = True
        self._previous_sda = True
        self.scl_float = True
        self.sda_float = True
        self._state = I2C_IDLE
        self._current_byte = 0x00
        self._bit_count = 0
        self._sending_next = False
        self._sending = False
        self._addressed = False

    def step(self, time, scl, sda):
        scl = bool(scl)
        sda = bool(sda)
        scl_changed = self._previous_scl != scl
        sda_changed = self._previous_sda != sda
        if scl_changed:
            if sda_changed:
                raise ValueError('SCL and SDA changed during same step')
            self._scl_edge_time = time
            self._scl_edge = scl
            if time >= self._next_scl_time:
                self._next_scl_time = time + self._min_change_period
                self.onClockEdge(time=time, scl=scl, sda=sda)
                self._previous_scl = scl
        elif sda_changed:
            self.onDataEdge(time=time, scl=scl, sda=sda)
            self._previous_sda = sda

    def _shiftCurrentByteToSDA(self):
        self.sda_float = bool(self._current_byte & 0x80)
        self._current_byte = (self._current_byte << 1) & 0xff

    def _onByteReceived(self, time):
        if self._state == I2C_ADDRESS:
            if self._current_byte == self._read_address:
                self._sending_next = True
                self._addressed = True
                ack = self.onAddressed(True)
            elif self._current_byte == self._write_address:
                self._addressed = True
                ack = self.onAddressed(False)
            else:
                ack = False
            if ack:
                self.sda_float = False # ACK
                # CPU may decide to clock as soon as it sees our ACK, so allow
                # an immediate edge.
                self._next_scl_time = time
                self._state = I2C_DATA
            else:
                self.sda_float = True
                self._state = I2C_IGNORE
        elif self._state == I2C_DATA:
            if self.onDataByteReceived(self._current_byte):
                self.sda_float = False # ACK
                # CPU may decide to clock as soon as it sees our ACK, so allow
                # an immediate edge.
                self._next_scl_time = time
            else:
                self.sda_float = True # NAK
                self._state = I2C_IGNORE
        else:
            raise ValueError('_onByteReceived called in state %r' % self._state)

    def onClockEdge(self, time, scl, sda):
        if self._state == I2C_IGNORE:
            return
        if scl:
            # Rising clock edge
            if self._bit_count < 8:
                # Still during data bits
                if not self._sending:
                    # Receiving: Sample data bit
                    self._current_byte = (
                        (self._current_byte << 1) | sda
                    ) & 0xff
                # Nothing to do when sending.
            else:
                # Handshake bit
                assert self._bit_count == 8, self._bit_count
                if self._sending and sda:
                    # Sending and CPU NAKed
                    self._state = I2C_IGNORE
                # Nothig to do when receiving
        else:
            # Falling clock edge
            if self._bit_count < 7:
                # Still during data bits
                self._bit_count += 1
                if self._sending:
                    # Sending: send data bit
                    self._shiftCurrentByteToSDA()
                # Nothing to do when receiving
            elif self._bit_count == 7:
                # Handshake bit
                self._bit_count += 1
                if self._sending:
                    # Release SDA so CPU may NAK
                    self.sda_float = True
                else:
                    self._onByteReceived(time=time)
                    self._current_byte = 0x00
            else:
                assert self._bit_count == 8, self._bit_count
                # Handshake bit time finished
                self._bit_count = 0
                self._sending = self._sending_next
                if self._sending:
                    # Prepare first bit of next byte
                    next_byte = self.getNextDataByte()
                    if next_byte is None:
                        # Master reads more than is available.
                        self.sda_float = True
                        self._state = I2C_IGNORE
                    else:
                        self._current_byte = next_byte
                        self._shiftCurrentByteToSDA()
                else:
                    if not self.sda_float:
                        # CPU may decide to clock as soon as it sees we stopped
                        # ACKing, so allow an immediate edge.
                        self._next_scl_time = time
                    self.sda_float = True

    def onDataEdge(self, time, scl, sda):
        _ = time
        if scl:
            if sda:
                if self._addressed:
                    self.onStop()
                self._state = I2C_IDLE
            else:
                self._state = I2C_ADDRESS
            self._addressed = False
            self._bit_count = -1
            self._sending = False
            self._sending_next = False

class USBDevice:
    """
    Implement USB protocol-level functionalities.
    """
    def __init__(
        self,
        cpu,
        step=None,
        on_wake_signaling=None,
        on_enable_change=None,
        on_ep_enable_change=None,
    ):
        """
        cpu
          The CPU whose USB subsystem is to interface with.
        step
          A callable to cause CPU to advance by one simulation step while
          waiting for a USB transaction compleation. Defaults to cpu.step when
          None.
        """
        usb = cpu.usb
        if on_wake_signaling is not None:
            usb.on_wake_signaling = on_wake_signaling
        if on_enable_change is not None:
            usb.on_enable_change = on_enable_change
        if on_ep_enable_change is not None:
            usb.on_ep_enable_change = on_ep_enable_change
        self._cpu = cpu
        self._step = cpu.step if step is None else step

    def _waitForEP0EventsHandled(self, deadline):
        cpu = self._cpu
        step = self._step
        while (cpu.FEP0SETUP or cpu.FEP0IN or cpu.FEP0OUT) and cpu.run_time < deadline:
            step()
        if cpu.run_time >= deadline:
            raise ValueError('Timeout reached')

    def _waitForAckOrStall(self, endpoint, deadline):
        cpu = self._cpu
        step = self._step
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

    def _sleep(self, duration):
        cpu = self._cpu
        step = self._step
        deadline = cpu.run_time + duration
        while cpu.run_time < deadline:
            step()

    def reset(self):
        """
        Signal an USB reset condition.
        """
        self._cpu.usb.FBUS_RST = True
        self._sleep(10) # Reset lasts 10ms
        self._cpu.usb.FBUS_RST = False

    def suspend(self):
        """
        Signal an USB bus suspension condition

        No-op if the bus is already suspended.
        """
        cpu = self._cpu
        cpu.FSUSPEND = True
        cpu.usb.next_sof_time = float('inf')

    def resume(self):
        """
        Signal an USB bus resume condition

        No-op if bus is already active.
        """
        cpu = self._cpu
        cpu.FSUSPEND = False
        cpu.wake()
        usb = cpu.usb
        usb.next_sof_time = min(usb.next_sof_time, cpu.run_time + 1)

    def controlRead(self, request_type, request, value, index, length, timeout=5):
        cpu = self._cpu
        deadline = cpu.run_time + timeout
        self._waitForEP0EventsHandled(deadline)
        request_type |= 0x80
        cpu.usb.sendSETUP(request_type, request, value, index, length)
        self._waitForEP0EventsHandled(deadline)
        # Hardcoded max packet size, as it is fixed by spu for endpoint 0
        result = self._readEP(0, length, 8, deadline)
        self._writeEP(0, b'', 8, deadline)
        return result

    def controlWrite(self, request_type, request, value, index, data, timeout=5):
        cpu = self._cpu
        deadline = cpu.run_time + timeout
        self._waitForEP0EventsHandled(deadline)
        request_type &= 0x7f
        cpu.usb.sendSETUP(request_type, request, value, index, len(data))
        self._waitForEP0EventsHandled(deadline)
        # Hardcoded max packet size, as it is fixed by cpu for endpoint 0
        if data:
            self._writeEP(0, data, 8, deadline)
        self._readEP(0, 0, 8, deadline)

    def readEP(self, endpoint, length, max_packet_size, timeout=5):
        return self._readEP(endpoint, length, max_packet_size, self._cpu.run_time + timeout)

    def writeEP(self, endpoint, data, max_packet_size, timeout=5):
        self._writeEP(endpoint, data, max_packet_size, self._cpu.run_time + timeout)

    def _readEP(self, endpoint, length, max_packet_size, deadline):
        recv = self._cpu.usb.recv
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
        send = self._cpu.usb.send
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
            b'',
            timeout,
        )

    def setFeature(self, recipient, feature, index=0, timeout=5):
        self.controlWrite(
            recipient,
            3,
            feature,
            index,
            b'',
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
            b'',
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
            b'',
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
            b'',
            timeout,
        )
