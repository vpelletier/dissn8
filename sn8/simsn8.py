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

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from builtins import hex
from builtins import chr
from builtins import range
from past.utils import old_div
from builtins import object
from functools import partial
import os.path
from struct import unpack
import warnings
from .libsn8 import parseConfig
from .dissn8 import systematic

CONFIG_DIR = os.path.dirname(__file__)

try:
    _ = ord(b'\x00'[0])
except TypeError:
    # Python 3
    byte_ord = lambda x: x
else:
    byte_ord = ord

class CPUHalted(Exception):
    """
    Raised when trying to step CPU but it is currently halted.
    """
    pass

class EndpointStall(Exception):
    """
    Endpoint signals an error.
    """
    pass

class EndpointNAK(Exception):
    """
    Endpoint has no space for data (OUT) or no data available (IN).
    """
    pass

class ByteProperty(object):
    def __init__(self, address):
        self.address = address

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return instance.read(self.address)

    def __set__(self, instance, value):
        assert value == (value & 0xff), repr(value)
        instance.write(self.address, value)

class BitProperty(object):
    def __init__(self, address, bit):
        self.address = address
        self.bit = bit
        self._set_mask = 1 << bit
        self._clear_mask = (~self._set_mask) & 0xff

    def __get__(self, instance, owner):
        if instance is owner:
            return self
        return (instance.read(self.address) >> self.bit) & 1

    def __set__(self, instance, value):
        whole_value = instance.read(self.address)
        if value:
            whole_value |= self._set_mask
        else:
            whole_value &= self._clear_mask
        instance.write(self.address, whole_value)

RESET_SOURCE_WATCHDOG = 0x00
RESET_SOURCE_LOW_VOLTAGE = 0x80
RESET_SOURCE_PIN = 0xc0
MISS = object() # Missing register marker
VOLA = object() # Volatile register marker
REGISTERS_RESET_VALUE_LIST = (
    # 0     1     2     3     4     5     6     7     8     9     a     b     c     d     e     f
    MISS, MISS, None, None, None, MISS, 0x00, None, VOLA, VOLA, VOLA, VOLA, VOLA, VOLA, VOLA, VOLA, # 0x80
    VOLA, VOLA, VOLA, 0x00, 0x80, 0x00, 0x00, VOLA, VOLA, 0x00, VOLA, 0x00, VOLA, 0x00, VOLA, 0x00, # 0x90
    0x00, 0x00, 0x00, 0x00, MISS, VOLA, VOLA, VOLA, VOLA, 0x00, 0x00, 0xd5, 0x00, 0x00, VOLA, VOLA, # 0xa0
    0x00, 0x00, 0x00, MISS, MISS, VOLA, 0x00, VOLA, VOLA, VOLA, VOLA, 0x00, 0x00, 0x00, 0x00, 0x0a, # 0xb0
    0x00, VOLA, VOLA, MISS, VOLA, VOLA, 0x00, 0x00, 0x00, 0x00, 0x00, MISS, VOLA, MISS, 0x00, 0x00, # 0xc0
    VOLA, VOLA, VOLA, MISS, VOLA, VOLA, MISS, MISS, VOLA, VOLA, VOLA, VOLA, VOLA, MISS, MISS, 0x07, # 0xd0
    VOLA, VOLA, VOLA, MISS, VOLA, VOLA, MISS, VOLA, MISS, 0x00, VOLA, VOLA, VOLA, 0x00, 0x00, MISS, # 0xe0
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, # 0xf0
)

class MainSeriesPort(object):
    """
    I2C port, under another name.
    """
    def __init__(self, cpu, irq_name, ien_name):
        self.cpu = cpu
        self.irq_name = irq_name
        self.ien_name = ien_name
        self.reset()

    def __repr__(self):
        return '<%s@%08x status=%#04x>' % (
            self.__class__.__name__,
            id(self),
            self.status,
        )

    def getState(self):
        return {
            'status': self.status,
        }

    def setState(self, state):
        self.status = state['status']

    def reset(self):
        self.status = 0x00

    def readStatus(self):
        return self.status

    def writeStatus(self, value):
        self.status = (self.status & 0xbf) | (value & 0x40)

    def readMode1(self):
        raise NotImplementedError

    def writeMode1(self, value):
        raise NotImplementedError

    def readMode2(self):
        raise NotImplementedError

    def writeMode2(self, value):
        raise NotImplementedError

class USB(object):
    def __init__(self, cpu, irq_name, ien_name):
        self.cpu = cpu
        self.irq_name = irq_name
        self.ien_name = ien_name
        self.next_sof_time = 0
        self.on_wake_signaling = None
        self.on_enable_change = None
        self.on_ep_enable_change = None
        self.on_ep_event_available = None
        self.on_setup_read = None
        self.reset()

    def __repr__(self):
        return '<%s@%08x status=%#04x drive=%#04x address=%#04x>' % (
            self.__class__.__name__,
            id(self),
            self.status,
            self.drive,
            self.address,
        )

    def getState(self):
        return {
            'status': self.status,
            'device_se0_start_time': self.device_se0_start_time,
            'epbuf': self.epbuf[:],
            'drive': self.drive,
            'toggle': self.toggle,
            'address': self.address,
            'ep0enable': self.ep0enable,
            'ep1enable': self.ep1enable,
            'ep2enable': self.ep2enable,
            'ep3enable': self.ep3enable,
            'ep4enable': self.ep4enable,
        }

    def setState(self, state):
        self.status = state['status']
        self.device_se0_start_time = state['device_se0_start_time']
        self.epbuf[:] = state['epbuf']
        self.drive = state['drive']
        self.toggle = state['toggle']
        self.address = state['address']
        self.ep0enable = state['ep0enable']
        self.ep1enable = state['ep1enable']
        self.ep2enable = state['ep2enable']
        self.ep3enable = state['ep3enable']
        self.ep4enable = state['ep4enable']

    def reset(self):
        self.status = 0x00
        self.device_se0_start_time = None
        self.epbuf = [None] * 136
        self.drive = 0x00
        self.toggle = 0x07
        self.address = 0x00
        self.ep0enable = 0x00
        self.ep1enable = 0x00
        self.ep2enable = 0x00
        self.ep3enable = 0x00
        self.ep4enable = 0x00

    def readStatus(self):
        return self.status

    def writeStatus(self, value):
        if self.on_setup_read is not None and not value & 0x04:
            self.on_setup_read()
        self.status = (self.status & 0x18) | (value & 0xe7)

    def readAddress(self):
        return self.address

    def writeAddress(self, value):
        if (
            self.on_enable_change is not None and
            (self.address & 0x80) != (value & 0x80)
        ):
            self.on_enable_change(bool(value & 0x80))
        self.address = value

    def readFIFO(self):
        return self.epbuf[self.cpu.UDP0]

    def writeFIFO(self, value):
        self.epbuf[self.cpu.UDP0] = value

    def readPinControl(self):
        return self.drive

    def writePinControl(self, value):
        self.drive = value & 0x07
        if (
            self.pin_drive_enable and
            not self.drive_data_plus and
            not self.drive_data_minus
        ):
            # Driving a single-ended zero
            if self.device_se0_start_time is None:
                # ...which has just started
                self.device_se0_start_time = self.cpu.run_time
        else:
            # Not driving, or not a single-ended zero.
            self.device_se0_start_time = None

    def readEP0Enable(self):
        return self.ep0enable

    def readEP1Enable(self):
        return self.ep1enable

    def readEP2Enable(self):
        return self.ep2enable

    def readEP3Enable(self):
        return self.ep0enable

    def readEP4Enable(self):
        return self.ep4enable

    def _onWriteEPxEnable(self, endpoint, current_value, new_value):
        if (
            self.on_ep_enable_change is not None and
            endpoint != 0 and
            (new_value & 0x80) != (current_value & 0x80)
        ):
            self.on_ep_enable_change(endpoint, bool(new_value & 0x80))
        if (
            self.on_ep_event_available is not None and
            (new_value & 0x60) and
            (new_value & 0x60) != (current_value & 0x60)
        ):
            self.on_ep_event_available(
                endpoint,
                stall=bool(new_value & 0x40),
                ack=bool(new_value & 0x20),
            )

    def writeEP0Enable(self, value):
        self._onWriteEPxEnable(0, self.ep0enable, value)
        self.ep0enable = value

    def writeEP1Enable(self, value):
        self._onWriteEPxEnable(1, self.ep1enable, value)
        self.ep1enable = value

    def writeEP2Enable(self, value):
        self._onWriteEPxEnable(2, self.ep2enable, value)
        self.ep2enable = value

    def writeEP3Enable(self, value):
        self._onWriteEPxEnable(3, self.ep3enable, value)
        self.ep3enable = value

    def writeEP4Enable(self, value):
        self._onWriteEPxEnable(4, self.ep4enable, value)
        self.ep4enable = value

    def tic(self):
        cpu = self.cpu
        cpu_time = cpu.run_time
        if (
            self.device_se0_start_time is not None and
            self.on_wake_signaling is not None and
            cpu_time - self.device_se0_start_time > 1 # Wake signaling to host after 1ms
        ):
            self.on_wake_signaling()
        if (
            cpu.FSOF_INT_EN and
            cpu_time > self.next_sof_time # Full-speed SOFs are every 1ms
        ):
            cpu.FSOF = 1
            self.next_sof_time = cpu_time + 1
            self._interrupt()

    def _interrupt(self):
        cpu = self.cpu
        setattr(cpu, self.irq_name, 1)
        if getattr(cpu, self.ien_name):
            cpu.interrupt()

    def sendSETUP(self, request_type, request, value, index, length):
        cpu = self.cpu
        if not cpu.FUDE:
            raise RuntimeError('USB is disabled by firmware')
        if cpu.FEP0SETUP or cpu.FEP0IN or cpu.FEP0OUT:
            raise RuntimeError('Firmware has unhandled EP0 events')
        self.epbuf[0] = request_type
        self.epbuf[1] = request
        self.epbuf[2] = value & 0xff
        self.epbuf[3] = (value >> 8) & 0xff
        self.epbuf[4] = index & 0xff
        self.epbuf[5] = (index >> 8) & 0xff
        self.epbuf[6] = length & 0xff
        self.epbuf[7] = (length >> 8) & 0xff
        cpu.FUE0M0 = 0
        cpu.FUE0M1 = 0
        cpu.FEP0SETUP = 1
        self._interrupt()

    def _checkEndpoint(self, endpoint):
        cpu = self.cpu
        if not cpu.FUDE:
            raise RuntimeError('USB is disabled')
        if endpoint == 0:
            enabled = True
            stall               = cpu.FUE0M1
            ack                 = cpu.FUE0M0
            has_pending_events = cpu.FEP0SETUP or cpu.FEP0IN or cpu.FEP0OUT
            nak_int_en = False
        elif endpoint == 1:
            enabled             = cpu.FUE1EN
            stall               = cpu.FUE1M1
            ack                 = cpu.FUE1M0
            has_pending_events  = cpu.FEP1_ACK or cpu.FEP1_NAK
            nak_int_en          = cpu.FEP1NAK_INT_EN
        elif endpoint == 2:
            enabled             = cpu.FUE2EN
            stall               = cpu.FUE2M1
            ack                 = cpu.FUE2M0
            has_pending_events  = cpu.FEP2_ACK or cpu.FEP2_NAK
            nak_int_en          = cpu.FEP2NAK_INT_EN
        elif endpoint == 3:
            enabled             = cpu.FUE3EN
            stall               = cpu.FUE3M1
            ack                 = cpu.FUE3M0
            has_pending_events  = cpu.FEP3_ACK or cpu.FEP3_NAK
            nak_int_en          = cpu.FEP3NAK_INT_EN
        elif endpoint == 4:
            enabled             = cpu.FUE4EN
            stall               = cpu.FUE4M1
            ack                 = cpu.FUE4M0
            has_pending_events  = cpu.FEP4_ACK or cpu.FEP4_NAK
            nak_int_en          = cpu.FEP4NAK_INT_EN
        if not enabled:
            raise RuntimeError('Endpoint is disabled')
        if stall:
            raise EndpointStall
        if not ack:
            if nak_int_en:
                if endpoint == 1:
                    cpu.FEP1_NAK = 1
                elif endpoint == 2:
                    cpu.FEP2_NAK = 1
                elif endpoint == 3:
                    cpu.FEP3_NAK = 1
                elif endpoint == 4:
                    cpu.FEP4_NAK = 1
                self._interrupt()
            raise EndpointNAK
        if has_pending_events:
            raise RuntimeError('Endpoint accepts transfer but firmware did not clear pending events')
        start, stop = (
            0,
            8,
            cpu.EP2FIFO_ADDR,
            cpu.EP3FIFO_ADDR,
            cpu.EP4FIFO_ADDR,
            0x136,
        )[endpoint:endpoint + 2]
        if stop == 0:
            stop = 0x136
        return start, stop

    def send(self, endpoint, data):
        """
        Write <data> to CPU's USB subsystem in <endpoint> buffer.
        Simulates an OUT USB transaction.
        """
        cpu = self.cpu
        start, stop = self._checkEndpoint(endpoint)
        length = len(data)
        if length > stop - start:
            raise ValueError('Data too long for endpoint buffer')
        for index, value in enumerate(data, start):
            self.epbuf[index] = byte_ord(value)
        if endpoint == 0:
            cpu.FUE0M0 = 0
            cpu.EP0OUT_CNT = length
            cpu.FEP0OUT = 1
        elif endpoint == 1:
            cpu.FUE1M0 = 0
            cpu.UE1R_C = length
            cpu.FEP1_ACK = 1
        elif endpoint == 2:
            cpu.FUE2M0 = 0
            cpu.UE2R_C = length
            cpu.FEP2_ACK = 1
        elif endpoint == 3:
            cpu.FUE3M0 = 0
            cpu.UE3R_C = length
            cpu.FEP3_ACK = 1
        elif endpoint == 4:
            cpu.FUE4M0 = 0
            cpu.UE4R_C = length
            cpu.FEP4_ACK = 1
        self._interrupt()

    def recv(self, endpoint):
        """
        Read any pending data from CPU's USB subsystem in
        <endpoint> buffer.
        Simulates an IN USB transaction.
        """
        cpu = self.cpu
        start, stop = self._checkEndpoint(endpoint)
        length = min(
            (
                cpu.UE0R & 0x0f,
                cpu.UE1R_C,
                cpu.UE2R_C,
                cpu.UE3R_C,
                cpu.UE4R_C,
            )[endpoint],
            stop - start,
        )
        result = b''.join(
            chr(x)
            for x in self.epbuf[start:start + length]
        )
        # XXX: FEP?_ACK only for INT transfers ?
        if endpoint == 0:
            cpu.FUE0M0 = 0
            cpu.FEP0IN = 1
        elif endpoint == 1:
            cpu.FUE1M0 = 0
            cpu.FEP1_ACK = 1
        elif endpoint == 2:
            cpu.FUE2M0 = 0
            cpu.FEP2_ACK = 1
        elif endpoint == 3:
            cpu.FUE3M0 = 0
            cpu.FEP3_ACK = 1
        elif endpoint == 4:
            cpu.FUE4M0 = 0
            cpu.FEP4_ACK = 1
        self._interrupt()
        return result

    def readToggle(self):
        return self.toggle

    def writeToggle(self, value):
        self.toggle = value & 0x07

    @property
    def FCRCERR(self):
        return bool(self.status & 0x80)

    @FCRCERR.setter
    def FCRCERR(self, value):
        if value:
            self.status |= 0x80
        else:
            self.status &= 0x7f

    @property
    def FPKTERR(self):
        return bool(self.status & 0x40)

    @FPKTERR.setter
    def FPKTERR(self, value):
        if value:
            self.status |= 0x40
        else:
            self.status &= 0xcf

    @property
    def FSOF(self):
        return bool(self.status & 0x20)

    @FSOF.setter
    def FSOF(self, value):
        if value:
            self.status |= 0x20
            self._interrupt()
        else:
            self.status &= 0xdf

    @property
    def FBUS_RST(self):
        return bool(self.status & 0x10)

    @FBUS_RST.setter
    def FBUS_RST(self, value):
        if value:
            self.status |= 0x10
            self._interrupt()
        else:
            self.status &= 0xef

    @property
    def FSUSPEND(self):
        return bool(self.status & 0x08)

    @FSUSPEND.setter
    def FSUSPEND(self, value):
        if value:
            self.status |= 0x08
            self._interrupt()
        else:
            self.status &= 0xf7

    @property
    def FEP0SETUP(self):
        return bool(self.status & 0x04)

    @FEP0SETUP.setter
    def FEP0SETUP(self, value):
        if value:
            self.status |= 0x04
        else:
            self.status &= 0xfc

    @property
    def FEP0IN(self):
        return bool(self.status & 0x02)

    @FEP0IN.setter
    def FEP0IN(self, value):
        if value:
            self.status |= 0x02
        else:
            self.status &= 0xfd

    @property
    def FEP0OUT(self):
        return bool(self.status & 0x01)

    @FEP0OUT.setter
    def FEP0OUT(self, value):
        if value:
            self.status |= 0x01
        else:
            self.status &= 0xfe

    @property
    def pin_drive_enable(self):
        return bool(self.drive & 0x04)

    @property
    def drive_data_plus(self):
        return bool(self.drive & 0x02)

    @property
    def drive_data_minus(self):
        return bool(self.drive & 0x01)

class UART(object):
    def __init__(self, cpu, rx_irq_name, rx_ien_name, tx_irq_name, tx_ien_name):
        self.cpu = cpu
        self.rx_irq_name = rx_irq_name
        self.rx_ien_name = rx_ien_name
        self.tx_irq_name = tx_irq_name
        self.tx_ien_name = tx_ien_name
        self.reset()

    def __repr__(self):
        return '<%s@%08x>' % (
            self.__class__.__name__,
            id(self),
        )

    def getState(self):
        return {}

    def setState(self, state):
        pass

    def reset(self):
        pass

    def readRXD1(self):
        raise NotImplementedError

    def readRXD2(self):
        raise NotImplementedError

class AnalogToDigitalConverter(object):
    def __init__(self, cpu, irq_name, ien_name):
        self.cpu = cpu
        self.irq_name = irq_name
        self.ien_name = ien_name
        self.reset()

    def __repr__(self):
        return '<%s@%08x>' % (
            self.__class__.__name__,
            id(self),
        )

    def getState(self):
        return {}

    def setState(self, state):
        pass

    def reset(self):
        pass

    def readADB(self):
        raise NotImplementedError

    def readADR(self):
        raise NotImplementedError

    def writeADR(self, value):
        raise NotImplementedError

INF = float('inf')
class Port(object):
    # TODO: wakeup, open-drain
    def __init__(self, chip, vdd, source_current, sink_current, pull_up, pin_count):
        self.chip = chip
        self.vdd = vdd
        self.max_zero = vdd * .2
        self.min_one = vdd * .8
        self.source_current = source_current
        self.source_impedance = old_div(self.min_one, source_current)
        self.sink_current = sink_current
        self.sink_impedance = old_div(self.max_zero, sink_current)
        self.pull_up_impedance = pull_up
        # Assume floating pins: infinite impedance towards Vss.
        # (volts, impedance)
        self.load_list = [[0, INF] for _ in range(pin_count)]
        self.reset()

    def __repr__(self):
        return '<%s@%08x read=%#04x>' % (
            self.__class__.__name__,
            id(self),
            self.read(),
        )

    def setLoad(self, pin, load):
        self.load_list[pin] = load

    def getInternalAsLoad(self, pin):
        mask = 1 << pin
        if self.direction & mask:
            # Output.
            if self.value & mask:
                return self.vdd, self.source_impedance
            return 0, self.sink_impedance
        # Input.
        if self.pull_up & mask:
            return self.vdd, self.pull_up_impedance
        return 0, INF

    def getState(self):
        return {
            'direction': self.direction,
            'pull_up': self.pull_up,
            'value': self.value,
        }

    def setState(self, state):
        self.direction = state['direction']
        self.pull_up = state['pull_up']
        self.value = state['value']

    def reset(self):
        self.direction = 0x00 # All in
        self.pull_up = 0x00 # No pull-up
        self.value = 0x00 # All zeroes

    def read(self):
        result = 0x00
        for pin, load in enumerate(self.load_list):
            if callable(load):
                load = load()
            (load_voltage, load_impedance) = load
            mask = 1 << pin
            if self.direction & mask:
                # Output.
                # XXX: is it how it is implemented ?
                result |= self.value & mask
            else:
                # Input.
                if self.pull_up & mask:
                    # Voltage divisor:
                    # Vdd-{pull_up_impedance}-pin-{load_impedance}-load_voltage
                    voltage = self.vdd - (
                        old_div((
                            self.vdd - load_voltage
                        ), (load_impedance + self.pull_up_impedance))
                    ) * self.pull_up_impedance
                else:
                    voltage = load_voltage
                if voltage > self.min_one:
                    result |= mask
                elif voltage > self.max_zero:
                    raise ValueError(
                        'Pin %i is metastable: %.3fV' % (pin, voltage),
                    )
        return result

    def write(self, value):
        self.value = value

    def readDirection(self):
        return self.direction

    def writeDirection(self, value):
        self.direction = value

    def writePullUp(self, value):
        self.pull_up = value

class Watchdog(object):
    def __init__(self, cpu):
        self.cpu = cpu
        self.reset()

    def __repr__(self):
        return '<%s@%08x value=%#05x>' % (
            self.__class__.__name__,
            id(self),
            self.value,
        )

    def getState(self):
        return {
            'value': self.value,
        }

    def setState(self, state):
        self.value = state['value']

    def reset(self):
        self.value = 0

    def tic(self): # Call on Fslow
        self.value += 1
        if self.value & 0x7ff == 0: # 11 bits counter
            warnings.warn('Watchdog triggered')
            self.cpu.reset(RESET_SOURCE_WATCHDOG)

    def write(self, value):
        if value == 0x5a:
            self.value = 0
        else:
            warnings.warn('Bad value written to watchdog: %#04x' % (value, ))

class Timer(object):
    def __init__(self, cpu, counter_mask, irq_name, ien_name, mode_mask=0xf0, can_wake=True):
        self.cpu = cpu
        self.counter_mask = counter_mask
        self.irq_name = irq_name
        self.ien_name = ien_name
        self.mode_mask = mode_mask
        self.can_wake = can_wake
        self.reset()

    def __repr__(self):
        return '<%s@%08x mode=%#04x reload=%#04x value=%#06x internal_count=%#04x internal_mask=%#04x>' % (
            self.__class__.__name__,
            id(self),
            self.mode,
            self.reload,
            self.value,
            self.internal_count,
            self.internal_mask,
        )

    def getState(self):
        return {
            'mode': self.mode,
            'value': self.value,
            'reload': self.reload,
            'internal_count': self.internal_count,
            'internal_mask': self.internal_mask,
        }

    def setState(self, state):
        self.mode = state['mode']
        self.value = state['value']
        self.reload = state['reload']
        self.internal_count = state['internal_count']
        self.internal_mask = state['internal_mask']

    def reset(self):
        self.mode = 0x00
        self.value = 0x00
        self.reload = 0x00
        self.internal_count = 0x00
        self.internal_mask = 0x01

    @property
    def enabled(self):
        return bool(self.mode & 0x80)

    # TODO: P0.{1,2,3} input
    def tic(self):
        if self.mode & 0x88 == 0x80:
            self.internal_count += 1
            if self.internal_count & self.internal_mask == 0:
                self.internal_count = 0
                self.value += 1
                if self.value & self.counter_mask == 0:
                    self.value = self.reload if self.mode & 0x04 else 0
                    if self.can_wake:
                        self.cpu.wake()
                    setattr(self.cpu, self.irq_name, 1)
                    if getattr(self.cpu, self.ien_name):
                        self.cpu.interrupt()

    def readLow(self):
        return self.value & 0xff

    def writeLow(self, value):
        self.value = (self.value & 0xff00) | value

    def readHigh(self):
        return (self.value >> 8) & 0xff

    def writeHigh(self, value):
        self.value = (self.value & 0xff) | (value << 8)

    def writeReload(self, value):
        self.reload = value

    def readMode(self):
        return self.mode

    def writeMode(self, value):
        value &= self.mode_mask
        self.mode = value
        self.internal_mask = {
            0x70: 0xff,
            0x60: 0x7f,
            0x50: 0x3f,
            0x40: 0x1f,
            0x30: 0x0f,
            0x20: 0x07,
            0x10: 0x03,
            0x00: 0x01,
        }[value & 0x70]

class TimerCounter(Timer):
    def __init__(self, cpu, irq_name, ien_name):
        super(TimerCounter, self).__init__(cpu, 0xff, irq_name, ien_name, 0xff, False)

class SN8(object):
    def __init__(self, flash_file):
        self.ram = ram = [None] * 0x300
        self.push_buf = (None, None)
        self.flash = [
            unpack('<H', flash_file.read(2))[0]
            for _ in range(0x3000)
        ]
        # TODO: update on rom write
        self.disassembly = systematic(dict(enumerate(self.flash)))
        self.run_time = 0
        self.cycle_count = 0
        self.p0 = p0 = Port(self, 5, 0.015, 0.015, 40000, 7)
        self.p1 = p1 = Port(self, 5, 0.015, 0.015, 40000, 8)
        self.p2 = p2 = Port(self, 3.3, 0.001, 0.002, 55000, 8)
        self.p4 = p4 = Port(self, 5, 0.015, 0.015, 40000, 8)
        self.p5 = p5 = Port(self, 5, 0.015, 0.015, 40000, 5)
        self.slow_clock = 0 # ms
        self.oscillator_wakeup_time = 6 # ms
        self.watchdog = watchdog = Watchdog(self)
        self.t0 = t0 = Timer(self, 0xff, 'FT0IRQ', 'FT0IEN')
        self.t1 = t1 = Timer(self, 0xffff, 'FT1IRQ', 'FT1IEN')
        self.tc0 = tc0 = TimerCounter(self, 'FTC0IRQ', 'FTC0IEN')
        self.tc1 = tc1 = TimerCounter(self, 'FTC1IRQ', 'FTC1IEN')
        self.tc2 = tc2 = TimerCounter(self, 'FTC2IRQ', 'FTC2IEN')
        self.msp = msp = MainSeriesPort(self, 'FMSPIRQ', 'FMSPIEN')
        self.usb = usb = USB(self, 'FUSBIRQ', 'FUSBIEN')
        self.uart = uart = UART(self, 'FUTRXIRQ', 'FUTRXIEN', 'FUTTXIRQ', 'FUTTXIEN')
        self.adc = adc = AnalogToDigitalConverter(self, 'FADCIRQ', 'FADCIEN')
        self.A = None
        addr = self.addressOf
        self._volatile_dict = {
            addr('TC0M'):       (tc0.readMode,      tc0.writeMode),
            addr('TC0C'):       (tc0.readLow,       tc0.writeLow),
            addr('TC0R'):       (None,              tc0.writeReload),
            addr('TC1M'):       (tc1.readMode,      tc1.writeMode),
            addr('TC1C'):       (tc1.readLow,       tc1.writeLow),
            addr('TC1R'):       (None,              tc1.writeReload),
            addr('TC2M'):       (tc2.readMode,      tc2.writeMode),
            addr('TC2C'):       (tc2.readLow,       tc2.writeLow),
            addr('TC2R'):       (None,              tc2.writeReload),
            addr('UDA'):        (usb.readAddress,   usb.writeAddress),
            addr('USTATUS'):    (usb.readStatus,    usb.writeStatus),
            addr('UE0R'):       (usb.readEP0Enable, usb.writeEP0Enable),
            addr('UE1R'):       (usb.readEP1Enable, usb.writeEP1Enable),
            addr('UE2R'):       (usb.readEP2Enable, usb.writeEP2Enable),
            addr('UE3R'):       (usb.readEP3Enable, usb.writeEP3Enable),
            addr('UE4R'):       (usb.readEP4Enable, usb.writeEP4Enable),
            addr('UDR0_R'):     (usb.readFIFO,      None),
            addr('UDR0_W'):     (None,              usb.writeFIFO),
            addr('UPID'):       (usb.readPinControl, usb.writePinControl),
            addr('UTOGGLE'):    (usb.readToggle,    usb.writeToggle),
            addr('URRXD1'):     (uart.readRXD1,     None),
            addr('URRXD2'):     (uart.readRXD2,     None),
            addr('ADB'):        (adc.readADB,       None),
            addr('ADR'):        (adc.readADR,       adc.writeADR),
            addr('P0M'):        (p0.readDirection,  p0.writeDirection),
            addr('P4CON'):      (None,              partial(ram.__setitem__, addr('P4CON'))),
            addr('PECMD'):      (None,              self._writeProgramEraseCommand),
            addr('P1M'):        (p1.readDirection,  p1.writeDirection),
            addr('P2M'):        (p2.readDirection,  p2.writeDirection),
            addr('P4M'):        (p4.readDirection,  p4.writeDirection),
            addr('P5M'):        (p5.readDirection,  p5.writeDirection),
            addr('WDTR'):       (None,              watchdog.write),
            addr('P0'):         (p0.read,           p0.write),
            addr('P1'):         (p1.read,           p1.write),
            addr('P2'):         (p2.read,           p2.write),
            addr('P4'):         (p4.read,           p4.write),
            addr('P5'):         (p5.read,           p5.write),
            addr('T0M'):        (t0.readMode,       t0.writeMode),
            addr('T0C'):        (t0.readLow,        t0.writeLow),
            addr('T1M'):        (t1.readMode,       t1.writeMode),
            addr('T1CL'):       (t1.readLow,        t1.writeLow),
            addr('T1CH'):       (t1.readHigh,       t1.writeHigh),
            addr('P0UR'):       (None,              p0.writePullUp),
            addr('P1UR'):       (None,              p1.writePullUp),
            addr('P2UR'):       (None,              p2.writePullUp),
            addr('P4UR'):       (None,              p4.writePullUp),
            addr('P5UR'):       (None,              p5.writePullUp),
            addr('@YZ'):        (self._readYZ,      self._writeYZ),
            addr('MSPSTAT'):    (msp.readStatus,    msp.writeStatus),
            addr('MSPM1'):      (msp.readMode1,     msp.writeMode1),
            addr('MSPM2'):      (msp.readMode2,     msp.writeMode2),
        }
        # Sanity check
        for key in self._volatile_dict:
            assert REGISTERS_RESET_VALUE_LIST[key - 0x80] is VOLA, hex(key)
        # Non-volatile reisters which do not have all bits populated.
        # Volatile registers should handle masking on their own.
        self._register_mask_dict = {
            addr('PFLAG'):      0xc7,
            addr('RBANK'):      0x03,
            addr('PERAMCNT'):   0xfb,
            addr('OSCM'):       0x1e,
            addr('PCH'):        0x3f,
            addr('STKP'):       0x87,
            addr('STK7H'):      0x3f,
            addr('STK6H'):      0x3f,
            addr('STK5H'):      0x3f,
            addr('STK4H'):      0x3f,
            addr('STK3H'):      0x3f,
            addr('STK2H'):      0x3f,
            addr('STK1H'):      0x3f,
            addr('STK0H'):      0x3f,
        }
        # Sanity check
        volatile_and_masked_set = set(self._register_mask_dict).intersection(
            self._volatile_dict,
        )
        assert not volatile_and_masked_set, volatile_and_masked_set
        self._read_watcher_dict = {}
        self._write_watcher_dict = {}
        self.breakpoint_set = set()
        self._bit_instruction_dict = {
            0x4000: self.clearBit,
            0x4800: self.setBit,
            0x5000: self.testBitZero,
            0x5800: self.testBitOne,
        }
        self._no_operand_instruction_dict = {
            0x0000: self.nop,
            0x0400: self.push,
            0x0500: self.pop,
            0x0d00: self.movc,
            0x0e00: self.ret,
            0x0f00: self.reti,
        }
        logic_or = lambda a, b: a | b
        logic_xor = lambda a, b: a ^ b
        logic_and = lambda a, b: a & b
        self._instruction_dict = {
            0x0600:           self.cmprsAI,                             # CMPRS A, #
            0x0700: lambda x: self.cmprsAI(self.read(self.bankify(x))), # CMPRS A, M

            0x0800: lambda x: self.rotateA(self.bankify(x), self._rrc), # RRC M
            0x0900: lambda x: self.rotateM(self.bankify(x), self._rrc), # RRCM M
            0x0a00: lambda x: self.rotateA(self.bankify(x), self._rlc), # RLC M
            0x0b00: lambda x: self.rotateM(self.bankify(x), self._rlc), # RLCM M

            0x1000: lambda x: self.addAM(self.bankify(x), self.FC), # ADC A, M
            0x1100: lambda x: self.addMA(self.bankify(x), self.FC), # ADC M, A
            0x1200: lambda x: self.addAM(self.bankify(x)),          # ADD A, M
            0x1300: lambda x: self.addMA(self.bankify(x)),          # ADD M, A
            0x1400:           self.addAI,                           # ADD A, #
            0x0300:           self.addMA,                           # B0ADD M, A

            0x2000: lambda x: self.subAM(self.bankify(x), self.FC), # SBC A, M
            0x2100: lambda x: self.subMA(self.bankify(x), self.FC), # SBC M, A
            0x2200: lambda x: self.subAM(self.bankify(x)),          # SUB A, M
            0x2300: lambda x: self.subMA(self.bankify(x)),          # SUB M, A
            0x2400:           self.subAI,                           # SUB A, #

            0x1500: lambda x: self.incAM(self.bankify(x),  1), # INCS M
            0x1600: lambda x: self.incMM(self.bankify(x),  1), # INCMS M
            0x2500: lambda x: self.incAM(self.bankify(x), -1), # DECS M
            0x2600: lambda x: self.incMM(self.bankify(x), -1), # DECMS M

            0x1700: lambda x: self.swapAM(self.bankify(x)), # SWAP M
            0x2700: lambda x: self.swapMM(self.bankify(x)), # SWAPM M

            0x1800: lambda x: self.logicAM(self.bankify(x), logic_or),  # OR  A, M
            0x1900: lambda x: self.logicMA(self.bankify(x), logic_or),  # OR  M, A
            0x1a00: lambda x: self.logicAI(x,               logic_or),  # OR  A, #
            0x1b00: lambda x: self.logicAM(self.bankify(x), logic_xor), # XOR A, M
            0x1c00: lambda x: self.logicMA(self.bankify(x), logic_xor), # XOR M, A
            0x1d00: lambda x: self.logicAI(x,               logic_xor), # XOR A, #
            0x2800: lambda x: self.logicAM(self.bankify(x), logic_and), # AND A, M
            0x2900: lambda x: self.logicMA(self.bankify(x), logic_and), # AND M, A
            0x2a00: lambda x: self.logicAI(x,               logic_and), # AND A, #

            0x1e00: lambda x: self.movAM(self.bankify(x)),          # MOV A, M
            0x2e00:           self.movAM,                           # B0MOV A, M
            0x1f00: lambda x: self.movMI(self.bankify(x), self.A),  # MOV M, A
            0x2f00: lambda x: self.movMI(x,               self.A),  # B0MOV M, A
            0x2d00:           self.movAI,                           # MOV A, #

            0x2b00: lambda x: self.movMI(self.bankify(x), 0), # CLR M
            0x2c00: lambda x: self.xch(self.bankify(x)),      # XCH M
            0x0200:           self.xch,                       # B0XCH M
        }
        self._stkp_underflow = False
        # Power-on reset
        self.reset(RESET_SOURCE_LOW_VOLTAGE)

    def addressOf(self, name):
        return getattr(self.__class__, name).address

    def __repr__(self):
        return '<%s@%08x run_time=%8.3fms cycle_count=%9i A=%#04x R=%#04x Y=%#04x Z=%#04x PC=%#06x FC=%i FZ=%i RBANK=%02i watchdog=%#08x%s instr=%-20s stack=%s>' % (
            self.__class__.__name__,
            id(self),
            self.run_time,
            self.cycle_count,
            self.A or 0,
            self.ram[self.addressOf('R')] or 0,
            self.ram[self.addressOf('Y')] or 0,
            self.ram[self.addressOf('Z')] or 0,
            self.pc,
            self.FC,
            self.FZ,
            self.ram[self.addressOf('RBANK')] or 0,
            self.watchdog.value,
            ''.join(
                ' %s=%r' % (x, getattr(self, x))
                for x in ('t0', 't1', 'tc0', 'tc1', 'tc2')
                if getattr(self, x).enabled
            ),
            self.disassembly[self.pc].expandtabs(8),
            ','.join(
                '%#06x' % (
                    (
                        self.ram[self.addressOf('STK7H') + x * 2] << 8
                    ) | self.ram[self.addressOf('STK7L') + x * 2]
                )
                for x in range(7, self.STKP & 0x7f, -1)
            ),
        )

    @property
    def pc(self):
        return (self.PCH << 8) | self.PCL

    @pc.setter
    def pc(self, value):
        assert 0 <= value <= 0x3fff, repr(value)
        self.PCL = value & 0xff
        self.PCH = (value >> 8) & 0xff

    def step(self):
        if self.OSCM & 0x18:
            # CPU is halted in green & sleep modes
            self.tic()
            return
        pc = self.pc
        if pc in self.breakpoint_set:
            print('bp %#06x %r' % (pc, self))
            import pdb; pdb.set_trace()
        instruction = self.flash[pc]
        if instruction in self._no_operand_instruction_dict:
            self._no_operand_instruction_dict[instruction]()
        elif instruction >= 0x8000: # 0x8000..0xffff: JMP & CALL
            (
                self.call
                if instruction & 0xc000 == 0xc000 else
                self.jump
            )(instruction & 0x3fff)
        elif instruction >= 0x4000: # 0x4000..0x7ffff: bit instructions
            operand = instruction & 0xff
            if (instruction & 0x2000) == 0:
                operand = self.bankify(operand)
            self._bit_instruction_dict[instruction & 0x5800](
                bit=(instruction >> 8) & 0x7,
                address=operand,
            )
        elif instruction >= 0x3000: # 0x3000..0x3fff: B0MOV reg, immediate
            self.movMI(
                0x80 + ((instruction >> 8) & 0x07),
                instruction & 0xff,
            )
        else:
            self._instruction_dict[instruction & 0xff00](
                instruction & 0xff,
            )

    def _writeProgramEraseCommand(self, value):
        if value not in (0x5a, 0xc3):
            warnings.warn('Non-standard PECMD write: %#06x' % (value, ))
            return
        base_address = (self.PEROMH << 8) | self.PEROML
        if 0x2f80  <= base_address < 0x3000 and self.flash[0x2fff] & 0x0002 == 0:
            warnings.warn(
                'Firmware attempted to reprogram protected page with '
                'security set. Ignored.',
            )
            return
        if value == 0x5a: # Program
            ram_base_address = ((self.PERAMCNT & 0x3) << 8) | self.PERAML
            word_count = (self.PERAMCNT >> 3) + 1
            ram_count = word_count * 2
            if (
                0x80 <= ram_base_address < 0x100 or
                0x80 <= ram_base_address + ram_count < 0x100
            ):
                raise ValueError(
                    'Firmware is trying to write register area to flash.',
                )
            for index in range(word_count):
                ram_index = index * 2
                self.flash[base_address + index] = (
                    self.read(ram_index + 1) << 8
                ) | self.read(ram_index)
            self.run_time += 2 # 1~2ms to write a page
        else: # Erase
            base_address &= ~0x7f
            for address in range(
                base_address,
                base_address + 0x80,
            ):
                self.flash[address] = 0xffff
            self.run_time += 50 # 25~50ms to erase a page

    def _readYZ(self):
        address = (self.Y << 8) | self.Z
        assert address != self.addressOf('@YZ')
        return self.read(address)

    def _writeYZ(self, value):
        address = (self.Y << 8) | self.Z
        assert address != self.addressOf('@YZ')
        self.write(address, value)

    def read(self, address):
        assert (address & 0x3ff) == address, hex(address)
        if address in self._volatile_dict:
            reader = self._volatile_dict[address][0]
            if reader is None:
                warnings.warn('Ignoring read from %#06x' % address)
                value = 0
            else:
                value = reader()
        else:
            value = self.ram[address]
            if value is MISS:
                raise ValueError('Nothing to read from at %#05x' % address)
        if value is None:
            raise ValueError('Reading from uninitialised memory')
        if address in self._read_watcher_dict:
            self._read_watcher_dict[address](self, address, value)
        assert value == (value & 0xff), repr(value)
        return value

    def write(self, address, value):
        assert value == (value & 0xff), repr(value)
        assert (address & 0x3ff) == address, hex(address)
        if address in self._write_watcher_dict:
            self._write_watcher_dict[address](self, address, value)
        if address in self._volatile_dict:
            writer = self._volatile_dict[address][1]
            if writer is None:
                warnings.warn('Ignoring write to %#06x: %#04x' % (
                    address,
                    value,
                ))
            else:
                writer(value)
        else:
            original_value = self.ram[address]
            if original_value is MISS:
                warnings.warn('Ignoring write to %#06x: %#04x' % (
                    address,
                    value,
                ))
            else:
                self.ram[address] = value & self._register_mask_dict.get(
                    address,
                    0xff,
                )

    def onRead(self, address, callback):
        if callback is None:
            self._read_watcher_dict.pop(address)
        else:
            self._read_watcher_dict[address] = callback

    def onWrite(self, address, callback):
        if callback is None:
            self._write_watcher_dict.pop(address)
        else:
            self._write_watcher_dict[address] = callback

    def getState(self):
        # State may contain:
        # - dicts with deterministic sets of keys
        # - vectors (lists or tuples, should not matter which)
        # - integers (should be 0..0xffff)
        # - None for uninitialised memory
        # It should not contain anything else.
        result = {
            'run_time': self.run_time,
            'cycle_count': self.cycle_count,
            'slow_clock': self.slow_clock,
            'A': self.A,
            'push_buf': self.push_buf,
            'ram': [0 if x in (VOLA, MISS) else x for x in self.ram],
            'flash': self.flash[:],
        }
        for peripheral_name in (
            'p0', 'p1', 'p2', 'p4', 'p5',
            't0', 't1', 'tc0', 'tc1', 'tc2', 'watchdog',
            'msp', 'usb', 'uart', 'adc',
        ):
            result[peripheral_name] = getattr(self, peripheral_name).getState()
        return result

    def setState(self, state):
        for peripheral_name in (
            'p0', 'p1', 'p2', 'p4', 'p5',
            't0', 't1', 'tc0', 'tc1', 'tc2', 'watchdog',
            'msp', 'usb', 'uart', 'adc',
        ):
            getattr(self, peripheral_name).setState(state[peripheral_name])
        self.run_time = state['run_time']
        self.cycle_count = state['cycle_count']
        self.slow_clock = state['slow_clock']
        self.A = state['A']
        self.push_buf = state['push_buf']
        ram = self.ram
        state_ram = state['ram']
        for index, cell in enumerate(ram):
            if cell not in (MISS, VOLA):
                ram[index] = state_ram[index]
        self.flash[:] = state['flash']
        self._reloadCodeOptions()

    def _reloadCodeOptions(self):
        code_options = self.flash[0x2fff]
        self.watchdog_enabled = code_options & 0x0f00 != 0b1010
        self.watchdog_always_on = code_options & 0x0f00 == 0
        self.high_speed_cycle_duration_ms = {
            0x00: 1,
            0x04: 2,
            0x08: 4,
            0x0c: 8,
        }[code_options & 0x0c] / 12000.
        self.low_speed_cycle_duration_ms = {
            0x00: 2,
            0x80: 4,
        }[code_options & 0x80] / 12.
        self.slow_clock_threshold = (
            old_div(self.low_speed_cycle_duration_ms, self.high_speed_cycle_duration_ms)
        )

    def reset(self, source):
        self.ram[0x80:0x100] = REGISTERS_RESET_VALUE_LIST
        self.PFLAG = (self.PFLAG & 0x3f) | source
        self._reloadCodeOptions()
        for subsystem in (
            self.p0, self.p1, self.p2, self.p4, self.p5,
            self.watchdog,
            self.t0, self.t1,
            self.msp,
            self.uart,
            self.adc,
            self.usb,
        ):
            subsystem.reset()

    def slow_tic(self):
        if (
            self.watchdog_enabled and
            self.OSCM & 0x18 != 0x08 or self.watchdog_always_on
        ):
            self.watchdog.tic()

    def tic(self):
        oscm = self.OSCM
        fcpum1_0 = oscm & 0x18
        if fcpum1_0 == 0x08:
            # sleep
            raise CPUHalted('CPU in sleep mode')
        elif fcpum1_0 == 0x10:
            # green mode
            # XXX: T1 is documented as having wake ability, but is disabled in green & sleep modes.
            # XXX: TC2 is not documented as enabled in any power mode, assuming same as TC0 & TC1.
            device_list = (self.t0, )
        else:
            # normal mode (fast or slow clock)
            device_list = (self.t0, self.t1, self.tc0, self.tc1, self.tc2)
            self.cycle_count += 1
            # USB runs only in fast mode
            if not (oscm & 0x04):
                device_list += (self.usb, )
        if oscm & 0x04:
            # Slow clock source
            self.run_time += self.low_speed_cycle_duration_ms
            self.slow_tic()
        else:
            # Fast clock source
            self.run_time += self.high_speed_cycle_duration_ms
            self.slow_clock += 1
            if self.slow_clock > self.slow_clock_threshold:
                self.slow_tic()
                self.slow_clock -= self.slow_clock_threshold
        for device in device_list:
            device.tic()

    def wake(self):
        oscm = self.OSCM
        fcpum1_0 = oscm & 0x18
        if fcpum1_0 == 0x08:
            # Wake from halt: return to normal mode
            self.OSCM = 0x00
            self.run_time += (
                16384 * self.high_speed_cycle_duration_ms +
                self.oscillator_wakeup_time
            )
        elif fcpum1_0 == 0x10:
            # Wake from green: return to previous mode
            self.OSCM = oscm & ~0x18
        # else, ignore

    def bankify(self, address):
        return (self.RBANK << 8) | address

    # Instructions

    def nop(self):
        self.pc += 1
        self.tic()

    def jump(self, addr):
        self.pc = addr
        self.tic()
        self.tic()

    def _call(self, addr):
        stkp = self.STKP & 0x07
        if stkp == 0:
            self._stkp_underflow = True
        elif stkp == 7 and self._stkp_underflow:
            warnings.warn('Stack pointer underflow')
        offset = stkp * 2
        self.write(self.addressOf('STK7L') + offset, self.PCL)
        self.write(self.addressOf('STK7H') + offset, self.PCH)
        self.STKP = (self.STKP & 0xf8) | ((stkp - 1) & 0x07)
        self.jump(addr)

    def call(self, addr):
        self.pc += 1
        self._call(addr)

    def ret(self):
        stkp = self.STKP & 0x07
        if stkp == 7:
            if self._stkp_underflow:
                self._stkp_underflow = False
            else:
                warnings.warn('Stack pointer overflow')
        stkp = (stkp + 1) & 0x07
        offset = stkp * 2
        self.STKP = (self.STKP & 0xf8) | stkp
        self.jump(
            (
                (
                    self.read(self.addressOf('STK7H') + offset) << 8
                ) | self.read(self.addressOf('STK7L') + offset)
            )
        )

    def interrupt(self):
        if self.FGIE:
            self.FGIE = False
            # XXX: assuming interrupt has 2-cycle duration, like a normal call
            self._call(0x0008) # TODO: symbolic name from config file

    def reti(self):
        # XXX: assuming interrupts are re-enabled before jumping back (so tics
        # can interrupt again).
        self.FGIE = True
        self.ret()

    def push(self):
        self.pc += 1
        # XXX: are NT0, NPD masked on push or on pop ?
        self.push_buf = self.A, (self.PFLAG & 0x3f)
        self.tic()

    def pop(self):
        self.pc += 1
        self.A = self.push_buf[0]
        self.PFLAG = (self.PFLAG & 0xc0) | self.push_buf[1]
        self.tic()

    def movc(self):
        self.pc += 1
        value = self.flash[(self.Y << 8) | self.Z]
        self.A = value & 0xff
        self.R = value >> 8
        self.tic()
        self.tic()

    def xch(self, address):
        self.pc += 1
        from_ram = self.read(address)
        self.write(address, self.A)
        self.A = from_ram
        # FZ unchanged
        if not 0x80 <= address < 0x100:
            self.tic()
        self.tic()

    def movAM(self, address):
        self.pc += 1
        self.A = value = self.read(address)
        self.FZ = value == 0
        self.tic()

    def movAI(self, immediate):
        self.pc += 1
        self.A = immediate
        # FZ unchanged
        self.tic()

    def movMI(self, address, value):
        self.pc += 1
        self.write(address, value)
        self.tic()

    @staticmethod
    def _swap(value):
        return ((value << 4) & 0xf0) | ((value >> 4) & 0x0f)

    def swapMM(self, address):
        self.pc += 1
        self.write(address, self._swap(self.read(address)))
        if not 0x80 <= address < 0x100:
            self.tic()
        self.tic()

    def swapAM(self, address):
        self.pc += 1
        self.A = self._swap(self.read(address))
        # FZ unchanged
        self.tic()

    def logicAI(self, immediate, logic):
        self.pc += 1
        self.A = value = logic(self.A, immediate)
        self.FZ = value == 0
        self.tic()

    def logicAM(self, address, logic):
        self.pc += 1
        self.A = value = logic(self.A, self.read(address))
        self.FZ = value == 0
        self.tic()

    def logicMA(self, address, logic):
        self.pc += 1
        value = logic(self.A, self.read(address))
        self.write(address, value)
        self.FZ = value == 0
        if not 0x80 <= address < 0x100:
            self.tic()
        self.tic()

    def cmprsAI(self, immediate):
        self.pc += 1
        a = self.A
        self.FC = a >= immediate
        self.FZ = condition = a == immediate
        if condition:
            self.pc += 1
            self.tic()
        self.tic()

    def _rrc(self, address):
        value = (self.FC << 8) | self.read(address)
        self.FC = value & 1
        return value >> 1

    def _rlc(self, address):
        value = self.read(address) << 1 | self.FC
        self.FC = bool(value & 0x100)
        return value & 0xff

    def rotateA(self, address, rotor):
        # Tested on vendor's simulator: "RLC PCL" stores in A the address of
        # next instruction, shifted left once.
        # Similarly, "RRC PCL" stored in A the address of next instruction,
        # shifted right once.
        self.pc += 1
        self.A = rotor(address)
        self.tic()

    def rotateM(self, address, rotor):
        # Tested on vendor's simulator: "ORG 0x7e RLCM PCL" jumps to 0xff, so
        # it sees PCL as 0x7f.
        # Similarly, "ORG 0xfd RRCM PCL" jumps to 0x7f, so it sees PCL as 0xfe
        # (or 0xff, but that would not make sense).
        self.pc += 1
        self.write(address, rotor(address))
        if not 0x80 <= address < 0x100:
            self.tic()
        self.tic()

    def _addAI(self, immediate):
        a = self.A
        value = a + immediate
        byte_value = value & 0xff
        self.FC = value > 0xff
        self.FDC = (a & 0xf) + (immediate & 0xf) > 0xf
        self.FZ = byte_value == 0
        return byte_value

    def addAM(self, address, carry=0):
        # Tested on vendor's simulator: "MOV A, #0x00\nADD A, PCL" stores
        # in A the address of next instruction.
        self.pc += 1
        self.A = self._addAI(self.read(address) + carry)
        self.tic()

    def addMA(self, address, carry=0):
        # Tested on vendor's simulator: "MOV A, #0xfe\nADD PCL, A" jumps to
        # previous instruction. 0xfe is -2 (modulo 0x100), so ADD instruction
        # saw PCL pointing at the next instruction.
        # Note, this simulator behavior directly contradicts the spec:
        # according to 2.1.1.4, if PCL overflows because of "ADD PCL, A", then
        # PCH is incremented by 1.
        # Then again, the spec contradicts itself several times by both saying
        # the above and on the very next page (same section) saying that jump
        # tables must not sit on both sides of a 0x100 boundary.
        # So just raise if this situation happens.
        self.pc += 1
        self.write(address, self._addAI(self.read(address) + carry))
        if address == self.addressOf('PCL') and self.FC:
            raise RuntimeError('Incrementing PCL overflows')
        if not 0x80 <= address < 0x100:
            self.tic()
        self.tic()

    def addAI(self, immediate):
        self.pc += 1
        self.A = self._addAI(immediate)
        self.tic()

    def _subIA(self, immediate):
        a = self.A
        value = a - immediate
        byte_value = value & 0xff
        self.FC = value >= 0
        self.FDC = (a & 0xf) - (immediate & 0xf) >= 0
        self.FZ = byte_value == 0
        return byte_value

    def subAM(self, address, carry=1):
        self.pc += 1
        self.A = self._subIA(self.read(address) - carry + 1)
        self.tic()

    def subMA(self, address, carry=1):
        # read, pc increment and write order confirmed using vendor's
        # simulator:
        # ORG 0
        #   MOV A, #2  ; pc=0
        #   SUB PCL, A ; pc=1, doing pc=pc-2 jumps to 0 and not to 0xff
        # (side note: vendor's emulator soft-freezes when jumping below 0, as
        # if addredd -1 contained "JMP $")
        # XXX: vendor's simulator behaves strangely: "SUB PCL, A" jumps to
        # 0x2000, apparently whatever A and ORG are.
        self.pc += 1
        self.write(address, self._subIA(self.read(address) - carry + 1))
        self.tic()
        if not 0x80 <= address < 0x100:
            self.tic()

    def subAI(self, immediate):
        self.pc += 1
        self.A = self._subIA(immediate)
        self.tic()

    def clearBit(self, address, bit):
        # Tested on vendor's simulator: "ORG 0xfd BCLR PCL.0" jumps to next
        # instruction, so PC was 0x before the bit gets set.
        self.pc += 1
        self.write(address, self.read(address) & ~(1 << bit))
        if not 0x80 <= address < 0x100:
            self.tic()
        self.tic()

    def setBit(self, address, bit):
        # Tested on vendor's simulator: "ORG 0xff BSET PCL.0" skips next
        # instruction, so PC was 0x100 before the bit gets set.
        self.pc += 1
        self.write(address, self.read(address) | (1 << bit))
        if not 0x80 <= address < 0x100:
            self.tic()
        self.tic()

    def testBitZero(self, address, bit):
        # Tested on vendor's simulator: "BTS0 PCL.0" on an odd address skips
        # next instruction (so it saw 0, so PCL was even, so it was next
        # instruction's address)
        self.pc += 1
        if not self.read(address) & (1 << bit):
            self.pc += 1
            self.tic()
        self.tic()

    def testBitOne(self, address, bit):
        # Tested on vendor's simulator: "BTS1 PCL.0" on an even address skips
        # next instruction (so it saw 1, so PCL was odd, so it was next
        # instruction's address)
        self.pc += 1
        if self.read(address) & (1 << bit):
            self.pc += 1
            self.tic()
        self.tic()

    def incAM(self, address, immediate):
        # Tested on vendor's simulator: "DECS PCL" on a 256-aligned address
        # puts 0 in A, so it saw 0x01 as PCL. It then skips next instruction,
        # meaning there are 2 separate increments of PC for this test
        # instruction.
        # Similarly, "INCS PCL" store next instruction's address plus one,
        # and skips next instruction if that value is zero.
        self.pc += 1
        self.A = result = (self.read(address) + immediate) & 0xff
        if result == 0:
            self.pc += 1
            self.tic()
        self.tic()

    def incMM(self, address, immediate):
        # Tested on vendor(s simulator: "ORG 0xfd INCMS PCL" skips next
        # instruction, despite FZ being cleared, meaning PCL was seen as 0xfe.
        # Similarly, "ORG 0x100 DECMS PCL" jumps to next instruction, despite
        # FZ being set, meaning "skip next instruction" re-reads PC after it
        # was decremented.
        # XXX "ORG 0xfe INCMS PCL" soft-freezes the emulator (PC stays on 0xfe
        # while instruction counter runs until interrupted). Why ?
        self.pc += 1
        result = (self.read(address) + immediate) & 0xff
        self.write(address, result)
        if result == 0:
            self.pc += 1
            self.tic()
        if not 0x80 <= address < 0x100:
            self.tic()
        self.tic()

def newChip(name):
    base = SN8
    config = parseConfig([open(
        os.path.join(CONFIG_DIR, name.lower() + '.cfg')
    )])
    # TODO: pull more from config, and de-hardcode from SN8
    dikt = {}
    for address, register_name in list(config['ram-reserved'].items()):
        assert not hasattr(base, register_name)
        assert register_name not in dikt
        if '.' in address:
            address, bit = address.split('.')
            propertified = BitProperty(int(address, 0), int(bit, 0))
        else:
            propertified = ByteProperty(int(address, 0))
        dikt[register_name] = propertified
    return type(name, (base, ), dikt)

SN8F2288 = newChip('SN8F2288')
