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
from io import BytesIO
import unittest
from .assn8 import assemble
from . import simsn8

EQUAL = object()

def _diffDict(a, b):
    result = {}
    key_set = set(a)
    unique_key_set = key_set.symmetric_difference(b)
    if unique_key_set:
        raise ValueError(a.keys(), b.keys())
    for key in key_set:
        value_diff = diff(a[key], b[key])
        if value_diff is not EQUAL:
            result[key] = value_diff
    if result:
        return result
    return EQUAL

def _diffVector(a, b):
    result = {}
    if len(a) != len(b):
        raise ValueError(len(a), len(b))
    for index, (item_a, item_b) in enumerate(zip(a, b)):
        item_diff = diff(item_a, item_b)
        if item_diff is not EQUAL:
            result[index] = item_b
    if result:
        return result
    return EQUAL

def _diffValue(a, b):
    return EQUAL if a == b else b

_type_diff = {
    dict: _diffDict,
    list: _diffVector,
    tuple: _diffVector,
    int: _diffValue,
    type(None): _diffValue,
}

def diff(a, b):
    diff_a = _type_diff[type(a)]
    diff_b = _type_diff[type(b)]
    if diff_a is not diff_b:
        raise TypeError(type(a), type(b))
    return _type_diff[type(a)](a, b)

class SimSN8F2288Tests(unittest.TestCase):
    @staticmethod
    def _getSimulator(source, watchdog=False):
        return simsn8.SN8F2288(BytesIO(assemble(
            # Boilerplate stuff.
            u'CHIP SN8F2288\n'
            u'//{{SONIX_CODE_OPTION\n'
            u'    .Code_Option Watch_Dog "' + (
                u'Enable' if watchdog else u'Disable'
            ) + u'"\n'
            u'    .Code_Option LVD "LVD_M"\n'
            u'//}}SONIX_CODE_OPTION\n'
            u'.CODE\n'
            u'ORG 0\n' +
            source + '\n'
        )))

    @staticmethod
    def _stripStateTiming(state):
        return {
            x: y
            for x, y in state.iteritems()
            if x not in ('run_time', 'cycle_count', 'slow_clock')
        }

    def assertStrippedDifferenceEqual(self, state0, state1, expected):
        self.assertEqual(
            diff(
                self._stripStateTiming(state0),
                self._stripStateTiming(state1),
            ),
            expected,
        )

    def testJMP(self):
        sim = self._getSimulator(u'JMP $')
        state0 = sim.getState()
        sim.step()
        state1 = sim.getState()
        self.assertEqual(state0['cycle_count'] + 2, state1['cycle_count'])
        self.assertEqual(self._stripStateTiming(state0), self._stripStateTiming(state1))

    def testCALL_RET(self):
        sim = self._getSimulator(u'''
                CALL func
                JMP  $
            ORG 0x1234
            func:
                RET
        ''')
        state0 = sim.getState()
        # CALL
        sim.step()
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
        # RET
        sim.step()
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

    def _testMOV_BSET_BCLR(self, bank):
        sim = self._getSimulator(u'''
                MOV     A, #0x55
                MOV     0x00, A
                B0MOV   0x01, A
                BCLR    0x00.0
                BSET    0x00.1
                B0BCLR  0x01.4
                B0BSET  0x01.5
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

    def testMOV_BSET_BCLR(self):
        self._testMOV_BSET_BCLR(0)
        self._testMOV_BSET_BCLR(1)
        self._testMOV_BSET_BCLR(2)

    def testInterrupt_RETI(self):
        sim = self._getSimulator(u'''
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
        sim = self._getSimulator(u'''
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

if __name__ == '__main__':
    unittest.main()
