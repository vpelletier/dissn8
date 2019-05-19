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

CHIP SN8F2288

//{{SONIX_CODE_OPTION
	.Code_Option	Fcpu		"Fosc/4"
	.Code_Option	Fslow		"Flosc/2"
	.Code_Option	High_CLK	"12M_X'tal"
	.Code_Option	LVD		"LVD_M"
	.Code_Option	Reset_Pin	"P07"
	.Code_Option	Rst_Length	"No"
	.Code_Option	Security	"Enable"
	.Code_Option	Watch_Dog	"Enable"
//}}SONIX_CODE_OPTION

.DATA
y_bit_0 EQU   Y.0         ; declaration of a bit address
just_y  EQU   Y           ; declaration of a byte address

.CODE
ORG 0 ; decimal
_reset: ; redefining default label at same address
        JMP start ; not-defined-yet label

ORG 0b1000 ; binary, 0b... form
        RETI

ORG 0x10 ; hexadecimal, 0x... form
start:
_non_exported_label:
        B0MOV Y, #0b      ; binary, 0...b form
        B0MOV Z, #07fh    ; hexadecimal, 0...h form 
@@:                       ; anonymous label
        CLR   @YZ
        DECMS Z
        JMP   @B          ; jump to previous anonymous label

        B0MOV just_y, #0x01
        B0MOV Z, #0xff
@@:
        CLR   @YZ
        DECMS Z
        JMP   @B

        BSET  Y.1         ; bit selector on an identifier
        BCLR  y_bit_0     ; bit address identifier
        B0MOV Z, #0xff
@@:
        CLR   @YZ
        DECMS Z
        JMP   @B

        MOV   A, #just_y  ; immediate value from declared identifier

        JMP   $+1         ; 2-cycles, 1-instruction delay
        JMP   label_in_included_file

.ALIGN 32
INCLUDE "0.inc.0.asm"
        JMP   halt_and_catch_fire

.ALIGN 0x20
halt_and_catch_fire:
        JMP   $

.ALIGN 0b00100000
        DW    0xffff, 'F', 'o', 'o', 0x0000
        DB    "Bar", 'B', 'a'
ENDP
