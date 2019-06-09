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
; - alters A, R, FC
; - bank-safe
;
; Input parameters:
; sda:     bank zero bit address of SDA signal
; sda_dir: bank zero bit address of SDA direction control (1=output, 0=input)
; scl:     bank zero bit address of SCL signal
; scl_dir: bank zero bit address of SCL direction control (1=output, 0=input)
; Main application is expected to initialise:
; - sda and scl to 0
; - sda_dir and scl_dir to input (0)

.CODE
; Each JMP is 2 cycles. CALL and RET are also 2 cycles each.
_i2c_delay_5_0: ; 5.0µs@3MHz = 15.0 cycles
_i2c_delay_4_7: ; 4.7µs@3MHz = 14.1
                JMP $+1
_i2c_delay_4_0: ; 4.0µs@3MHz = 12.0 cycles
                JMP $+1
                JMP $+1
                JMP $+1
                JMP $+1
                RET

_i2c_wait_scl_high: ; modifies: (nil)
                    ; line state in: scl=- sda=-
                    ; line state out: scl=HZ sda=-
        B0BCLR  scl_dir
@@:
        B0BTS1  scl
        JMP     @B
        RET

_i2c_recv_r0:       ; modifies: A, R
                    ; line state in: scl=- sda=HZ
                    ; line state out: scl=0 sda=HZ
        B0MOV   A, R
        B0ADD   R, A
        CALL    _i2c_delay_4_0        ; tLOW is 4.7µs, the difference was taken
                                      ; by above B0MOV B0ADD instructions
        B0BSET  R.0
        CALL    _i2c_wait_scl_high
        CALL    _i2c_delay_4_0  ; tHIGH
        B0BTS1  sda
        B0BCLR  R.0
        B0BSET  scl_dir
        RET

_i2c_send_r7:       ; modifies: A, R
                    ; line state in: scl=- sda=-
                    ; line state out: scl=0 sda=-
        B0BCLR  sda_dir
        B0BTS1  R.7
        B0BSET  sda_dir
        B0MOV   A, R
        B0ADD   R, A
        ; tSU;DAT is 0.75 cycles, CALL takes 2: no wait needed.
        ; fall through
_i2c_clock_bit_out: ; modifies: -
                    ; line state in: scl=- sda=-
                    ; line state out: scl=0 sda=-
        CALL    _i2c_wait_scl_high
        CALL    _i2c_delay_4_0        ; tHIGH
        B0BSET  scl_dir
        JMP     _i2c_delay_5_0        ; tHD;DAT, CALL + RET

; Write 8 bits to bus. Returns whether device acked.
i2c_write_byte: ; in: R
                ; line state in: scl=0 sda=-
                ; line state out: scl=0 sda=HZ
                ; modifies: A, R
                ; result: R.0 (0=ack, 1=nack)
        B0BSET  sda_dir
        CALL    _i2c_send_r7          ; bit 7
        CALL    _i2c_send_r7          ; bit 6
        CALL    _i2c_send_r7          ; bit 5
        CALL    _i2c_send_r7          ; bit 4
        CALL    _i2c_send_r7          ; bit 3
        CALL    _i2c_send_r7          ; bit 2
        CALL    _i2c_send_r7          ; bit 1
        CALL    _i2c_send_r7          ; bit 0
        B0BCLR  sda_dir
        ; _i2c_recv_r0 starts with tLOW, which is >tVD;ACK
        CALL    _i2c_recv_r0
        RET

; Read 8 bits from bus. Does not ack/nack.
i2c_read_byte: ; line state in: scl=0 sda=-
               ; line state out: scl=0 sda=HZ
               ; modifies: A, R
               ; result: R
        B0BCLR  sda_dir
        CALL    _i2c_recv_r0          ; bit 7
        CALL    _i2c_recv_r0          ; bit 6
        CALL    _i2c_recv_r0          ; bit 5
        CALL    _i2c_recv_r0          ; bit 4
        CALL    _i2c_recv_r0          ; bit 3
        CALL    _i2c_recv_r0          ; bit 2
        CALL    _i2c_recv_r0          ; bit 1
        CALL    _i2c_recv_r0          ; bit 0
        RET

; Ack read byte
i2c_ack: ; line state in: scl=0, sda=-
         ; line state out: scl=0, sda=0
         ; modifies: -
        B0BSET  sda_dir
        JMP     _i2c_clock_bit_out ; CALL + RET

; Nak read byte
i2c_nak: ; line state in: scl=0, sda=-
         ; line state out: scl=0, sda=HZ
         ; modifies: -
        B0BCLR  sda_dir
        JMP     _i2c_clock_bit_out ; CALL + RET

; (Re)Start condition
i2c_start: ; line state in: scl=-, sda=HZ
           ; line state out: scl=0, sda=0
           ; modifies: -
        B0BTS1  scl_dir
        JMP     @F                    ; start
        B0BCLR  scl_dir
        CALL    _i2c_wait_scl_high    ; restart
        CALL    _i2c_delay_4_7        ; tSU;STA
@@:
        B0BSET  sda_dir
        CALL    _i2c_delay_4_0        ; tHD;STA
        B0BSET  scl_dir
        RET

; Stop condition
i2c_stop: ; line state in: scl=0, sda=-
          ; line state out: scl=HZ, sda=HZ
          ; modifies: -
        B0BSET  sda_dir
        CALL    _i2c_delay_5_0        ; tHD;DAT ?
        CALL    _i2c_wait_scl_high
        CALL    _i2c_delay_4_0        ; tSU;STO
        B0BCLR  sda_dir
;        CALL    _i2c_delay_4_7        ; tBUF
        RET
