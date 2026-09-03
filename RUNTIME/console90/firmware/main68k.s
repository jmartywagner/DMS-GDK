; DAC MASTER DMS-1 CONSOLE90 P0.5 - 68000 bootstrap ROM
; This source documents the exact intent of tools/dms_console90_firmware.py.
; The binary stored in DMC2 uses real Motorola 68000 opcodes.
;
; Memory map:
; $000000 cartridge M68K ROM
; $100000 64 KiB work RAM
; $200000 128 KiB VRAM window
; $300000 VDP registers
; $400000 PAD0
; $500000 68000<->Z80 mailbox
;
; Reset vectors: SP=$10FFFC, PC=$000100.
; Main loop waits for VBlank, reads PAD0, updates sprite X/Y, then sends
; PLAY/STOP commands to the Z80 mailbox.
;
; Controls: arrows=D-pad, Z=A, X=B, C=C, Enter=START.
