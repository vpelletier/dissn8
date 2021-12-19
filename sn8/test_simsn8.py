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

import unittest
from .test_base import SimSN8F2288TestBase, EQUAL

class SimSN8F2288Tests(SimSN8F2288TestBase):
    def testJMP(self):
        sim = self._getSimulator('''
                NOP
                JMP $
        ''')
        state0 = sim.getState()
        sim.step()
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x01,
                },
            },
        )
        sim.step()
        state2 = sim.getState()
        self.assertEqual(state1['cycle_count'] + 2, state2['cycle_count'])
        self.assertEqual(self._stripStateTiming(state1), self._stripStateTiming(state2))

    def testCALL_RET(self):
        sim = self._getSimulator('''
                CALL    func
                MOV     A, #0x01
                B0MOV   STKP, A
                CALL    func2
                JMP     $
            ORG 0x1234
            func:
                RET
            func2:
                CALL    func
                RET
        ''')
        state0 = sim.getState()
        sim.step() # CALL
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x34,
                    sim.addressOf('PCH'): 0x12,
                    sim.addressOf('STKP'): 6,
                    sim.addressOf('STK0L'): 0x01,
                    #sim.addressOf('STK0H'): 0x00, # Unchanged
                },
            },
        )
        sim.step() # RET
        state2 = sim.getState()
        self.assertEqual(state1['cycle_count'] + 2, state2['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state1, state2,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x01,
                    sim.addressOf('PCH'): 0x00,
                    sim.addressOf('STKP'): 7,
                },
            },
        )
        sim.step() # MOV A, I
        sim.step() # B0MOV M, A
        state0 = sim.getState()
        sim.step() # CALL
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x35,
                    sim.addressOf('PCH'): 0x12,
                    sim.addressOf('STKP'): 0,
                    sim.addressOf('STK6L'): 0x04,
                    #sim.addressOf('STK6H'): 0x00, # Unchanged
                },
            },
        )
        sim.step() # CALL
        state2 = sim.getState()
        self.assertEqual(state1['cycle_count'] + 2, state2['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state1, state2,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x34,
                    #sim.addressOf('PCH'): 0x12, # Unchanged
                    sim.addressOf('STKP'): 7,
                    sim.addressOf('STK7L'): 0x36,
                    sim.addressOf('STK7H'): 0x12,
                },
            },
        )
        sim.step() # RET
        state3 = sim.getState()
        self.assertEqual(state2['cycle_count'] + 2, state3['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state2, state3,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x36,
                    #sim.addressOf('PCH'): 0x12, # Unchanged
                    sim.addressOf('STKP'): 0,
                },
            },
        )
        sim.step() # RET
        state4 = sim.getState()
        self.assertEqual(state3['cycle_count'] + 2, state4['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state3, state4,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x04,
                    sim.addressOf('PCH'): 0x00,
                    sim.addressOf('STKP'): 1,
                },
            },
        )

    def _testMOV_BSET_BCLR(self, bank):
        sim = self._getSimulator('''
                MOV     A, #0x55
                MOV     0x00, A
                B0MOV   0x01, A
                BCLR    0x00.0
                BSET    0x00.1
                B0BCLR  0x01.4
                B0BSET  0x01.5
                MOV     A, #0
                MOV     0x02, A
                MOV     A, 0x02
                B0MOV   A, 0x01
                CLR     0x00
        ''')
        bank_address = bank << 8
        sim.RBANK = bank
        state0 = sim.getState()
        sim.step() # MOV A, #
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'A': 0x55,
                'ram': {
                    sim.addressOf('PCL'): 0x01,
                },
            },
        )
        sim.step() # MOV M, A
        sim.step() # B0MOV M, A
        state2 = sim.getState()
        self.assertEqual(state1['cycle_count'] + 2, state2['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state1, state2,
            {
                'ram': {
                    bank_address + 0x00: 0x55,
                    0x01: 0x55,
                    sim.addressOf('PCL'): 0x03,
                },
            },
        )
        sim.step() # BCLR M.b
        sim.step() # BSET M.b
        sim.step() # B0BCLR M.b
        sim.step() # B0BSET M.b
        state3 = sim.getState()
        self.assertEqual(state2['cycle_count'] + 8, state3['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state2, state3,
            {
                'ram': {
                    bank_address + 0x00: 0x56,
                    0x01: 0x65,
                    sim.addressOf('PCL'): 0x07,
                },
            },
        )
        sim.step() # MOV A, I
        state4 = sim.getState()
        self.assertEqual(state3['cycle_count'] + 1, state4['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state3, state4,
            {
                'A': 0x00,
                'ram': {
                    sim.addressOf('PCL'): 0x08,
                },
            },
        )
        sim.step() # MOV M, A
        state5 = sim.getState()
        self.assertEqual(state4['cycle_count'] + 1, state5['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state4, state5,
            {
                'ram': {
                    bank_address + 0x02: 0x00,
                    sim.addressOf('PCL'): 0x09,
                },
            },
        )
        sim.step() # MOV A, M
        state6 = sim.getState()
        self.assertEqual(state5['cycle_count'] + 1, state6['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state5, state6,
            {
                'ram': {
                    sim.addressOf('PFLAG'): 0x81, # Z set
                    sim.addressOf('PCL'): 0x0a,
                },
            },
        )
        sim.step() # B0MOV A, M
        state7 = sim.getState()
        self.assertEqual(state6['cycle_count'] + 1, state7['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state6, state7,
            {
                'A': 0x65,
                'ram': {
                    sim.addressOf('PFLAG'): 0x80, # Z cleared
                    sim.addressOf('PCL'): 0x0b,
                },
            },
        )
        sim.step() # CLR M
        state8 = sim.getState()
        self.assertEqual(state7['cycle_count'] + 1, state8['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state7, state8,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x0c,
                    bank_address + 0x00: 0x00,
                },
            },
        )

    def testMOV_BSET_BCLR(self):
        self._testMOV_BSET_BCLR(0)
        self._testMOV_BSET_BCLR(1)
        self._testMOV_BSET_BCLR(2)

    def testInterrupt_RETI(self):
        sim = self._getSimulator('''
                B0BSET  FGIE
                JMP     $
            ORG 8
                RETI
        ''')
        sim.step() # B0BSET FGIE
        self.assertTrue(sim.FGIE)
        state0 = sim.getState()
        # One jump, to check that RETI will obey current instruction and not go
        # to next address.
        sim.step()
        # Interrupt
        sim.interrupt()
        state1 = sim.getState()
        # XXX: no cycle count check, as it does not seem specified.
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x08,
                    #sim.addressOf('PCH'): 0x00, # Unchanged
                    sim.addressOf('STKP'): 6, # FGIE cleared
                    sim.addressOf('STK0L'): 0x01,
                    #sim.addressOf('STK0H'): 0x00, # Unchanged
                },
            },
        )
        # RETI
        sim.step()
        state2 = sim.getState()
        self.assertEqual(state1['cycle_count'] + 2, state2['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state1, state2,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x01,
                    #sim.addressOf('PCH'): 0x00, # Unchanged
                    sim.addressOf('STKP'): 0x87, # FGIE set
                },
            },
        )
        # One jump, checking that RETI did not escape codepath.
        sim.step()
        self.assertStrippedDifferenceEqual(state2, sim.getState(), EQUAL)

    def testPushPop(self):
        sim = self._getSimulator('''
                MOV     A, #0xaa
                B0BSET  FC
                B0BSET  FZ
                PUSH
                MOV     A, #0x55
                B0BCLR  FC
                B0BSET  FDC
                POP
        ''')
        state0 = sim.getState()
        sim.step() # MOV A, I
        sim.step() # B0BSET M.b
        sim.step() # B0BSET M.b
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 3, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'A': 0xaa,
                'ram': {
                    sim.addressOf('PFLAG'): 0b10000101,
                    sim.addressOf('PCL'): 0x03,
                },
            },
        )
        sim.step() # PUSH
        state2 = sim.getState()
        self.assertEqual(state1['cycle_count'] + 1, state2['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state1, state2,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x04,
                },
                # push buffer is considered out-of-ram, and not visible in
                # vendor's simulator. So order should not really matter.
                # Also, whether PFLAG is masked at push does not really matter.
                'push_buf': {
                    0: 0xaa,
                    1: 0b00000101,
                },
            },
        )
        sim.step() # MOV A, I
        sim.step() # B0BCLR M.b
        sim.step() # B0BSET M.b
        state3 = sim.getState()
        self.assertEqual(state2['cycle_count'] + 3, state3['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state2, state3,
            {
                'A': 0x55,
                'ram': {
                    sim.addressOf('PFLAG'): 0b10000011,
                    sim.addressOf('PCL'): 0x07,
                },
            },
        )
        sim.step() # POP
        state4 = sim.getState()
        self.assertEqual(state3['cycle_count'] + 1, state4['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state3, state4,
            {
                'A': 0xaa,
                'ram': {
                    sim.addressOf('PFLAG'): 0b10000101,
                    sim.addressOf('PCL'): 0x08,
                },
            },
        )

    def test_MOVC(self):
        sim = self._getSimulator('''
        .DATA
        data    EQU 0x2345
        .CODE
        ORG 0
                MOV     A, #data$M
                B0MOV   Y, A
                MOV     A, #data$L
                B0MOV   Z, A
                MOVC
        ORG data
                DW      0x1234
        ''')
        state0 = sim.getState()
        sim.step() # MOV A, I
        sim.step() # MOV M, A
        sim.step() # MOV A, I
        sim.step() # MOV M, A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 4, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'A': 0x45,
                'ram': {
                    sim.addressOf('Y'): 0x23,
                    sim.addressOf('Z'): 0x45,
                    sim.addressOf('PCL'): 0x04,
                },
            },
        )
        sim.step()
        state2 = sim.getState()
        self.assertEqual(state1['cycle_count'] + 2, state2['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state1, state2,
            {
                'A': 0x34,
                'ram': {
                    sim.addressOf('R'): 0x12,
                    sim.addressOf('PCL'): 0x05,
                },
            },
        )

    def test_XCH(self):
        sim = self._getSimulator('''
                B0MOV   RBANK, #1
                MOV     A, #0x55
                MOV     0x00, A
                B0MOV   R, #0xff
                MOV     A, #0
                B0XCH   A, R
                XCH     A, 0x00
                MOV     A, #0
                B0MOV   RBANK, A
                XCH     A, PCL
        ''')
        state0 = sim.getState()
        sim.step() # B0MOV M, I
        sim.step() # MOV A, I
        sim.step() # MOV M, A
        sim.step() # B0MOV M, I
        sim.step() # MOV A, I
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 5, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'A': 0x00,
                'ram': {
                    sim.addressOf('R'): 0xff,
                    sim.addressOf('RBANK'): 0x01,
                    sim.addressOf('PCL'): 0x05,
                    0x0100: 0x55,
                },
            },
        )
        sim.step() # B0XCH
        state2 = sim.getState()
        self.assertEqual(state1['cycle_count'] + 1, state2['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state1, state2,
            {
                'A': 0xff,
                'ram': {
                    sim.addressOf('R'): 0x00,
                    sim.addressOf('PCL'): 0x06,
                },
            },
        )
        sim.step() # XCH
        state3 = sim.getState()
        self.assertEqual(state2['cycle_count'] + 2, state3['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state2, state3,
            {
                'A': 0x55,
                'ram': {
                    0x0100: 0xff,
                    sim.addressOf('PCL'): 0x07,
                },
            },
        )
        sim.step() # NOP
        sim.step() # B0MOV M, I
        sim.step() # XCH
        state4 = sim.getState()
        self.assertEqual(state3['cycle_count'] + 3, state4['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state3, state4,
            {
                'A': 0x0a, # Address after XCH, coming from PCL
                'ram': {
                    sim.addressOf('RBANK'): 0x00,
                    sim.addressOf('PCL'): 0x00,
                },
            },
        )

    def test_SWAP(self):
        sim = self._getSimulator('''
                B0MOV   RBANK, #1
                MOV     A, #0xf0
                MOV     0x00, A
                SWAP    0x00
                MOV     A, #0
                SWAPM   0x00
        ''')
        state0 = sim.getState()
        sim.step() # B0MOV M, I
        sim.step() # MOV A, I
        sim.step() # MOV M, A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 3, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'A': 0xf0,
                'ram': {
                    sim.addressOf('RBANK'): 0x01,
                    sim.addressOf('PCL'): 0x03,
                    0x0100: 0xf0,
                },
            },
        )
        sim.step() # SWAP
        state2 = sim.getState()
        self.assertEqual(state1['cycle_count'] + 1, state2['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state1, state2,
            {
                'A': 0x0f,
                'ram': {
                    sim.addressOf('PCL'): 0x04,
                },
            },
        )
        sim.step() # MOV A, I
        sim.step() # SWAPM
        state3 = sim.getState()
        self.assertEqual(state2['cycle_count'] + 3, state3['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state2, state3,
            {
                'A': 0x00,
                'ram': {
                    sim.addressOf('PCL'): 0x06,
                    0x0100: 0x0f,
                },
            },
        )

    def _test_logic(self, instruction, expected):
        res0, res1 = expected
        # Operations chosen so that:
        # - at least one produces a 0
        # - the 2 variable results differ for all 3 instructions
        # - all 3 variants are exercised (AM, MA, AI)
        # - for AM and MA variants, result differs from both operands
        sim = self._getSimulator('''
                B0MOV   RBANK, #1
                MOV     A, #0x0f
                MOV     0x00, A
                B0B%(z0)s FZ
                MOV     A, #0xf0
                %(ins)s A, 0x00     ; 0x0f ins 0xf0
                B0B%(z1)s FZ
                MOV     A, #0xfe
                %(ins)s 0x00, A     ; 0x0f ins 0xfe
                B0BCLR  FZ
                MOV     A, #0
                %(ins)s A, #0       ; 0x00 ins 0x00
        ''' % {
            'ins': instruction,
            'z0': 'SET' if res0 else 'CLR',
            'z1': 'SET' if res1 else 'CLR',
        })
        for _ in range(5):
            sim.step()
        state_before = sim.getState()
        sim.step()
        state_after = sim.getState()
        self.assertEqual(state_before['cycle_count'] + 1, state_after['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state_before, state_after,
            {
                'A': res0,
                'ram': {
                    sim.addressOf('PCL'): 0x06,
                    sim.addressOf('PFLAG'): 0x80 | (0 if res0 else 1),
                },
            },
        )
        sim.step()
        sim.step()
        state_before = sim.getState()
        sim.step()
        state_after = sim.getState()
        self.assertEqual(state_before['cycle_count'] + 2, state_after['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state_before, state_after,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x09,
                    sim.addressOf('PFLAG'): 0x80 | (0 if res1 else 1),
                    0x100: res1,
                },
            },
        )
        sim.step()
        sim.step()
        state_before = sim.getState()
        sim.step()
        state_after = sim.getState()
        self.assertEqual(state_before['cycle_count'] + 1, state_after['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state_before, state_after,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x0c,
                    sim.addressOf('PFLAG'): 0x81, # FZ set
                },
            },
        )

    def test_logic(self):
        self._test_logic('AND', (0x00, 0x0e))
        self._test_logic('OR ', (0xff, 0xff))
        self._test_logic('XOR', (0xff, 0xf1))

    def test_rotor(self):
        sim = self._getSimulator('''
                B0MOV   RBANK, #1
                MOV     A, #0xa5
                MOV     0x00, A
                RLC     0x00
                MOV     A, #0
                B0BCLR  FC
                RLCM    0x00
                RRC     0x00
                MOV     A, #0
                B0BSET  FC
                RRCM    0x00
        ''')
        sim.step() # MOV M, I
        sim.step() # MOV A, I
        sim.step() # MOV M, A
        state0 = sim.getState()
        sim.step() # RLC M
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'A': 0x4a,
                'ram': {
                    sim.addressOf('PCL'): 0x04,
                    sim.addressOf('PFLAG'): 0x84, # FC set
                },
            },
        )
        sim.step() # MOV A, I
        sim.step() # B0BCLR FC
        state2 = sim.getState()
        sim.step() # RLCM M
        state3 = sim.getState()
        self.assertEqual(state2['cycle_count'] + 2, state3['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state2, state3,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x07,
                    sim.addressOf('PFLAG'): 0x84, # FC set
                    0x100: 0x4a,
                },
            },
        )
        sim.step() # RRC M
        state4 = sim.getState()
        self.assertEqual(state3['cycle_count'] + 1, state4['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state3, state4,
            {
                'A': 0xa5,
                'ram': {
                    sim.addressOf('PCL'): 0x08,
                    sim.addressOf('PFLAG'): 0x80, # FC clear
                },
            },
        )
        sim.step() # MOV A, I
        sim.step() # B0BSET FC
        state5 = sim.getState()
        sim.step() # RRCM M
        state6 = sim.getState()
        self.assertEqual(state5['cycle_count'] + 2, state6['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state5, state6,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x0b,
                    sim.addressOf('PFLAG'): 0x80, # FC clear
                    0x100: 0xa5
                },
            },
        )

    def test_ADD_ADC_SUB_SBC(self):
        sim = self._getSimulator('''
                B0MOV   RBANK, #1
                ; {ADD,SUB} A, I without carry but with nibble carry
                MOV     A, #0xef
                ADD     A, #0x01
                SUB     A, #0x01
                ; {ADD,SUB} A, I with carry but without nibble carry
                MOV     A, #0xf0
                ADD     A, #0x10
                SUB     A, #0x10

                MOV     A, #0x01
                MOV     0x00, A
                ; ADC A, M
                B0MOV   PFLAG, #0x84
                MOV     A, #0xfe
                ADC     A, 0x00
                ; ADC M, A
                B0MOV   PFLAG, #0x84
                MOV     A, #0xfe
                ADC     0x00, A

                MOV     A, #0x01
                MOV     0x00, A
                ; ADD A, M
                B0MOV   PFLAG, #0x84
                MOV     A, #0xfe
                ADD     A, 0x00
                ; ADD M, A
                B0MOV   PFLAG, #0x84
                MOV     A, #0xfe
                ADD     0x00, A

                MOV     A, #0x01
                MOV     0x00, A
                ; SBC A, M
                B0MOV   PFLAG, #0x81
                MOV     A, #0x00
                SBC     A, 0x00
                ; SBC M, A
                B0MOV   PFLAG, #0x81
                MOV     A, #0x00
                SBC     0x00, A

                MOV     A, #0x01
                MOV     0x00, A
                ; SUB A, M
                B0MOV   PFLAG, #0x81
                MOV     A, #0x00
                SUB     A, 0x00
                ; SUB M, A
                B0MOV   PFLAG, #0x81
                MOV     A, #0x00
                SUB     0x00, A
        ''')
        sim.step() # B0MOV M, I

        sim.step() # MOV A, I
        state0 = sim.getState()
        sim.step() # ADD A, I
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'A': 0xf0,
                'ram': {
                    sim.addressOf('PCL'): 0x03,
                    sim.addressOf('PFLAG'): 0x82, # FC clear, FDC set, FZ clear
                },
            },
        )
        sim.step() # SUB A, I
        state2 = sim.getState()
        self.assertEqual(state1['cycle_count'] + 1, state2['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state1, state2,
            {
                'A': 0xef,
                'ram': {
                    sim.addressOf('PCL'): 0x04,
                    sim.addressOf('PFLAG'): 0x84, # FC set, FDC clear, FZ clear
                },
            },
        )

        sim.step() # MOV A, I
        state0 = sim.getState()
        sim.step() # ADD A, I
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'A': 0x00,
                'ram': {
                    sim.addressOf('PCL'): 0x06,
                    sim.addressOf('PFLAG'): 0x85, # FC set, FDC clear, FZ set
                },
            },
        )
        sim.step() # SUB A, I
        state2 = sim.getState()
        self.assertEqual(state1['cycle_count'] + 1, state2['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state1, state2,
            {
                'A': 0xf0,
                'ram': {
                    sim.addressOf('PCL'): 0x07,
                    sim.addressOf('PFLAG'): 0x82, # FC clear, FDC set, FZ clear
                },
            },
        )

        sim.step() # MOV A, I
        sim.step() # MOV M, A
        sim.step() # MOV M, I
        sim.step() # MOV A, I
        state0 = sim.getState()
        sim.step() # ADC A, M
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'A': 0x00,
                'ram': {
                    sim.addressOf('PCL'): 0x0c,
                    sim.addressOf('PFLAG'): 0x87, # FC set, FDC set, FZ set
                },
            },
        )
        sim.step() # MOV M, I
        sim.step() # MOV A, I
        state0 = sim.getState()
        sim.step() # ADC M, A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x0f,
                    sim.addressOf('PFLAG'): 0x87, # FC set, FDC set, FZ set
                    0x100: 0x00,
                },
            },
        )

        sim.step() # MOV A, I
        sim.step() # MOV M, A
        sim.step() # MOV M, I
        sim.step() # MOV A, I
        state0 = sim.getState()
        sim.step() # ADD M, A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'A': 0xff,
                'ram': {
                    sim.addressOf('PCL'): 0x14,
                    sim.addressOf('PFLAG'): 0x80, # FC clear, FDC clear, FZ clear
                },
            },
        )
        sim.step() # MOV M, I
        sim.step() # MOV A, I
        state0 = sim.getState()
        sim.step() # ADD M, A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x17,
                    sim.addressOf('PFLAG'): 0x80, # FC clear, FDC clear, FZ clear
                    0x100: 0xff,
                },
            },
        )

        sim.step() # MOV A, I
        sim.step() # MOV M, A
        sim.step() # MOV M, I
        sim.step() # MOV A, I
        state0 = sim.getState()
        sim.step() # SBC A, M
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'A': 0xfe,
                'ram': {
                    sim.addressOf('PCL'): 0x01c,
                    sim.addressOf('PFLAG'): 0x80, # FC clear, FDC clear, FZ clear
                },
            },
        )
        sim.step() # MOV M, I
        sim.step() # MOV A, I
        state0 = sim.getState()
        sim.step() # SBC M, A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x1f,
                    sim.addressOf('PFLAG'): 0x80, # FC clear, FDC clear, FZ clear
                    0x100: 0xfe,
                },
            },
        )

        sim.step() # MOV A, I
        sim.step() # MOV M, A
        sim.step() # MOV M, I
        sim.step() # MOV A, I
        state0 = sim.getState()
        sim.step() # SUB M, A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'A': 0xff,
                'ram': {
                    sim.addressOf('PCL'): 0x24,
                    sim.addressOf('PFLAG'): 0x80, # FC clear, FDC clear, FZ clear
                },
            },
        )
        sim.step() # MOV M, I
        sim.step() # MOV A, I
        state0 = sim.getState()
        sim.step() # SUB M, A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x27,
                    sim.addressOf('PFLAG'): 0x80, # FC clear, FDC clear, FZ clear
                    0x100: 0xff,
                },
            },
        )

    def test_jumpTable(self):
        sim = self._getSimulator('''
                MOV     A, #0x05
                B0ADD   PCL, A
        ''')
        sim.step() # MOV A, I
        state0 = sim.getState()
        sim.step() # B0ADD PCL, A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x07,
                },
            },
        )

    def test_conditionals(self):
        sim = self._getSimulator('''
                B0MOV   RBANK, #1

                MOV     A, #0x02    ; for CMPRS
                MOV     0x02, A
                MOV     A, #0x01
                MOV     0x01, A
                MOV     A, #0x00
                MOV     0x00, A
                MOV     A, #0xfe    ; for INCS/INCMS
                MOV     0x03, A
                MOV     A, #0xff
                MOV     0x04, A
                MOV     A, #0x02    ; for DECS/DECMS
                MOV     0x05, A
                MOV     A, #0x01
                MOV     0x06, A
                MOV     A, #0x02    ; for BTS0/BTS1
                MOV     0x07, A
                B0MOV   0x08, A

                B0BSET  FZ
                MOV     A, #0x01
                CMPRS   A, #0x02
                NOP
                CMPRS   A, #0x01
                JMP     $       ; it's a trap !
                CMPRS   A, #0x00
                NOP
                CMPRS   A, 0x02
                NOP
                CMPRS   A, 0x01
                JMP     $       ; it's a trap !
                CMPRS   A, 0x00
                NOP

                INCS    0x03
                NOP
                INCS    0x04
                JMP     $       ; it's a trap !
                INCMS   0x03
                NOP
                INCMS   0x03
                JMP     $       ; it's a trap !

                DECS    0x05
                NOP
                DECS    0x06
                JMP     $       ; it's a trap !
                DECMS   0x05
                NOP
                DECMS   0x05
                JMP     $       ; it's a trap !

                BTS0    0x07.0
                JMP     $       ; it's a trap !
                BTS0    0x07.1
                NOP
                BTS1    0x07.0
                NOP
                BTS1    0x07.1
                JMP     $       ; it's a trap !

                B0BTS0  0x08.0
                JMP     $       ; it's a trap !
                B0BTS0  0x08.1
                NOP
                B0BTS1  0x08.0
                NOP
                B0BTS1  0x08.1
                JMP     $       ; it's a trap !
        ''')
        for _ in range(20):
            sim.step()

        state0 = sim.getState()
        sim.step() # CMPRS A, I
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x15,
                    sim.addressOf('PFLAG'): 0x80, # FC clear, FZ clear
                },
            },
        )
        sim.step() # NOP
        state0 = sim.getState()
        sim.step() # CMPRS A, I
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x18,
                    sim.addressOf('PFLAG'): 0x85, # FC set, FZ set
                },
            },
        )
        state0 = sim.getState()
        sim.step() # CMPRS A, I
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x19,
                    sim.addressOf('PFLAG'): 0x84, # FC set, FZ clear
                },
            },
        )
        sim.step() # NOP
        state0 = sim.getState()
        sim.step() # CMPRS A, M
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x1b,
                    sim.addressOf('PFLAG'): 0x80, # FC clear, FZ clear
                },
            },
        )
        sim.step() # NOP
        state0 = sim.getState()
        sim.step() # CMPRS A, M
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x1e,
                    sim.addressOf('PFLAG'): 0x85, # FC set, FZ set
                },
            },
        )
        state0 = sim.getState()
        sim.step() # CMPRS A, M
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x1f,
                    sim.addressOf('PFLAG'): 0x84, # FC set, FZ clear
                },
            },
        )
        sim.step() # NOP


        state0 = sim.getState()
        sim.step() # INCS A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'A': 0xff,
                'ram': {
                    sim.addressOf('PCL'): 0x21,
                },
            },
        )
        sim.step() # NOP
        state0 = sim.getState()
        sim.step() # INCS A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'A': 0x00,
                'ram': {
                    sim.addressOf('PCL'): 0x24,
                },
            },
        )
        state0 = sim.getState()
        sim.step() # INCMS A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x25,
                    0x103: 0xff,
                },
            },
        )
        sim.step() # NOP
        state0 = sim.getState()
        sim.step() # INCMS A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 3, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x28,
                    0x103: 0x00,
                },
            },
        )

        state0 = sim.getState()
        sim.step() # DECS A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'A': 0x01,
                'ram': {
                    sim.addressOf('PCL'): 0x29,
                },
            },
        )
        sim.step() # NOP
        state0 = sim.getState()
        sim.step() # DECS A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'A': 0x00,
                'ram': {
                    sim.addressOf('PCL'): 0x2c,
                },
            },
        )
        state0 = sim.getState()
        sim.step() # DECMS A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x2d,
                    0x105: 0x01,
                },
            },
        )
        sim.step() # NOP
        state0 = sim.getState()
        sim.step() # DECMS A
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 3, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x30,
                    0x105: 0x00,
                },
            },
        )

        state0 = sim.getState()
        sim.step() # BTS0 M.b
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x32,
                },
            },
        )
        state0 = sim.getState()
        sim.step() # BTS0 M.b
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x33,
                },
            },
        )
        sim.step() # NOP
        state0 = sim.getState()
        sim.step() # BTS1 M.b
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x35,
                },
            },
        )
        sim.step() # NOP
        state0 = sim.getState()
        sim.step() # BTS1 M.b
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x38,
                },
            },
        )

        state0 = sim.getState()
        sim.step() # B0BTS0 M.b
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x3a,
                },
            },
        )
        state0 = sim.getState()
        sim.step() # B0BTS0 M.b
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x3b,
                },
            },
        )
        sim.step() # NOP
        state0 = sim.getState()
        sim.step() # B0BTS1 M.b
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 1, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x3d,
                },
            },
        )
        sim.step() # NOP
        state0 = sim.getState()
        sim.step() # B0BTS1 M.b
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertStrippedDifferenceEqual(
            state0, state1,
            {
                'ram': {
                    sim.addressOf('PCL'): 0x40,
                },
            },
        )

if __name__ == '__main__':
    unittest.main()
