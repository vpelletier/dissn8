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

ASMLIB_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', 'asmlib'),
)

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

if __name__ == '__main__':
    unittest.main()
