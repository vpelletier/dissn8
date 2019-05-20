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

; Chip state initialisation
; Should be included at firmware entry point
; - alters: A
.CODE
        MOV     A, #00000111b   ; As per datasheet §2.3.2
        B0MOV   STKP, A         ;



        B0BTS0  FNT0
        JMP     @F
        B0BTS1  FNPD
        JMP     watchdog_reset  ; /NT0 & /NPD
        ; reserved, fall-through: /NT0 & NPD (going to external_reset)
@@:
        B0BTS1  FNPD
        JMP     power_on_reset  ; NT0 & /NPD
        JMP     external_reset  ; NT0 & NPD

