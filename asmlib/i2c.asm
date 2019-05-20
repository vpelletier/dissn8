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

; Bit-banging I2C master primitives
; - Fcpu = Fosc/4 (3MHz)
; - <100kHz
; - start, restart, read8, write8, ack, nak, stop
; - bit-level clock stretching
; - no arbitration
; - max stack depth: 3, including incomming call.
; - alters R, Y, Z, FC
;
; Input parameters:
; sda:     bank zero bit address of SDA signal
; sda_dir: bank zero bit address of SDA direction control (1=output, 0=input)
; scl:     bank zero bit address of SCL signal
; scl_dir: bank zero bit address of SCL direction control (1=output, 0=input)

.DATA
; constants
_I2C_DELAY_4_0US    EQU   2 ; 4.0µs@3MHz = 12.0 cycles = 6 + 3 * 2
_I2C_DELAY_4_7US    EQU   3 ; 4.7µs@3MHz = 14.1 cycles = 6 + 3 * 3 - 0.9
_I2C_DELAY_5_0US    EQU   3 ; 5.0µs@3MHz = 15.0 cycles = 6 + 3 * 3

.CODE
_i2c_delay: ; modifies: Z
            ; Fixed delay: CALL + RET + B0MOV + DECMS exit = 6 cycles
            ; Variable delay: Z * (DECMS + JMP) = Z * 3 cycles
        DECMS   Z
        JMP     _i2c_delay
        RET

_i2c_wait_scl_high: ; modifies: (nil)
                    ; line state in: scl=- sda=-
                    ; line state out: scl=HZ sda=-
        B0BCLR  scl_dir
@@:
        B0BTS1  scl
        JMP     @B
        RET

_i2c_clock_bit_in: ; modifies: Z
                   ; line state in: scl=- sda=-
                   ; line state out: scl=HZ sda=-
        CALL    _i2c_wait_scl_high
        B0MOV   Z, #_I2C_DELAY_4_0US  ; tHIGH
        JMP     _i2c_delay ; CALL + RET

_i2c_clock_bit_out: ; modifies: Z
                    ; line state in: scl=- sda=-
                    ; line state out: scl=0 sda=-
        ; Same as _i2c_clock_bit_in ...
        CALL    _i2c_wait_scl_high
        B0MOV   Z, #_I2C_DELAY_4_0US  ; tHIGH
        CALL    _i2c_delay
        ; ... until here
        B0BSET  scl_dir
        B0MOV   Z, #_I2C_DELAY_5_0US  ; tHD;DAT
        JMP     _i2c_delay ; CALL + RET

; Write 8 bits to bus. Returns whether device acked.
i2c_write_byte: ; in: R
                ; line state in: scl=0 sda=-
                ; line state out: scl=0 sda=HZ
                ; modifies: R, Y, Z
                ; result: C (0=ack, 1=nack)
        B0BSET  sda_dir
        B0MOV   Y, #8                 ; 8 bits to go
@@:
        RLCM    R
        B0BCLR  sda_dir
        B0BTS1  FC
        B0BSET  sda_dir
        ; tSU;DAT is 0.75 cycles, CALL takes 2: no wait needed.
        CALL    _i2c_clock_bit_out
        DECMS   Y
        JMP     @B
        B0BCLR  sda_dir
        B0MOV   Z, #_I2C_DELAY_4_0US  ; >tVD;ACK
        CALL    _i2c_delay
        B0BSET  FC
        CALL    _i2c_clock_bit_in
        B0BTS1  sda
        B0BCLR  FC
        B0BSET  scl_dir
        RET

; Read 8 bits from bus. Does not ack/nack.
i2c_read_byte: ; line state in: scl=0 sda=-
               ; line state out: scl=0 sda=HZ
               ; modifies: Y, Z
               ; result: R
        B0BCLR  sda_dir
        B0MOV   Y, #8                 ; 8 bits to go
@@:
        B0MOV   Z, #_I2C_DELAY_4_7US  ; tLOW
        CALL    _i2c_delay
        B0BSET  FC
        CALL    _i2c_clock_bit_in
        B0BTS1  sda
        B0BCLR  FC
        RLCM    R
        B0BSET  scl_dir
        DECMS   Y
        JMP     @B
        RET

; Initialise bus direction & pin value
i2c_init:
        B0BCLR  sda_dir
        B0BCLR  sda
        B0BCLR  scl_dir
        B0BCLR  scl

; Ack read byte
i2c_ack: ; line state in: scl=0, sda=-
         ; line state out: scl=0, sda=0
         ; modifies: Z
        B0BSET  sda_dir
        JMP     _i2c_clock_bit_out ; CALL + RET

; Nak read byte
i2c_nak: ; line state in: scl=0, sda=-
         ; line state out: scl=0, sda=HZ
         ; modifies: Z
        B0BCLR  sda_dir
        JMP     _i2c_clock_bit_out ; CALL + RET

; (Re)Start condition
i2c_start: ; line state in: scl=-, sda=HZ
           ; line state out: scl=0, sda=0
           ; modifies: Z
        B0BCLR  scl_dir
        B0BTS0  scl
        JMP     @F                    ; start
        CALL    _i2c_wait_scl_high    ; restart
        B0MOV   Z, #_I2C_DELAY_4_7US  ; tSU;STA
        CALL    _i2c_delay
@@:
        B0BSET  sda_dir
        B0MOV   Z, #_I2C_DELAY_4_0US  ; tHD;STA
        CALL    _i2c_delay
        B0BSET  scl_dir
        RET

; Stop condition
i2c_stop: ; line state in: scl=0, sda=-
          ; line state out: scl=HZ, sda=HZ
          ; modifies: Z
        B0BSET  sda_dir
        B0MOV   Z, #_I2C_DELAY_5_0US  ; tHD;DAT ?
        CALL    _i2c_delay
        CALL    _i2c_wait_scl_high
        B0MOV   Z, #_I2C_DELAY_4_0US  ; tSU;STO
        CALL    _i2c_delay
        B0BCLR  sda_dir
;        B0MOV   Z, #_I2C_DELAY_4_7US  ; tBUF
;        CALL    _i2c_delay
        RET
