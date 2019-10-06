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

; USB handling helpers.
; Inspired from fx2lib.
; - max stack depth: 2, including incomming call and calls to functions
;   expected from main firmware:
;   - usb_get_device_descriptor_address_and_length
;   - usb_get_configuration_descriptor_address_and_length
;   - usb_get_string_descriptor_address_and_length
;   - usb_set_configuration
;   - usb_get_interface
;   - usb_set_interface
;   - usb_on_suspend
;   - usb_on_reset
;   - usb_on_sof
;   - usb_on_pkt_err
;   - usb_on_crc_err
;   - usb_on_setupdata
;     Called when host initiated a non-standard SETUP request
;     (bmRequestType & 0x60 != 0).
;     Expected to jump to usb_deferred_stall_ep0 if the request cannot be
;     handled, or return otherwise.
;   - usb_on_ep0_out
;     Called for the OUT data stage of a non-standard SETUP request.
;     In the data stage, expected to jump to usb_deferred_stall_ep0 if the
;     data cannot be handled, or return otherwise.
;     In the status stage, expected to jump to usb_stall_ep0 if the request
;     cannot be handled, or to usb_ack_ep0 otherwise.
;   - usb_on_ep0_in
;     Similar to usb_on_ep0_out, but for the IN data stage.
; - tries to stick to standard compliance
; - tries to not rely on host behaving in a strictly standard manner
;   - does rely on host following proper transaction sequence (one OUT & IN max
;     per SETUP transaction, consistently with bmRequestType.7)
; - modifies A, R, Y, Z
; - needs 7 bytes in page zero

.DATA
UDPR0_ADDRESS_BM_REQUEST_TYPE EQU 0
UDPR0_ADDRESS_B_REQUEST       EQU 1
UDPR0_ADDRESS_W_VALUE_L       EQU 2
UDPR0_ADDRESS_W_VALUE_H       EQU 3
UDPR0_ADDRESS_W_INDEX_L       EQU 4
UDPR0_ADDRESS_W_INDEX_H       EQU 5
UDPR0_ADDRESS_W_LENGTH_L      EQU 6
UDPR0_ADDRESS_W_LENGTH_H      EQU 7

_pending_address        DS  1
; Bit 7 is actually FUDE, making it a handy flag to detect SET_ADDRESS(0)
_has_pending_address    EQU _pending_address.7
_bitmap0                DS  1
_remote_wakeup_enabled  EQU _bitmap0.0
_ep0_handoff            EQU _bitmap0.1
_ep0_stall_next_stage   EQU _bitmap0.2
_set_feature            EQU _bitmap0.3 ; 0 for CLEAR_FEATURE, 1 for SET_FEATURE
_setup_data_out         EQU _bitmap0.4 ; 0 for IN data stage + OUT status stage
                                       ; 1 for optional OUT data stage and IN status stage
_data_in_from_flash     EQU _bitmap0.5 ; 0 if IN transfer data is already in buffer
                                       ; 1 if it must be read from flash
usb_data_in_skip_low_byte   EQU _bitmap0.6
_active_configuration       DS 1
usb_descriptor_pointer_l    DS 1
usb_descriptor_pointer_h    DS 1
usb_setup_data_len_l        DS 1
usb_setup_data_len_h        DS 1

USB_DT_DEVICE             EQU 0x01
USB_DT_CONFIG             EQU 0x02
USB_DT_STRING             EQU 0x03
USB_DT_INTERFACE          EQU 0x04
USB_DT_ENDPOINT           EQU 0x05
USB_DT_HID                EQU 0x21
USB_DT_REPORT             EQU 0x22
USB_DT_PHYSICAL           EQU 0x23

.CODE
; Initialises usb-related variables (not registers)
usb_init: ; modifies: A
        MOV       A, #0x00
        B0MOV     _pending_address, A
        B0MOV     _bitmap0, A
        B0MOV     _active_configuration, A
        B0MOV     usb_descriptor_pointer_l, A
        B0MOV     usb_descriptor_pointer_h, A
        B0MOV     usb_setup_data_len_l, A
        B0MOV     usb_setup_data_len_h, A
        RET

; Call to handle one USB EP0 or bus event.
usb_handle: ; modifies: A, R, Y, Z
        B0BTS0    FEP0OUT
        JMP       _handle_ep0_out
        B0BTS0    FEP0IN
        JMP       _handle_ep0_in
        B0BTS0    FEP0SETUP
        JMP       _handle_setupdata
        B0BTS0    FSUSPEND
        JMP       usb_on_suspend
        B0BTS0    FBUS_RST
        JMP       usb_on_reset
        B0BTS0    FSOF
        JMP       usb_on_sof
        B0BTS0    FPKTERR
        JMP       usb_on_pkt_err
        B0BTS0    FCRCERR
        JMP       usb_on_crc_err
        RET

; Deffered stall of EP0
; Should be used in SETUP or DATA stages, so that next DATA or STATUS stage STALLs.
usb_deferred_stall_ep0:
        B0BSET    _ep0_stall_next_stage
usb_ack_ep0:
        MOV       A, #0x20
        B0MOV     UE0R, A
        RET

; Immediate stall of EP0
; Should not be used in SETUP stage, only in DATA or STATUS stages.
usb_stall_ep0:
        B0BSET    FUE0M1
        RET

_handle_ep0_out:
        B0BTS0    _ep0_stall_next_stage
        JMP       usb_stall_ep0
        B0BTS0    _ep0_handoff
        JMP       usb_on_ep0_out
        B0BTS1    _setup_data_out
        JMP       usb_ack_ep0                   ; OUT status stage, ack
        ; OUT data stage not needed for implemented standard requests
        JMP       usb_stall_ep0

_handle_ep0_in:
        B0BTS0    _ep0_stall_next_stage
        JMP       usb_stall_ep0
        B0BTS0    _ep0_handoff
        JMP       usb_on_ep0_in
        B0BTS0    _setup_data_out
        JMP       usb_ack_ep0                   ; IN status stage, ack
        ; IN data stage
        B0BTS0    _data_in_from_flash
        JMP       _load_ep0_buffer_from_flash
_send_ep0_buffer:
        B0MOV     A, UDP0
        AND       A, #0x0f
        OR        A, #0x20
        B0MOV     UE0R, A                   ; ACK
        B0BTS1    _has_pending_address
        RET
        B0MOV     A, _pending_address
        B0MOV     UDA, A                    ; Finalise address change
        B0BCLR    _has_pending_address
        RET
_load_ep0_buffer_from_flash:
        B0MOV     A, usb_descriptor_pointer_h
        B0MOV     Y, A
        B0MOV     A, usb_descriptor_pointer_l
        B0MOV     Z, A
        MOV       A, #0x00
        B0MOV     UDP0, A
        B0BTS0    usb_data_in_skip_low_byte
        MOVC                                ; prime the pump
_load_ep0_buffer_from_flash_loop:
        ; any byte left to send ?
        B0MOV     A, usb_setup_data_len_l
        B0BTS1    FZ
        JMP       @F
        B0MOV     A, usb_setup_data_len_h      ; l is zero, check h
        B0BTS0    FZ
        JMP       _load_ep0_buffer_from_flash_exit ; h & l are zero, loop is over
        MOV       A, #0xff                  ; -1
        B0ADD     usb_setup_data_len_h, A
@@:
        MOV       A, #0xff                  ; -1
        B0ADD     usb_setup_data_len_l, A
        ; which byte should be sent ?
        B0BTS1    usb_data_in_skip_low_byte
        JMP       @F
        MOV       A, #1                     ; move pointer to next word
        B0ADD     Z, A
        B0BTS0    FC
        B0ADD     Y, A
        B0MOV     A, R                      ; and select high byte for write
        JMP       _load_ep0_buffer_from_flash_write_ep0_buf
@@:
        MOVC                                ; read new word & select low byte for write
_load_ep0_buffer_from_flash_write_ep0_buf:
        ; write byte to EP0 buffer and advance its pointer
        B0MOV     UDR0_W, A
        B0MOV     A, UDP0
        ADD       A, #1
        B0MOV     UDP0, A
        ; switch to the other byte for next iteration
        B0BTS0    usb_data_in_skip_low_byte
        JMP       @F
        B0BSET    usb_data_in_skip_low_byte
@@:
        B0BCLR    usb_data_in_skip_low_byte
        ; is there room left in EP0 buffer ?
        CMPRS     A, #8
        JMP       _load_ep0_buffer_from_flash_loop
_load_ep0_buffer_from_flash_exit:
        ; done, update flash pointer for next send
        B0MOV     A, Y
        B0MOV     usb_descriptor_pointer_h, A
        B0MOV     A, Z
        B0MOV     usb_descriptor_pointer_l, A
        JMP       _send_ep0_buffer

_handle_setupdata:
        MOV       A, #0x00
        B0MOV     usb_setup_data_len_l, A
        B0MOV     usb_setup_data_len_h, A
        B0BCLR    _ep0_stall_next_stage
        B0BCLR    _ep0_handoff
        B0BCLR    _setup_data_out
        MOV       A, #UDPR0_ADDRESS_BM_REQUEST_TYPE
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        AND       A, #0x60
        B0BTS0    FZ
        JMP       @F
        B0BSET    _ep0_handoff              ; Non-standard setup request, hand-off to firmware
        JMP       usb_on_setupdata
@@:
        MOV       A, #UDPR0_ADDRESS_B_REQUEST
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        SUB       A, #13
        B0BTS0    FC
        JMP       usb_deferred_stall_ep0        ; bRequest > 12, stall
        B0MOV     A, UDR0_R
        B0ADD     PCL, A
        JMP       _handle_get_status        ; 0
        JMP       _handle_clear_feature     ; 1
        JMP       usb_deferred_stall_ep0    ; 2 reserved
        JMP       _handle_set_feature       ; 3
        JMP       usb_deferred_stall_ep0    ; 4 reserved
        JMP       _handle_set_address       ; 5
        JMP       _handle_get_descriptor    ; 6
        JMP       usb_deferred_stall_ep0    ; 7 SET_DESCRIPTOR (XXX: support ?)
        JMP       _handle_get_configuration ; 8
        JMP       _handle_set_configuration ; 9
        JMP       _handle_get_interface     ; 10
        JMP       _handle_set_interface     ; 11
        JMP       usb_deferred_stall_ep0    ; 12 SYNCH_FRAME, no ISO support
        ; unreachable

_handle_get_status:
        ; All fields must be 0, except wLengthL wich must be 2 and wIndexL
        ; which depends on bmRequestType.
        MOV       A, #UDPR0_ADDRESS_W_VALUE_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_VALUE_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_INDEX_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        CMPRS     A, #2
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_BM_REQUEST_TYPE
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    UDR0_R.7
        JMP       usb_deferred_stall_ep0        ; bmRequestType direction != IN
        AND       A, #0x7f
        SUB       A, #3
        B0BTS0    FC
        JMP       usb_deferred_stall_ep0        ; bmRequestType recipient > 2
        B0MOV     A, UDR0_R
        AND       A, #0x7f
        B0ADD     PCL, A
        JMP       _handle_get_device_status
        JMP       _handle_get_interface_status
        ; handle get endpoint status
        MOV       A, #UDPR0_ADDRESS_W_INDEX_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        ; XXX: ignore endpoint direction bit. There is no easy way to know the
        ; direction of each endpoint (requires peeking at descriptors of active
        ; configuration...). Hand-over to firmware ?
        AND       A, #0x7f
        SUB       A, #5
        B0BTS0    FC
        JMP       usb_deferred_stall_ep0        ; endpoint > 4
        B0MOV     R, #0x00
        B0MOV     A, UDR0_R
        AND       A, #0x7f
        B0ADD     PCL, A
        JMP       _respond_get_status ; EP0 stall should always auto-clear
        JMP       _handle_get_ep1_status
        JMP       _handle_get_ep2_status
        JMP       _handle_get_ep3_status
        ; handle get endpoint 4 status
        B0BTS1    FUE4EN
        JMP       usb_deferred_stall_ep0        ; endpoint is disabled
        B0BTS0    FUE4M1
        B0MOV     R, #0x01
        JMP       _respond_get_status
_handle_get_ep1_status:
        B0BTS1    FUE1EN
        JMP       usb_deferred_stall_ep0        ; endpoint is disabled
        B0BTS0    FUE1M1
        B0MOV     R, #0x01
        JMP       _respond_get_status
_handle_get_ep2_status:
        B0BTS1    FUE2EN
        JMP       usb_deferred_stall_ep0        ; endpoint is disabled
        B0BTS0    FUE2M1
        B0MOV     R, #0x01
        JMP       _respond_get_status
_handle_get_ep3_status:        
        B0BTS1    FUE3EN
        JMP       usb_deferred_stall_ep0        ; endpoint is disabled
        B0BTS0    FUE3M1
        B0MOV     R, #0x01
        JMP       _respond_get_status
_handle_get_device_status:
        MOV       A, #UDPR0_ADDRESS_W_INDEX_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #0
        B0BTS0    _remote_wakeup_enabled
        OR        A, #0x02
        B0MOV     R, A
        JMP       _respond_get_status
_handle_get_interface_status:
        ; interface
        ; XXX: assumes all interfaces exist (should halt on invalid interface
        ; number)
        B0MOV     R, #0
_respond_get_status:
        B0BCLR    FEP0SETUP
        MOV       A, #0
        B0MOV     UDP0, A
        B0MOV     A, R
        B0MOV     UDR0_W, A
        MOV       A, #1
        B0MOV     UDP0, A
        MOV       A, #0
        B0MOV     UDR0_W, A
        MOV       A, #2
        B0MOV     UDP0, A
        JMP       usb_ack_ep0

_handle_clear_feature:
        B0BCLR    _set_feature
_handle_set_clear_feature:
        ; wIndexH, wValueH and wLength must be zero.
        MOV       A, #UDPR0_ADDRESS_W_VALUE_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_INDEX_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        CMPRS     A, #2
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_BM_REQUEST_TYPE
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS0    UDR0_R.7
        JMP       usb_deferred_stall_ep0        ; bmRequestType direction != OUT
        AND       A, #0x7f
        SUB       A, #3
        B0BTS0    FC
        JMP       usb_deferred_stall_ep0        ; bmRequestType recipient > 2
        B0MOV     A, UDR0_R
        AND       A, #0x7f
        B0ADD     PCL, A
        JMP       _handle_set_clear_device_feature
        JMP       usb_deferred_stall_ep0        ; interface: no standard feature
        ; handle clear endpoint feature
        ; wValueL must be 0
        MOV       A, #UDPR0_ADDRESS_W_VALUE_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_INDEX_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        ; XXX: ignore endpoint direction bit. There is no easy way to know the
        ; direction of each endpoint (requires peeking at descriptors of active
        ; configuration...). Hand-over to firmware ?
        AND       A, #0x7f
        SUB       A, #5
        B0BTS0    FC
        JMP       usb_deferred_stall_ep0        ; endpoint > 4
        B0MOV     A, UDR0_R
        AND       A, #0x7f
        B0ADD     PCL, A
        JMP       _handle_set_clear_ep0_stall
        JMP       _handle_set_clear_ep1_stall
        JMP       _handle_set_clear_ep2_stall
        JMP       _handle_set_clear_ep3_stall
        ; handle set/clear endpoint 4 feature
        B0BTS0    _set_feature
        B0BCLR    FUE4M1
        B0BTS1    _set_feature
        B0BSET    FUE4M1
        JMP       _respond_set_clear_feature
_handle_set_clear_ep0_stall:
        B0BTS0    _set_feature
        JMP       usb_deferred_stall_ep0
        JMP       _respond_set_clear_feature
_handle_set_clear_ep1_stall:
        B0BTS0    _set_feature
        B0BCLR    FUE1M1
        B0BTS1    _set_feature
        B0BSET    FUE1M1
        JMP       _respond_set_clear_feature
_handle_set_clear_ep2_stall:
        B0BTS0    _set_feature
        B0BCLR    FUE2M1
        B0BTS1    _set_feature
        B0BSET    FUE2M1
        JMP       _respond_set_clear_feature
_handle_set_clear_ep3_stall:
        B0BTS0    _set_feature
        B0BCLR    FUE3M1
        B0BTS1    _set_feature
        B0BSET    FUE3M1
_respond_set_clear_feature:
        B0BSET    _setup_data_out
        JMP       usb_ack_ep0
_handle_set_clear_device_feature:
        ; wValueL must be 1, test_mode is not supported.
        MOV       A, #UDPR0_ADDRESS_W_VALUE_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        CMPRS     A, #0x01
        JMP       usb_deferred_stall_ep0
        B0BTS0    _set_feature
        B0BCLR    _remote_wakeup_enabled
        B0BTS1    _set_feature
        B0BSET    _remote_wakeup_enabled
        JMP       _respond_set_clear_feature

_handle_set_feature:
        B0BSET    _set_feature
        JMP       _handle_set_clear_feature

_handle_set_address:
        ; All fields must be zero, except UDPR0_ADDRESS_W_VALUE_L which must be 0..127
        MOV       A, #UDPR0_ADDRESS_BM_REQUEST_TYPE
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_VALUE_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_INDEX_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_INDEX_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_VALUE_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS0    UDR0_R.7
        JMP       usb_deferred_stall_ep0
        OR        A, #0x80
        B0MOV     _pending_address, A
        B0BSET    _setup_data_out
        JMP       usb_ack_ep0

_handle_get_descriptor:
        MOV       A, #UDPR0_ADDRESS_BM_REQUEST_TYPE
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        CMPRS     A, #0x80
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_VALUE_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        CMPRS     A, #3
        JMP       @F
        JMP       _usb_get_string_descriptor
@@:
        B0MOV     R, A
        ; Not a string descriptor, wIndex must be zero
        MOV       A, #UDPR0_ADDRESS_W_INDEX_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS0    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_INDEX_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS0    FZ
        JMP       usb_deferred_stall_ep0
        B0MOV     A, R
        CMPRS     A, #2
        JMP       @F
        JMP       _get_configuration_descriptor
@@:
        ; Not a configuration descriptor, wValueL must be zero
        MOV       A, #UDPR0_ADDRESS_W_VALUE_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS0    FZ
        JMP       usb_deferred_stall_ep0
        B0MOV     A, R
        CMPRS     A, #1
        JMP       usb_deferred_stall_ep0
        B0BSET    FC
        ; ABI:
        ; in: (nil)
        ; out: (see usb_get_string_descriptor_address_and_length)
        CALL      usb_get_device_descriptor_address_and_length
        JMP       _handle_get_descriptor_respond
_get_configuration_descriptor:
        MOV       A, #UDPR0_ADDRESS_W_VALUE_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BSET    FC
        ; ABI:
        ; in: A: descriptor index
        ; out: (see usb_get_string_descriptor_address_and_length)
        CALL      usb_get_configuration_descriptor_address_and_length
        JMP       _handle_get_descriptor_respond
_usb_get_string_descriptor:
        MOV       A, #UDPR0_ADDRESS_W_VALUE_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BSET    FC
        ; ABI:
        ; in: A: descriptor index
        ; out: usb_descriptor_pointer_l,h set to in-flash address of first word
        ;      usb_data_in_skip_low_byte  set if low byte of first word must be skipped
        ;      usb_setup_data_len_l,h     set to descriptor length (ignore wLength)
        ;      FC                         clear to ACK (otherwise above values are then ignored and a stall is sent back)
        ; descriptor is expected packed in flash.
        CALL      usb_get_string_descriptor_address_and_length

        ; For ex, to send 0x01, 0x23, 0x45:
        ; dummy_descriptor: DB  0x01, 0x23, 0x45
        ; usb_get_string_descriptor_address_and_length:
        ;       MOV     A, #dummy_descriptor$L
        ;       B0MOV   usb_descriptor_pointer_l, A
        ;       MOV     A, #dummy_descriptor$H
        ;       B0MOV   usb_descriptor_pointer_h, A
        ;       MOV     A, #3
        ;       B0MOV   usb_setup_data_len_l, A
        ;       MOV     A, #0
        ;       B0MOV   usb_setup_data_len_h, A
        ;       B0BCLR  usb_data_in_skip_low_byte
        ;       B0BCLR  FC
        ;       RET

_handle_get_descriptor_respond:
        B0BTS0    FC
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        XOR       A, #0xff
        ADD       A, #1
        ADD       A, usb_setup_data_len_h
        B0BTS0    FC
        JMP       _handle_get_descriptor_done         ; usb_setup_data_len_h < wLengthH: use usb_setup_data_len_h,l
        B0BTS0    FZ
        JMP       @F                                  ; same MSB, check LSB
        B0MOV     A, UDR0_R                           ; wLengthH < usb_setup_data_len_h: use wLengthH,L
        B0MOV     usb_setup_data_len_h, A
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0MOV     usb_setup_data_len_l, A
        JMP       _handle_get_descriptor_done
@@:
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        ; the only important outcome is if wLengthL > usb_setup_data_len_l, a one's complement is enough
        XOR       A, #0xff
        ADD       A, usb_setup_data_len_l
        B0BTS0    FC
        JMP       _handle_get_descriptor_done         ; usb_setup_data_len_l <= wLengthL: use usb_setup_data_len_l
        B0MOV     A, UDR0_R                           ; else, use wLengthL
        B0MOV     usb_setup_data_len_l, A
_handle_get_descriptor_done:
        B0BSET    _data_in_from_flash
        B0BCLR    FEP0SETUP
        JMP       usb_ack_ep0
        
_handle_get_configuration:
        MOV       A, #UDPR0_ADDRESS_BM_REQUEST_TYPE
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        CMPRS     A, #0x80
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_VALUE_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_VALUE_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_INDEX_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_INDEX_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        CMPRS     A, #0x01
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        B0BCLR    FEP0SETUP
        MOV       A, #0
        B0MOV     UDP0, A
        B0MOV     A, _active_configuration
        B0MOV     UDR0_W, A
        MOV       A, #1
        B0MOV     UDP0, A
        JMP       usb_ack_ep0

_handle_set_configuration:
        MOV       A, #UDPR0_ADDRESS_BM_REQUEST_TYPE
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_VALUE_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_INDEX_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_INDEX_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_VALUE_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0MOV     _active_configuration, A
        ; ABI:
        ; in: A contains wValueL
        ; out: FC set to STALL, otherwise ACK
        CALL      usb_set_configuration
        B0BTS0    FC
        JMP       @F
        B0BSET    _setup_data_out
        JMP       usb_ack_ep0
@@:
        MOV       A, #0x00
        B0MOV     _active_configuration, A
        JMP       usb_deferred_stall_ep0

_handle_get_interface:
        MOV       A, #UDPR0_ADDRESS_BM_REQUEST_TYPE
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        CMPRS     A, #0x81
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_VALUE_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_VALUE_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        CMPRS     A, #0x01
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        B0MOV     A, _active_configuration
        B0BTS0    FZ
        JMP       usb_deferred_stall_ep0        ; GET_INTERFACE on unconfigured device
        B0BSET    FC
        ; ABI:
        ; in: (nil)
        ; out: clear FC to ack (default: stall)
        ;      R contains the answer when FC cleared
        CALL      usb_get_interface
        B0BTS0    FC
        JMP       usb_deferred_stall_ep0
        B0BCLR    FEP0SETUP
        MOV       A, #0
        B0MOV     UDP0, A
        B0MOV     A, R
        B0MOV     UDR0_W, A
        MOV       A, #1
        B0MOV     UDP0, A
        JMP       usb_ack_ep0

_handle_set_interface:
        MOV       A, #UDPR0_ADDRESS_BM_REQUEST_TYPE
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        CMPRS     A, #0x01
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        MOV       A, #UDPR0_ADDRESS_W_LENGTH_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0BTS1    FZ
        JMP       usb_deferred_stall_ep0
        B0MOV     A, _active_configuration
        B0BTS0    FZ
        JMP       usb_deferred_stall_ep0        ; SET_INTERFACE on unconfigured device
        B0BSET    FC
        ; ABI:
        ; in: (nil)
        ; out: clear FC to ACK (default: stall)
        CALL      usb_set_interface
        B0BTS0    FC
        JMP       usb_deferred_stall_ep0
        B0BSET    _setup_data_out
        JMP       usb_ack_ep0
