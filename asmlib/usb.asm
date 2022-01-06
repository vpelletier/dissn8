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
;   - usb_get_device_descriptor_address
;   - usb_get_configuration_descriptor_address
;   - usb_get_string_descriptor_address
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
;     Expected to jump to usb_stall_ep0 if the request cannot be
;     handled, or return otherwise.
;   - usb_on_ep0_out
;     Called for the OUT data stage of a non-standard SETUP request.
;     In the data stage, expected to jump to usb_stall_ep0 if the
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
; - needs 17 bytes in page zero

.DATA
_UDPR0_ADDRESS_BM_REQUEST_TYPE EQU 0
_UDPR0_ADDRESS_B_REQUEST       EQU 1
_UDPR0_ADDRESS_W_VALUE_L       EQU 2
_UDPR0_ADDRESS_W_VALUE_H       EQU 3
_UDPR0_ADDRESS_W_INDEX_L       EQU 4
_UDPR0_ADDRESS_W_INDEX_H       EQU 5
_UDPR0_ADDRESS_W_LENGTH_L      EQU 6
_UDPR0_ADDRESS_W_LENGTH_H      EQU 7

_pending_address        DS  1
; Bit 7 is actually FUDE, making it a handy flag to detect SET_ADDRESS(0)
_has_pending_address    EQU _pending_address.7
_bitmap0                DS  1
_remote_wakeup_enabled  EQU _bitmap0.0
_ep0_handoff            EQU _bitmap0.1
_set_feature            EQU _bitmap0.2 ; 0 for CLEAR_FEATURE, 1 for SET_FEATURE
_setup_data_out         EQU _bitmap0.3 ; 0 for IN data stage + OUT status stage
                                       ; 1 for optional OUT data stage and IN status stage
_active_configuration       DS 1
usb_descriptor_pointer_l    DS 1
usb_descriptor_pointer_m    DS 1
_usb_setup_data_len_l       DS 1
_usb_setup_data_len_m       DS 1
_scratch                    DS 1 ; for emulating "B0ADD A, Address"
_bytes_to_write             DS 1

usb_setup_request_type      DS 1
usb_setup_request           DS 1
usb_setup_value_l           DS 1
usb_setup_value_h           DS 1
usb_setup_index_l           DS 1
usb_setup_index_h           DS 1
usb_setup_length_l          DS 1
usb_setup_length_h          DS 1

USB_DT_DEVICE             EQU 0x01
USB_DT_CONFIG             EQU 0x02
USB_DT_STRING             EQU 0x03
USB_DT_INTERFACE          EQU 0x04
USB_DT_ENDPOINT           EQU 0x05
USB_DT_HID                EQU 0x21
USB_DT_REPORT             EQU 0x22
USB_DT_PHYSICAL           EQU 0x23

USB_EP0_MAX_PACKET_SIZE   EQU 8

.CODE
; Initialises usb-related variables (not registers)
usb_init: ; modifies: A
        MOV       A, #0x00
        B0MOV     _pending_address, A
        B0MOV     _bitmap0, A
        B0MOV     _active_configuration, A
        B0MOV     usb_descriptor_pointer_l, A
        B0MOV     usb_descriptor_pointer_m, A
        B0MOV     _usb_setup_data_len_l, A
        B0MOV     _usb_setup_data_len_m, A
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
        JMP       _handle_reset
        B0BTS0    FSOF
        JMP       _handle_sof
        B0BTS0    FPKTERR
        JMP       _handle_pkt_err
        B0BTS0    FCRCERR
        JMP       _handle_crc_err
        RET

; Call with number of bytes written to EP0 buffer in A (should be 0..8)
usb_ack_ep0:
        AND       A, #0x0f
        OR        A, #0x20 ; FUE0M0
        B0MOV     UE0R, A
        RET

usb_stall_ep0:
        B0BSET    FUE0M1
        RET

_handle_ep0_out:
        B0BCLR    FEP0OUT
        B0BTS0    _ep0_handoff
        JMP       usb_on_ep0_out
        ; OUT data stage not needed for implemented standard requests
        RET

_handle_ep0_in:
        B0BCLR    FEP0IN
        B0BTS0    _ep0_handoff
        JMP       usb_on_ep0_in
        B0BTS1    _setup_data_out
        ; IN data stage
        JMP       _load_ep0_buffer_from_flash
        ; IN status stage
        B0BTS0    _has_pending_address
        RET
        ; Finalise address change
        B0MOV     A, _pending_address
        B0MOV     UDA, A
        B0BCLR    _has_pending_address
        RET

_load_ep0_buffer_from_flash:
        ; copy data from flash to EP0 buffer for the next IN data stage
        ; usb_descriptor_pointer_{m,l}:
        ;   address of the first word to send (lsB then msB)
        ; _usb_setup_data_len_{m,l}:
        ;   number of bytes to send (in <=8 bytes transactions)
        ; Updates these variables on each call.
        B0MOV     A, usb_descriptor_pointer_m
        B0MOV     Y, A
        B0MOV     A, usb_descriptor_pointer_l
        B0MOV     Z, A
        MOV       A, #0x00
        B0MOV     UDP0, A
        B0MOV     A, _usb_setup_data_len_l
        B0MOV     R, A
        ; _usb_setup_data_len_l -= 8
        MOV       A, #0xf8 ; -8
        B0ADD     _usb_setup_data_len_l, A
        B0BTS1    FC ; if (signed) _usb_setup_data_len_l >= 0
        JMP       @F
        MOV       A, #USB_EP0_MAX_PACKET_SIZE
        JMP       _load_ep0_buffer_from_flash_loop
@@:
        ; _usb_setup_data_len_m -= 1
        MOV       A, #0xff ; -1
        B0ADD     _usb_setup_data_len_m, A
        B0BTS1    FC ; if (signed) _usb_setup_data_len_m >= 0
        JMP       @F
        MOV       A, #USB_EP0_MAX_PACKET_SIZE
        JMP       _load_ep0_buffer_from_flash_loop
@@:
        ; Nothing to send after this call, zero-out _usb_setup_data_len_*
        MOV       A, #0
        B0MOV     _usb_setup_data_len_l, A
        B0MOV     _usb_setup_data_len_m, A
        ; Load original _usb_setup_data_len_l value as number of bytes to send
        B0MOV     A, R
        B0BTS0    FZ ; is there anything to send at all ?
        JMP       _load_ep0_buffer_from_flash_exit
_load_ep0_buffer_from_flash_loop:
        B0MOV     _bytes_to_write, A
@@:
        ; Write 2 bytes to endpoint buffer per iteration.
        ; Load 2 bytes from flash
        MOVC
        ; Write A to buffer
        B0MOV     UDR0_W, A
        ; advance buffer pointer (also used as bytes-to-send counter)
        MOV       A, #1
        B0ADD     UDP0, A
        ; Is there more to write ?
        MOV       A, #0xff                  ; -1
        B0ADD     _bytes_to_write, A
        B0BTS0    FZ
        JMP       _load_ep0_buffer_from_flash_exit
        ; Yes, write R to buffer
        B0MOV     A, R
        B0MOV     UDR0_W, A
        ; advance buffer pointer (also used as bytes-to-send counter)
        MOV       A, #1
        B0ADD     UDP0, A
        ; Is there more to write ?
        MOV       A, #0xff                  ; -1
        B0ADD     _bytes_to_write, A
        B0BTS0    FZ
        JMP       _load_ep0_buffer_from_flash_exit
        ; Yes, advance flash pointer...
        MOV       A, #1
        B0ADD     Z, A
        B0BTS0    FC
        B0ADD     Y, A
        ; ...and go to next iteration
        JMP       @B
_load_ep0_buffer_from_flash_exit:
        ; done, update flash pointer for next send
        B0MOV     A, Y
        B0MOV     usb_descriptor_pointer_m, A
        B0MOV     A, Z
        B0MOV     usb_descriptor_pointer_l, A
        B0MOV     A, UDP0
        JMP       usb_ack_ep0

_handle_setupdata:
        MOV       A, #_UDPR0_ADDRESS_BM_REQUEST_TYPE
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0MOV     usb_setup_request_type, A
        MOV       A, #_UDPR0_ADDRESS_B_REQUEST
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0MOV     usb_setup_request, A
        MOV       A, #_UDPR0_ADDRESS_W_VALUE_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0MOV     usb_setup_value_l, A
        MOV       A, #_UDPR0_ADDRESS_W_VALUE_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0MOV     usb_setup_value_h, A
        MOV       A, #_UDPR0_ADDRESS_W_INDEX_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0MOV     usb_setup_index_l, A
        MOV       A, #_UDPR0_ADDRESS_W_INDEX_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0MOV     usb_setup_index_h, A
        MOV       A, #_UDPR0_ADDRESS_W_LENGTH_L
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0MOV     usb_setup_length_l, A
        MOV       A, #_UDPR0_ADDRESS_W_LENGTH_H
        B0MOV     UDP0, A
        B0MOV     A, UDR0_R
        B0MOV     usb_setup_length_h, A
        B0BCLR    FEP0SETUP

        MOV       A, #0x00
        B0MOV     _usb_setup_data_len_l, A
        B0MOV     _usb_setup_data_len_m, A
        B0BCLR    _ep0_handoff
        B0BCLR    _setup_data_out
        B0MOV     A, usb_setup_request_type
        AND       A, #0x60
        B0BTS0    FZ
        JMP       @F
        B0BSET    _ep0_handoff              ; Non-standard setup request, hand-off to firmware
        JMP       usb_on_setupdata
@@:
        B0MOV     A, usb_setup_request
        SUB       A, #13
        B0BTS0    FC
        JMP       usb_stall_ep0             ; bRequest > 12, stall
        B0MOV     A, usb_setup_request
        B0ADD     PCL, A
        JMP       _handle_get_status        ; 0
        JMP       _handle_clear_feature     ; 1
        JMP       usb_stall_ep0             ; 2 reserved
        JMP       _handle_set_feature       ; 3
        JMP       usb_stall_ep0             ; 4 reserved
        JMP       _handle_set_address       ; 5
        JMP       _handle_get_descriptor    ; 6
        JMP       usb_stall_ep0             ; 7 SET_DESCRIPTOR (XXX: support ?)
        JMP       _handle_get_configuration ; 8
        JMP       _handle_set_configuration ; 9
        JMP       _handle_get_interface     ; 10
        JMP       _handle_set_interface     ; 11
        JMP       usb_stall_ep0             ; 12 SYNCH_FRAME, no ISO support
        ; unreachable

_handle_reset:
        CALL      usb_init
        B0MOV     A, UDA
        AND       A, #0x80
        B0MOV     UDA, A
        MOV       A, #0x00
        CALL      usb_set_configuration     ; return value ignored
        JMP       usb_on_reset
        ; RET stolen from usb_on_reset

_handle_sof:
        CALL      usb_on_sof
        B0BCLR    FSOF
        RET

_handle_pkt_err:
        CALL      usb_on_pkt_err
        B0BCLR    FPKTERR
        RET

_handle_crc_err:
        CALL      usb_on_crc_err
        B0BCLR    FCRCERR
        RET

_handle_get_status:
        ; All fields must be 0, except wLengthL wich must be 2 and wIndexL
        ; which depends on bmRequestType.
        B0MOV     A, usb_setup_value_l
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_value_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_index_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_length_l
        CMPRS     A, #2
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_length_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_request_type
        B0BTS1    usb_setup_request_type.7
        JMP       usb_stall_ep0        ; bmRequestType direction != IN
        AND       A, #0x7f
        SUB       A, #3
        B0BTS0    FC
        JMP       usb_stall_ep0        ; bmRequestType recipient > 2
        B0MOV     A, usb_setup_request_type
        AND       A, #0x7f
        B0ADD     PCL, A
        JMP       _handle_get_device_status
        JMP       _handle_get_interface_status
        ; handle get endpoint status
        B0MOV     A, usb_setup_index_l
        ; XXX: ignore endpoint direction bit. There is no easy way to know the
        ; direction of each endpoint (requires peeking at descriptors of active
        ; configuration...). Hand-over to firmware ?
        AND       A, #0x7f
        SUB       A, #5
        B0BTS0    FC
        JMP       usb_stall_ep0        ; endpoint > 4
        B0MOV     R, #0x00
        ADD       A, #5                ; get A back to UDR0_R & #0x7f
        B0ADD     PCL, A
        JMP       _respond_get_status ; EP0 stall should always auto-clear
        JMP       _handle_get_ep1_status
        JMP       _handle_get_ep2_status
        JMP       _handle_get_ep3_status
        ; handle get endpoint 4 status
        B0BTS1    FUE4EN
        JMP       usb_stall_ep0        ; endpoint is disabled
        B0BTS0    FUE4M1
        B0MOV     R, #0x01
        JMP       _respond_get_status
_handle_get_ep1_status:
        B0BTS1    FUE1EN
        JMP       usb_stall_ep0        ; endpoint is disabled
        B0BTS0    FUE1M1
        B0MOV     R, #0x01
        JMP       _respond_get_status
_handle_get_ep2_status:
        B0BTS1    FUE2EN
        JMP       usb_stall_ep0        ; endpoint is disabled
        B0BTS0    FUE2M1
        B0MOV     R, #0x01
        JMP       _respond_get_status
_handle_get_ep3_status:
        B0BTS1    FUE3EN
        JMP       usb_stall_ep0        ; endpoint is disabled
        B0BTS0    FUE3M1
        B0MOV     R, #0x01
        JMP       _respond_get_status
_handle_get_device_status:
        B0MOV     A, usb_setup_index_l
        B0BTS1    FZ
        JMP       usb_stall_ep0
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
        MOV       A, #0
        B0MOV     UDP0, A
        B0MOV     A, R
        B0MOV     UDR0_W, A
        MOV       A, #1
        B0MOV     UDP0, A
        MOV       A, #0
        B0MOV     UDR0_W, A
        MOV       A, #2
        JMP       usb_ack_ep0

_handle_clear_feature:
        B0BCLR    _set_feature
_handle_set_clear_feature:
        ; wIndexH, wValueH and wLength must be zero.
        B0MOV     A, usb_setup_value_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_index_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_length_l
        CMPRS     A, #2
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_length_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_request_type
        B0BTS0    usb_setup_request_type.7
        JMP       usb_stall_ep0        ; bmRequestType direction != OUT
        AND       A, #0x7f
        SUB       A, #3
        B0BTS0    FC
        JMP       usb_stall_ep0        ; bmRequestType recipient > 2
        B0MOV     A, usb_setup_request_type
        AND       A, #0x7f
        B0ADD     PCL, A
        JMP       _handle_set_clear_device_feature
        JMP       usb_stall_ep0        ; interface: no standard feature
        ; handle clear endpoint feature
        ; wValueL must be 0
        B0MOV     A, usb_setup_value_l
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_index_l
        ; XXX: ignore endpoint direction bit. There is no easy way to know the
        ; direction of each endpoint (requires peeking at descriptors of active
        ; configuration...). Hand-over to firmware ?
        AND       A, #0x7f
        SUB       A, #5
        B0BTS0    FC
        JMP       usb_stall_ep0        ; endpoint > 4
        ADD       A, #5                ; get A back to UDR0_R & #0x7f
        B0BTS0    _set_feature
        JMP       _handle_set_ep_feature
        B0ADD     PCL, A
        JMP       _respond_set_clear_feature
        JMP       _handle_clear_ep1_stall
        JMP       _handle_clear_ep2_stall
        JMP       _handle_clear_ep3_stall
        B0BCLR    FUE4M1
        JMP       _respond_set_clear_feature
_handle_clear_ep1_stall:
        B0BCLR    FUE1M1
        JMP       _respond_set_clear_feature
_handle_clear_ep2_stall:
        B0BCLR    FUE2M1
        JMP       _respond_set_clear_feature
_handle_clear_ep3_stall:
        B0BCLR    FUE3M1
        JMP       _respond_set_clear_feature
_handle_set_ep_feature:
        B0ADD     PCL, A
        JMP       usb_stall_ep0
        JMP       _handle_set_ep1_stall
        JMP       _handle_set_ep2_stall
        JMP       _handle_set_ep3_stall
        B0BSET    FUE4M1
        JMP       _respond_set_clear_feature
_handle_set_ep1_stall:
        B0BSET    FUE1M1
        JMP       _respond_set_clear_feature
_handle_set_ep2_stall:
        B0BSET    FUE2M1
        JMP       _respond_set_clear_feature
_handle_set_ep3_stall:
        B0BSET    FUE3M1
_respond_set_clear_feature:
        B0BSET    _setup_data_out
        MOV       A, #0
        JMP       usb_ack_ep0
_handle_set_clear_device_feature:
        ; wValueL must be 1, test_mode is not supported.
        B0MOV     A, usb_setup_value_l
        CMPRS     A, #0x01
        JMP       usb_stall_ep0
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
        B0MOV     A, usb_setup_request_type
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_value_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_index_l
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_index_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_length_l
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_length_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_value_l
        B0BTS0    usb_setup_value_l.7
        JMP       usb_stall_ep0
        OR        A, #0x80
        B0MOV     _pending_address, A
        B0BSET    _setup_data_out
        MOV       A, #0
        JMP       usb_ack_ep0

_handle_get_descriptor:
        B0MOV     A, usb_setup_request_type
        CMPRS     A, #0x80
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_value_h
        CMPRS     A, #USB_DT_STRING
        JMP       @F
        JMP       _usb_get_string_descriptor
@@:
        B0MOV     R, A
        ; Not a string descriptor, wIndex must be zero
        B0MOV     A, usb_setup_index_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_index_l
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, R
        CMPRS     A, #USB_DT_CONFIG
        JMP       @F
        JMP       _get_configuration_descriptor
@@:
        ; Not a configuration descriptor, wValueL must be zero
        B0MOV     A, usb_setup_value_l
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, R
        CMPRS     A, #USB_DT_DEVICE
        JMP       usb_stall_ep0
        B0BSET    FC
        ; ABI:
        ; in: (nil)
        ; out: (see usb_get_string_descriptor_address)
        CALL      usb_get_device_descriptor_address
        JMP       _get_descriptor_length
_get_configuration_descriptor:
        B0MOV     A, usb_setup_value_l
        B0BSET    FC
        ; ABI:
        ; in: A: descriptor index
        ; out: (see usb_get_string_descriptor_address)
        CALL      usb_get_configuration_descriptor_address
        ; get length from wTotalLength
        B0MOV     A, usb_descriptor_pointer_l
        MOV       A, #1 ; wTotalLength is 1 word after descriptor start
        B0MOV     Z, A
        B0MOV     A, usb_descriptor_pointer_m
        B0BTS0    FC
        ADD       A, #1
        B0MOV     Y, A
        MOVC
        B0MOV     _usb_setup_data_len_l, A
        B0MOV     A, R
        B0MOV     _usb_setup_data_len_m, A
        JMP       _handle_get_descriptor_respond
_usb_get_string_descriptor:
        B0MOV     A, usb_setup_value_l
        B0BSET    FC
        ; ABI:
        ; in: A: descriptor index
        ; out: usb_descriptor_pointer_l,m set to in-flash address of first word
        ;      FC                         clear to ACK (otherwise above values are then ignored and a stall is sent back)
        ; descriptor is expected packed in flash.
        CALL      usb_get_string_descriptor_address
_get_descriptor_length:
        B0MOV     A, usb_descriptor_pointer_l
        B0MOV     Z, A
        B0MOV     A, usb_descriptor_pointer_m
        B0MOV     Y, A
        MOVC
        B0MOV     _usb_setup_data_len_l, A
        MOV       A, #0
        B0MOV     _usb_setup_data_len_m, A

        ; For example, to send the 'a' string descriptor:
        ; STRING_DESCRIPTOR_1: DB  0x04, USB_DT_STRING, 'a', 0
        ; usb_get_string_descriptor_address:
        ;       MOV     A, #STRING_DESCRIPTOR_1$L
        ;       B0MOV   usb_descriptor_pointer_l, A
        ;       MOV     A, #STRING_DESCRIPTOR_1$M
        ;       B0MOV   usb_descriptor_pointer_m, A
        ;       B0BCLR  FC
        ;       RET

_handle_get_descriptor_respond:
        B0BTS0    FC
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_length_h
        XOR       A, #0xff
        ADD       A, #1
        B0MOV     _scratch, A
        B0MOV     A, _usb_setup_data_len_m
        B0ADD     _scratch, A
        B0BTS0    FC
        JMP       _load_ep0_buffer_from_flash         ; _usb_setup_data_len_m < wLengthH: use _usb_setup_data_len_m,l
        B0BTS0    FZ
        JMP       @F                                  ; same MSB, check LSB
        B0MOV     A, usb_setup_length_h               ; wLengthH < _usb_setup_data_len_m: use wLengthH,L
        B0MOV     _usb_setup_data_len_m, A
        B0MOV     A, usb_setup_length_l
        B0MOV     _usb_setup_data_len_l, A
        JMP       _load_ep0_buffer_from_flash
@@:
        B0MOV     A, usb_setup_length_l
        ; the only important outcome is if wLengthL > _usb_setup_data_len_l, a one's complement is enough
        XOR       A, #0xff
        B0MOV     _scratch, A
        B0MOV     A, _usb_setup_data_len_l
        B0ADD     _scratch, A
        B0BTS1    FC
        JMP       _load_ep0_buffer_from_flash         ; _usb_setup_data_len_l <= wLengthL: use _usb_setup_data_len_l
        B0MOV     A, usb_setup_length_l               ; else, use wLengthL
        B0MOV     _usb_setup_data_len_l, A
        JMP       _load_ep0_buffer_from_flash

_handle_get_configuration:
        B0MOV     A, usb_setup_request_type
        CMPRS     A, #0x80
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_value_l
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_value_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_index_l
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_index_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_length_l
        CMPRS     A, #0x01
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_length_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        MOV       A, #0
        B0MOV     UDP0, A
        B0MOV     A, _active_configuration
        B0MOV     UDR0_W, A
        MOV       A, #1
        JMP       usb_ack_ep0

_handle_set_configuration:
        B0MOV     A, usb_setup_request_type
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_value_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_index_l
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_index_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_length_l
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_length_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_value_l
        B0MOV     _active_configuration, A
        ; ABI:
        ; in: A contains wValueL
        ; out: FC set to STALL, otherwise ACK
        CALL      usb_set_configuration
        B0BTS0    FC
        JMP       @F
        B0BSET    _setup_data_out
        MOV       A, #0
        JMP       usb_ack_ep0
@@:
        MOV       A, #0x00
        B0MOV     _active_configuration, A
        JMP       usb_stall_ep0

_handle_get_interface:
        B0MOV     A, usb_setup_request_type
        CMPRS     A, #0x81
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_value_l
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_value_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_length_l
        CMPRS     A, #0x01
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_length_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, _active_configuration
        B0BTS0    FZ
        JMP       usb_stall_ep0        ; GET_INTERFACE on unconfigured device
        B0BSET    FC
        ; ABI:
        ; in: (nil)
        ; out: clear FC to ack (default: stall)
        ;      R contains the answer when FC cleared
        CALL      usb_get_interface
        B0BTS0    FC
        JMP       usb_stall_ep0
        MOV       A, #0
        B0MOV     UDP0, A
        B0MOV     A, R
        B0MOV     UDR0_W, A
        MOV       A, #1
        JMP       usb_ack_ep0

_handle_set_interface:
        B0MOV     A, usb_setup_request_type
        CMPRS     A, #0x01
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_length_l
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, usb_setup_length_h
        B0BTS1    FZ
        JMP       usb_stall_ep0
        B0MOV     A, _active_configuration
        B0BTS0    FZ
        JMP       usb_stall_ep0        ; SET_INTERFACE on unconfigured device
        B0BSET    FC
        ; ABI:
        ; in: (nil)
        ; out: clear FC to ACK (default: stall)
        CALL      usb_set_interface
        B0BTS0    FC
        JMP       usb_stall_ep0
        B0BSET    _setup_data_out
        MOV       A, #0
        JMP       usb_ack_ep0
