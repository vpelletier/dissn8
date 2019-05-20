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

; Integration with SONIX in-chip flasher tool.
; Exports the following symbols:
; - start: Where firmware execution starts on reset, once canary check passed.
; - flasher: Jump to this address to enter fash mode from firmware.
; - persistent_store: A flash page (0x80 words) which is preserved when
;   flashing. Write from code. Can be used to store settings.
; It also writes the reset vector to jump at the canary checker function, and
; writes the canary.
; Firmware must not put any code beyond 0x2ffe.
;
; WARNING: Canary, on its own, only protects against an interrupted flash.
; If the firmware gets successfully flashed and does not provide a
; reliable way of calling "flasher", it will not be possible to reprogram the
; chip without specialised hardware !
; Also, it may be a good idea to jump to flasher when detecting a watchdog
; reset (NT0 == NPD == 0).

.DATA
start             EQU 0x0010
persistent_store  EQU 0x2800
_canary_checker   EQU 0x2880
flasher           EQU 0x2890

.CODE
ORG 0x0000
        JMP _canary_checker ; jump to canary checker
        DW  0, 0, 0, 0, 0, 0, 0

ORG 0x27ff
        DW  0xaaaa          ; canary

; Host flashing tool stops here. Everything below is for simulation only and
; does not correspond to actual in-chip code.

ORG persistent_store
        DW  0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff
        DW  0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff
        DW  0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff
        DW  0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff
        DW  0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff
        DW  0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff
        DW  0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff
        DW  0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff
        DW  0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff
        DW  0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff
        DW  0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff
        DW  0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff
        DW  0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff
        DW  0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff
        DW  0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff
        DW  0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff, 0xffff

ORG _canary_checker
        JMP start

ORG flasher
        JMP $
