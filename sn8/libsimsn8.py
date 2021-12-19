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
        onAddressed=lambda x: False,
        onStop=lambda: None,
        onDataByteReceived=lambda _: False,
        getNextDataByte=lambda: None,
    ):
        """
        address (int)
            7-bits I2C address.
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
        self.onAddressed = onAddressed
        self.onStop = onStop
        self.onDataByteReceived = onDataByteReceived
        self.getNextDataByte = getNextDataByte
        self.reset()

    def reset(self):
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

    def step(self, scl, sda):
        scl = bool(scl)
        sda = bool(sda)
        scl_changed = self._previous_scl != scl
        sda_changed = self._previous_sda != sda
        if scl_changed:
            if sda_changed:
                raise ValueError('SCL and SDA changed during same step')
            self.onClockEdge(scl, sda)
            self._previous_scl = scl
        elif sda_changed:
            self.onDataEdge(scl, sda)
            self._previous_sda = sda

    def _shiftCurrentByteToSDA(self):
        self.sda_float = bool(self._current_byte & 0x80)
        self._current_byte = (self._current_byte << 1) & 0xff

    def _onByteReceived(self):
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
                self._state = I2C_DATA
            else:
                self.sda_float = True
                self._state = I2C_IGNORE
        elif self._state == I2C_DATA:
            if self.onDataByteReceived(self._current_byte):
                self.sda_float = False # ACK
            else:
                self.sda_float = True # NAK
                self._state = I2C_IGNORE
        else:
            raise ValueError('_onByteReceived called in state %r' % self._state)

    def onClockEdge(self, scl, sda):
        if self._state == I2C_IGNORE:
            return
        if scl:
            # Rising clock edge
            if self._bit_count < 8:
                if not self._sending:
                    # Data bit
                    self._current_byte = (
                        (self._current_byte << 1) | sda
                    ) & 0xff
            else:
                assert self._bit_count == 8, self._bit_count
                if self._sending and sda:
                    # CPU NAKed
                    self._state = I2C_IGNORE
        else:
            # Falling clock edge
            if self._bit_count < 7:
                self._bit_count += 1
                if self._sending:
                    self._shiftCurrentByteToSDA()
            elif self._bit_count == 7:
                self._bit_count += 1
                if self._sending:
                    # Release SDA so CPU may NAK
                    self.sda_float = True
                else:
                    self._onByteReceived()
                    self._current_byte = 0x00
            else:
                assert self._bit_count == 8, self._bit_count
                # Ack bit time finished
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
                    self.sda_float = True

    def onDataEdge(self, scl, sda):
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
