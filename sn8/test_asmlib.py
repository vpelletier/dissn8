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

from __future__ import absolute_import
import os.path
import unittest
from .test_base import SimSN8F2288TestBase
from .libsimsn8 import BitBanging8bitsI2C

ASMLIB_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', 'asmlib'),
)

class TracingBitBanging8bitsI2C(BitBanging8bitsI2C):
    def __init__(self, cpu, *args, **kw):
        super(TracingBitBanging8bitsI2C, self).__init__(*args, **kw)
        self._cpu = cpu

    def reset(self):
        super(TracingBitBanging8bitsI2C, self).reset()
        self.event_list = []

    def onClockEdge(self, scl, sda):
        self.event_list.append((self._cpu.run_time, 'scl', scl, sda))
        super(TracingBitBanging8bitsI2C, self).onClockEdge(scl, sda)

    def onDataEdge(self, scl, sda):
        self.event_list.append((self._cpu.run_time, 'sda', scl, sda))
        super(TracingBitBanging8bitsI2C, self).onDataEdge(scl, sda)

class ASMLibTests(SimSN8F2288TestBase):
    def _getSimulator(self, *args, **kw):
        return super(ASMLibTests, self)._getSimulator(
            include=[ASMLIB_DIR],
            *args,
            **kw
        )
    def testWatchdog(self):
        sim = self._getSimulator(
            u'''
            //{{SONIX_CODE_OPTION
                .Code_Option Fcpu "Fosc/8"
            //}}SONIX_CODE_OPTION
            .CODE
            ORG 0
                JMP reset
            ORG 0x10
            reset:
                INCLUDE "init.asm"
            power_on_reset:
            external_reset:
                ; Fcpu is 1.5MHz, Fslow is 6kHz: Fslow tics every 250 CPU cycle
                B0MOV   RBANK, #0
                MOV     A, #63
                MOV     0x02, A
            @@:                     ; each loop takes 4 cycles:
                DECMS   0x02        ; 2 cycles
                JMP     @B          ; 2 cycles

                B0MOV   0x00, A     ; signal that we are about to reset watchdog
                INCLUDE "watchdog.asm"
                B0MOV   0x01, A     ; signal that watchdog is now reset
                JMP     $
            watchdog_reset:
                JMP     $
            ''',
            watchdog=u'Enable',
        )
        assert sim.ram[0] is None
        while sim.ram[0] is None:
            sim.step()
        self.assertNotEqual(sim.watchdog.value, 0)
        assert sim.ram[1] is None
        while sim.ram[1] is None:
            sim.step()
        self.assertEqual(sim.watchdog.value, 0)
        normal_reset_loop_address = sim.pc
        # watchdog is expected to overflow after 341.3ms
        deadline = sim.run_time + 341.4
        step = sim.step
        while sim.run_time < deadline:
            step()
        self.assertEqual(sim.pc, normal_reset_loop_address + 1)

    def test_i2c(self):
        # XXX: does not test clock stretching
        sim = self._getSimulator(u'''
        //{{SONIX_CODE_OPTION
            .Code_Option Fcpu "Fosc/4"
        //}}SONIX_CODE_OPTION
        .DATA
        sda     EQU P0.0
        sda_dir EQU P0M.0
        scl     EQU P0.1
        scl_dir EQU P0M.1
        _24c32_read_address   EQU     010100001b
        _24c32_write_address  EQU     010100000b
        .CODE
        ORG 0
            ; Note: not setting RBANK. I2C code is expected to be bank-safe.
            MOV     A, #0x00
            B0MOV   P0M, A
            MOV     A, #0x00
            B0MOV   P0, A
            CALL    i2c_start
            B0MOV   R, #_24c32_write_address
            CALL    i2c_write_byte      ; I2C address byte
            B0BTS0  FC
            JMP     _end
            B0MOV   R, #0x12
            CALL    i2c_write_byte      ; EEPROM address byte 0
            B0BTS0  FC
            JMP     _end
            B0MOV   R, #0x34
            CALL    i2c_write_byte      ; EEPROM address byte 1
            B0BTS0  FC
            JMP     _end
            CALL    i2c_start           ; restart
            B0MOV   R, #_24c32_read_address
            CALL    i2c_write_byte      ; I2C address byte
            B0BTS0  FC
            JMP     _end
            CALL    i2c_read_byte       ; read data byte 0
            B0MOV   A, R
            B0MOV   0x00, A
            CALL    i2c_ack
            CALL    i2c_read_byte       ; read data byte 1
            B0MOV   A, R
            B0MOV   0x01, A
            CALL    i2c_ack
            CALL    i2c_read_byte       ; read data byte 2
            B0MOV   A, R
            B0MOV   0x02, A
            CALL    i2c_ack
            CALL    i2c_read_byte       ; read data byte 3
            B0MOV   A, R
            B0MOV   0x03, A
            CALL    i2c_nak
        _end:
            CALL    i2c_stop
            B0MOV   0x04, A             ; signal the end
            JMP     $
        INCLUDE "i2c.asm"
        ''')
        event_list = []
        def onEvent(event, value=None):
            event_list.append((event, value))
        def onAddressed(read):
            onEvent('addr', read)
            return True
        def onDataByteReceived(data):
            onEvent('datw', data)
            return True
        _getNextDataByte = iter((0x81, 0x00, 0xff, 0x5a)).next
        def getNextDataByte():
            result = _getNextDataByte()
            onEvent('datr', result)
            return result
        i2c_device = TracingBitBanging8bitsI2C(
            cpu=sim,
            address=0b01010000,
            onAddressed=onAddressed,
            onStop=lambda: onEvent('stop'),
            onDataByteReceived=onDataByteReceived,
            getNextDataByte=getNextDataByte,
        )
        p0 = sim.p0
        HIGH = (p0.vdd, p0.vdd * 1000) # 1mA
        LOW = (0, 0) # short to ground
        p0.setLoad(0, lambda: HIGH if i2c_device.sda_float else LOW)
        p0.setLoad(1, lambda: HIGH if i2c_device.scl_float else LOW)
        assert sim.ram[4] is None
        while sim.ram[4] is None and sim.run_time < 2:
            sim.step()
            p0data = p0.read()
            i2c_device.step(scl=p0data & 2, sda=p0data & 1)
        # Protocol event checks
        self.assertEqual(
            event_list,
            [
                ('addr', False), # Writing operation address
                ('datw', 0x12),  # Address byte 1
                ('datw', 0x34),  # Address byte 2
                ('addr', True),  # Reading operation start
                ('datr', 0x81),  #
                ('datr', 0x00),  #
                ('datr', 0xff),  #
                ('datr', 0x5a),  #
                ('stop', None),  # Done
            ],
        )
        self.assertEqual(sim.ram[0], 0x81)
        self.assertEqual(sim.ram[1], 0x00)
        self.assertEqual(sim.ram[2], 0xff)
        self.assertEqual(sim.ram[3], 0x5a)
        self.assertNotEqual(sim.ram[4], None)
        # Wire event timing checks
        TIMING_CONSTRAINT_LIST = [
            # prev  curr   scl    sda     ms
            ('sda', 'scl', False, False, .00400), # tHD;STA
            ('scl', 'scl', True , None , .00470), # tLOW
            ('scl', 'scl', False, None , .00400), # tHIGH
            ('scl', 'sda', True , False, .00470), # tSU;STA
            #('sda', 'scl', False, False, 0     ), # tHD;DAT - only for CBUS
            ('sda', 'scl', True , None , .00025), # tSU;DAT
            ('scl', 'sda', True , True , .00400), # tSU;STO
            ('sda', 'sda', True , False, .00470), # tBUF
            #('', '', None , None , .00345), # tVD;DAT - max, for device
            #('', '', None , None , .00345), # tVD;ACK - max, for device
        ]
        last_clock_rising_time = 0
        last_clock_falling_time = 0
        MIN_CLOCK_PERIOD = .001 # 100kHz clock: 1ms period
        last_event_time = 0
        last_event_type = None
        for index, (time, event_type, scl, sda) in enumerate(
            i2c_device.event_list,
        ):
            duration = time - last_event_time
            for (
                constraint_last_type,
                constraint_type,
                constraint_scl,
                constraint_sda,
                constraint_min_duration,
            ) in TIMING_CONSTRAINT_LIST:
                if (
                    constraint_last_type == last_event_type and
                    constraint_type == event_type and
                    constraint_scl in (None, scl) and
                    constraint_sda in (None, sda)
                ):
                    self.assertGreaterEqual(
                        duration,
                        constraint_min_duration,
                        (
                            index,
                            len(i2c_device.event_list),
                            last_event_type,
                            event_type,
                            scl,
                            sda,
                            duration,
                        ),
                    )
            last_event_time = time
            last_event_type = event_type
            if event_type == 'scl':
                if scl:
                    duration = time - last_clock_rising_time
                    last_clock_rising_time = time
                else:
                    duration = time - last_clock_falling_time
                    last_clock_falling_time = time
                self.assertGreaterEqual(duration, MIN_CLOCK_PERIOD)

    def test_power(self):
        sim = self._getSimulator(u'''
        B0BCLR  FC          ; Keep xtal running
        MOV     A, #0
        CALL    power_slow
        B0MOV   0x00, A
        NOP                 ; Mesure slow cycle duration
        CALL    power_normal
        B0MOV   0x01, A
        NOP                 ; Mesure fast cycle duration
        ; setup T0 for wakeup
        MOV     A, #0xf0
        B0MOV   T0C, A      ; overflow in 16
        B0MOV   T0M, A      ; enable, fCPU/2
        CALL    power_green
        B0MOV   0x02, A
        INCLUDE "power.asm"
        ''')
        FAST_CYCLE_DURATION = 1./12000 # in ms
        SLOW_CYCLE_DURATION = 2./12    # in ms
        assert sim.ram[0] is None
        while sim.ram[0] is None:
            sim.step()
        before_time = sim.run_time
        sim.step() # NOP
        self.assertAlmostEqual(sim.run_time - before_time, SLOW_CYCLE_DURATION)
        assert sim.ram[1] is None
        while sim.ram[1] is None:
            sim.step()
        before_time = sim.run_time
        sim.step() # NOP
        self.assertAlmostEqual(sim.run_time - before_time, FAST_CYCLE_DURATION)
        sim.step() # MOV A,I
        sim.step() # B0MOV M, A
        sim.step() # B0MOV M, A
        # From here, T0 is ticking, and will take at most 32 instructions to
        # wake system up
        deadline = sim.run_time + FAST_CYCLE_DURATION * 16
        OSCM_ADDR = sim.addressOf('OSCM')
        assert sim.ram[OSCM_ADDR] & 0x10 == 0x00
        while sim.ram[OSCM_ADDR] & 0x10 == 0x00 and sim.run_time < deadline:
            sim.step()
        self.assertEqual(sim.ram[OSCM_ADDR] & 0x10, 0x10)
        assert sim.ram[2] is None
        while sim.ram[2] is None and sim.run_time < deadline:
            sim.step()
        self.assertEqual(sim.ram[OSCM_ADDR] & 0x10, 0x00)
        self.assertLess(sim.run_time, deadline)

if __name__ == '__main__':
    unittest.main()
