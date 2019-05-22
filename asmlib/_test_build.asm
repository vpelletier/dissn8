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

; Just a way to check all of asmlib play along, and maybe to debug it all.

INCLUDE "on_chip_flasher.asm"
CHIP SN8F2288

//{{SONIX_CODE_OPTION
        .Code_Option    Fcpu            "Fosc/4"
        .Code_Option    High_CLK        "12M_X'tal"
        .Code_Option    LVD             "LVD_M"
        .Code_Option    Watch_Dog       "Enable"
        .Code_Option    Security        "Disable"
//}}SONIX_CODE_OPTION

.DATA
watchdog_reset        EQU     flasher
; I²C pin assignment, as needed by i2c.asm
sda                   EQU     P2.0
sda_dir               EQU     P2M.0
scl                   EQU     P2.1
scl_dir               EQU     P2M.1
; Own declarations
_24c32_read_address   EQU     010100001b
_24c32_write_address  EQU     010100000b

.CODE
ORG 0x0008
        RETI

ORG start
        INCLUDE "init.asm"
power_on_reset:
external_reset:
        ; Watchdog triggers after 682ms.
        ; Below code sends 8 bytes (8*9 bits) and 3 start/stop conditions at a
        ; bit under 100kbps.
        ; (8 * 9 + 3) / 100kHz = 0.83ms
        ; It should take a lot of overhead and clock stretching to reach
        ; anywhere near watchdog timeout, so refresh it once only.
        INCLUDE "watchdog.asm"
        ; read EEPROM from 0x0000 to 0x0004, store in ram 0x00 to 0x04 of
        ; current bank:
        ; start, write device write address, write both eeprom address bytes,
        ; restart, write device read address, read 4 bytes.
        CALL    i2c_init
        CALL    i2c_start
        B0MOV   R, #_24c32_write_address
        CALL    i2c_write_byte            ; I²C address byte
        B0BTS0  FC
        JMP     _end
        B0MOV   R, #0
        CALL    i2c_write_byte            ; EEPROM address byte 0
        B0BTS0  FC
        JMP     _end
        B0MOV   R, #0
        CALL    i2c_write_byte            ; EEPROM address byte 1
        B0BTS0  FC
        JMP     _end
        CALL    i2c_start                 ; restart
        B0MOV   R, #_24c32_write_address
        CALL    i2c_write_byte            ; I²C address byte
        B0BTS0  FC
        JMP     _end
        CALL    i2c_read_byte             ; read data byte 0
        B0MOV   A, R
        MOV     0x00, A
        CALL    i2c_ack
        CALL    i2c_read_byte             ; read data byte 1
        B0MOV   A, R
        MOV     0x01, A
        CALL    i2c_ack
        CALL    i2c_read_byte             ; read data byte 2
        B0MOV   A, R
        MOV     0x02, A
        CALL    i2c_ack
        CALL    i2c_read_byte             ; read data byte 3
        B0MOV   A, R
        MOV     0x03, A
        CALL    i2c_nak

_end:
        CALL    i2c_stop
        JMP     flasher

usb_get_descriptor_address_and_length:
usb_set_configuration:
usb_get_interface:
usb_set_interface:
        B0BSET  FC
        RET

usb_on_ep0_in:
usb_on_ep0_out:
        JMP     usb_stall_ep0

usb_on_crc_err:
usb_on_pkt_err:
usb_on_reset:
usb_on_sof:
usb_on_suspend:
        RET

usb_on_setupdata:
        JMP     usb_deferred_stall_ep0

INCLUDE "i2c.asm"
INCLUDE "usb.asm"
INCLUDE "power.asm"
