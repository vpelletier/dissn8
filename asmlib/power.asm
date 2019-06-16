; Copyright (C) 2019  Vincent Pelletier <plr.vincent@gmail.com>
;
; This program is free software; you can redistribute it and/or
; modify it under the terms of the GNU General Public License
; as published by the Free Software Foundation; either version 2
; of the License, or (at your option) any later version.
;
; This program is distributed in the hope that it will be useful,
; but WITHOUT ANY WARRANTY; without even the implied warranty of
; MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
; GNU General Public License for more details.
;
; You should have received a copy of the GNU General Public License
; along with this program; if not, write to the Free Software
; Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

; Power control functions
; - Fslow = Flosc / 2 (slower than necessary otherwise, but functional)
; - max stack depth: 1, including incomming call

.CODE
; Stop all clocks and all timers. Returns to normal mode on wake-up.
; Wake-up sources: P0, P1, reset
power_sleep: ; entry: normal or slow mode.
             ; exit: normal mode.
             ; modifies: (nil)
        B0BSET  FCPUM0
        RET             ; XXX: likely unreachable when woken by reset

; Stop CPU. Returns to previous mode on wake-up.
; Wake-up soures: P0, P1, reset, T0
power_green: ; entry: normal or slow mode
             ; exit: same as entry
             ; modifies (nil)
        B0BSET  FCPUM1
        RET

; Switches cpu clock to slow source, optionally stopping fast clock.
power_slow: ; in: FC
            ; entry: normal mode
            ; exit: slow mode, if FC is set fast clock is stopped
            ; modifies: (nil)
        B0BSET  FCLKMD
        B0BTS0  FC
        B0BSET  FSTPHX
        RET

; Switches fast clock on (if not already on) and use it as cpu clock source.
power_normal: ; in: (nil)
              ; entry: slow mode
              ; exit: normal mode, if fast clock was not running it is started
              ;       and stabilised
              ; modifies: R if fast clock was stopped, in which case this must
              ;           be called with RBANK=0
        B0BTS1  FSTPHX
        JMP     _fast_clock_running
        B0BCLR  FSTPHX
        ; sleep for 3 + 19 * 3 cycles, 10ms at Flosc/2 and 20ms at Flosc/4
        B0MOV   R, #19
@@:
        JMP     $+1
        DECMS   R
        JMP     @B
_fast_clock_running:
        B0BCLR  FCLKMD
        RET
