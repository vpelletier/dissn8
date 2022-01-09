; Copyright (C) 2022  Vincent Pelletier <plr.vincent@gmail.com>
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
    .Code_Option Watch_Dog "Disable"
    .Code_Option LVD "LVD_M"
//}}SONIX_CODE_OPTION

.DATA
_interface1_alt_setting DS 1

.CODE
ORG 0
    JMP reset
ORG 8
    JMP interrupt
ORG 0x10
reset:
    INCLUDE "init.asm"
watchdog_reset:
power_on_reset:
external_reset:
    CALL    usb_init
    B0BSET  FUSBIEN
    B0BSET  FGIE
    B0BSET  FUDE
main_loop:
    JMP     main_loop
interrupt:
    CALL    usb_handle
    RETI

ORG 0x400
INCLUDE "power.asm"

ORG 0x800
INCLUDE "usb.asm"

ORG 0x1000
.CODE
_DEVICE_DESC:
    DB 18                   ; bLength
    DB USB_DT_DEVICE        ; bDescriptorType
    DW 0x0200               ; bcdUSB
    DB 0xff                 ; bDeviceClass
    DB 0xff                 ; bDeviceSubClass
    DB 0x00                 ; bDeviceProtocol
    DB USB_EP0_MAX_PACKET_SIZE ; bMaxPacketSize0
    DW 0x1d6b               ; idVendor
    DW 0x0105               ; idProduct
    DW 0x0000               ; bcdDevice
    DB 0x01                 ; iManufacturer
    DB 0x02                 ; iProduct
    DB 0x00                 ; iSerialNumber
    DB 0x02                 ; bNumConfigurations
_CONFIGURATION_1_DESC:
    DB 9                    ; bLength
    DB USB_DT_CONFIG        ; bDescriptorType
    DB 18, 0                ; wTotalLength
    DB 0x01                 ; bNumInterface
    DB 0x01                 ; bConfigurationValue
    DB 0x03                 ; iConfiguration
    DB 0b10000000           ; bmAttributes
    DB 50                   ; bMaxPower
    ; Interface 0
    DB 9                    ; bLength
    DB USB_DT_INTERFACE     ; bDescriptorType
    DB 0x00                 ; bInterfaceNumber
    DB 0x00                 ; bAlternateSetting
    DB 0x00                 ; bNumEndpoints
    DB 0xff                 ; bInterfaceClass
    DB 0x00                 ; bInterfaceSubClass
    DB 0xff                 ; bInterfaceProtocol
    DB 0x00                 ; iInterface
_CONFIGURATION_2_DESC:
    DB 9                    ; bLength
    DB USB_DT_CONFIG        ; bDescriptorType
    DB 46, 0                ; wTotalLength
    DB 0x01                 ; bNumInterface
    DB 0x02                 ; bConfigurationValue
    DB 0x04                 ; iConfiguration
    DB 0b10100000           ; bmAttributes
    DB 50                   ; bMaxPower
    ; Interface 0
    DB 9                    ; bLength
    DB USB_DT_INTERFACE     ; bDescriptorType
    DB 0x00                 ; bInterfaceNumber
    DB 0x00                 ; bAlternateSetting
    DB 0x04                 ; bNumEndpoints
    DB 0xff                 ; bInterfaceClass
    DB 0x00                 ; bInterfaceSubClass
    DB 0xff                 ; bInterfaceProtocol
    DB 0x00                 ; iInterface
    ; Endpoint 1 BULK OUT
    DB 7                    ; bLength
    DB USB_DT_ENDPOINT      ; bDescriptorType
    DB 0x01                 ; bEndpointAddress
    DB 0b00000010           ; bmAttributes
    DB 32, 0                ; wMaxPacketSize
    DB 0x00                 ; bInterval
    ; Endpoint 2 BULK IN
    DB 7                    ; bLength
    DB USB_DT_ENDPOINT      ; bDescriptorType
    DB 0x82                 ; bEndpointAddress
    DB 0b00000010           ; bmAttributes
    DB 32, 0                ; wMaxPacketSize
    DB 0x00                 ; bInterval
    ; Endpoint 3 BULK OUT
    DB 7                    ; bLength
    DB USB_DT_ENDPOINT      ; bDescriptorType
    DB 0x03                 ; bEndpointAddress
    DB 0b00000010           ; bmAttributes
    DB 32, 0                ; wMaxPacketSize
    DB 0x00                 ; bInterval
    ; Endpoint 4 BULK IN
    DB 7                    ; bLength
    DB USB_DT_ENDPOINT      ; bDescriptorType
    DB 0x84                 ; bEndpointAddress
    DB 0b00000010           ; bmAttributes
    DB 32, 0                ; wMaxPacketSize
    DB 0x00                 ; bInterval
_STRING_DESC_0:
    DB 4, USB_DT_STRING, 0x09, 0x04
_STRING_DESC_1:
    DB 8, USB_DT_STRING, "f\\x00o\\x00o\\x00"
_STRING_DESC_2:
    DB 8, USB_DT_STRING, "b\\x00a\\x00r\\x00"
_STRING_DESC_3:
    DB 12, USB_DT_STRING, "c\\x00o\\x00n\\x00f\\x001\\x00"
_STRING_DESC_4:
    DB 12, USB_DT_STRING, "c\\x00o\\x00n\\x00f\\x002\\x00"

ORG 0x1800
usb_on_suspend:
    JMP     power_sleep
    ; RET stolen from power_sleep

usb_on_reset:
usb_on_sof:
usb_on_pkt_err:
usb_on_crc_err:
    RET

usb_on_setupdata:
    JMP     usb_stall_ep0 ; TODO: implement and test
usb_on_ep0_in:
usb_on_ep0_out:
    JMP     $ ; TODO: implement once usb_on_setupdata is

usb_get_device_descriptor_address:
    MOV     A, #_DEVICE_DESC$L
    B0MOV   usb_descriptor_pointer_l, A
    MOV     A, #_DEVICE_DESC$M
    B0MOV   usb_descriptor_pointer_m, A
    JMP     _usb_get_descriptor_address_success

usb_get_configuration_descriptor_address:
    CMPRS   A, #1
    JMP     @F
    MOV     A, #_CONFIGURATION_1_DESC$L
    B0MOV   usb_descriptor_pointer_l, A
    MOV     A, #_CONFIGURATION_1_DESC$M
    B0MOV   usb_descriptor_pointer_m, A
    JMP     _usb_get_descriptor_address_success
@@:
    CMPRS   A, #2
    JMP     _usb_get_descriptor_address_failure
    MOV     A, #_CONFIGURATION_2_DESC$L
    B0MOV   usb_descriptor_pointer_l, A
    MOV     A, #_CONFIGURATION_2_DESC$M
    B0MOV   usb_descriptor_pointer_m, A
    JMP     _usb_get_descriptor_address_success

usb_get_string_descriptor_address:
    SUB     A, #5
    B0BTS0  FC
    JMP     _usb_get_descriptor_address_failure ; too-high descriptor requested
    ADD     A, #5
    B0ADD   PCL, A
    JMP     _usb_get_string_descriptor0_address
    JMP     _usb_get_string_descriptor1_address
    JMP     _usb_get_string_descriptor2_address
    JMP     _usb_get_string_descriptor3_address
    MOV     A, #_STRING_DESC_4$L
    B0MOV   usb_descriptor_pointer_l, A
    MOV     A, #_STRING_DESC_4$M
    B0MOV   usb_descriptor_pointer_m, A
    JMP     _usb_get_descriptor_address_success
_usb_get_string_descriptor3_address:
    MOV     A, #_STRING_DESC_3$L
    B0MOV   usb_descriptor_pointer_l, A
    MOV     A, #_STRING_DESC_3$M
    B0MOV   usb_descriptor_pointer_m, A
    JMP     _usb_get_descriptor_address_success
_usb_get_string_descriptor2_address:
    MOV     A, #_STRING_DESC_2$L
    B0MOV   usb_descriptor_pointer_l, A
    MOV     A, #_STRING_DESC_2$M
    B0MOV   usb_descriptor_pointer_m, A
    JMP     _usb_get_descriptor_address_success
_usb_get_string_descriptor1_address:
    MOV     A, #_STRING_DESC_1$L
    B0MOV   usb_descriptor_pointer_l, A
    MOV     A, #_STRING_DESC_1$M
    B0MOV   usb_descriptor_pointer_m, A
    JMP     _usb_get_descriptor_address_success
_usb_get_string_descriptor0_address:
    MOV     A, #_STRING_DESC_0$L
    B0MOV   usb_descriptor_pointer_l, A
    MOV     A, #_STRING_DESC_0$M
    B0MOV   usb_descriptor_pointer_m, A

_usb_get_descriptor_address_success:
    B0BCLR  FC
    RET
_usb_get_descriptor_address_failure:
    B0BSET  FC
    RET

usb_set_configuration:
    AND       A, #0x03
    B0ADD     PCL, A
    JMP     _disable_endpoints ; configuration 0: disable endpoints
    JMP     _disable_endpoints ; configuration 1: disable endpoints
    JMP     _enable_endpoints  ; configuration 2: enable endpoints
    RET                        ; configuration >2: stall
_disable_endpoints:
    B0BCLR  FUE1EN
    B0BCLR  FUE2EN
    B0BCLR  FUE3EN
    B0BCLR  FUE4EN
    JMP     _usb_set_configuration_success
_enable_endpoints:
    ; EP1 and EP3 are ready (to receive)
    MOV     A, #0xa0
    B0MOV   UE1R, A
    B0MOV   UE3R, A
    ; EP2 and EP4 are not ready (to send)
    MOV     A, #0x80
    B0MOV   UE2R, A
    B0MOV   UE4R, A
_usb_set_configuration_success:
    MOV     A, #0
    B0MOV   _interface1_alt_setting, A
    B0BCLR  FC
    RET

usb_get_interface:
    B0MOV   A, usb_setup_index_h
    B0BTS1  FZ
    RET     ; wIndexH != 0, stall
    B0MOV   A, usb_setup_index_l
    CMPRS   A, #0
    JMP     @F
    ; wIndexL == 0, accept only alt-setting 0
    B0MOV   R, #0
_usb_get_interface_success:
    B0BCLR  FC
    RET
@@:
    CMPRS   A, #1
    ; wIndexL > 1, stall
    RET
    B0MOV   A, _interface1_alt_setting
    B0MOV   R, A
    JMP     _usb_get_interface_success

usb_set_interface:
    B0MOV   A, usb_setup_value_h
    B0BTS1  FZ
    RET     ; wValueH != 0, stall
    B0MOV   A, usb_setup_index_h
    B0BTS1  FZ
    RET     ; wIndexH != 0, stall
    B0MOV   A, usb_setup_index_l
    CMPRS   A, #0
    JMP     @F
    ; wIndexL == 0, accept only alt-setting 0
    CALL    _readWValueL
    B0BTS1  FZ
    RET     ; wValueL != 0, stall
_usb_set_interface_success:
    B0BCLR  FC
    RET     ; wValueL == 0, ack
@@:
    CMPRS   A, #1
    ; wIndexL > 1, stall
    RET
    ; wIndexL == 1, accept alt-settings 0 and 1
    CALL    _readWValueL
    CMPRS   A, #0
    JMP     @F
    ; wValueL == 0, ack
    B0MOV   _interface1_alt_setting, A
    JMP     _usb_set_interface_success
@@: CMPRS   A, #1
    ; wValueL > 1, stall
    RET
    ; wValueL == 1, ack
    B0MOV   _interface1_alt_setting, A
    JMP     _usb_set_interface_success

_readWValueL:
    B0MOV   A, usb_setup_value_l
    RET
