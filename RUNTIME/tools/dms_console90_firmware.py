#!/usr/bin/env python3
"""Firmware builders for DMS-1 Console90 P0.8.

The output bytes are genuine 68000/Z80 opcodes. The tiny assemblers below only
support the instructions needed by the bootstrap ROM.
"""
from __future__ import annotations

from dataclasses import dataclass

# DMS-1 Console90 P0.8 68000 memory map
WORK_X = 0x100000
WORK_Y = 0x100002
WORK_TRACK = 0x100004
WORK_PREV_PAD = 0x100006
WORK_SCROLL_A = 0x100008
WORK_SCROLL_B = 0x10000A
WORK_BOOT_MODE = 0x10000C
VRAM_BASE = 0x200000
CRAM_BASE = 0x220000
VDP_STATUS = 0x300000
VDP_MODE = 0x300002
VDP_BACKDROP = 0x300004
VDP_SCROLL_A_X = 0x300010
VDP_SCROLL_A_Y = 0x300012
VDP_SCROLL_B_X = 0x300014
VDP_SCROLL_B_Y = 0x300016
SPRITE0_Y = VRAM_BASE + 0x0A000
SPRITE0_X = VRAM_BASE + 0x0A002
PAD0 = 0x400000
MAIL_CMD = 0x500000
MAIL_TRACK = 0x500001
MAIL_STATUS = 0x500002

PAD_UP = 0x01
PAD_DOWN = 0x02
PAD_LEFT = 0x04
PAD_RIGHT = 0x08
PAD_A = 0x10
PAD_B = 0x20
PAD_C = 0x40
PAD_START = 0x80


class Asm68k:
    def __init__(self, origin: int = 0) -> None:
        self.origin = origin
        self.data = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str]] = []
        self.jp_fixups: list[tuple[int, str]] = []

    @property
    def pc(self) -> int:
        return self.origin + len(self.data)

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ValueError(f"label 68k duplique: {name}")
        self.labels[name] = self.pc

    def w(self, value: int) -> None:
        self.data += int(value & 0xFFFF).to_bytes(2, "big")

    def l(self, value: int) -> None:
        self.data += int(value & 0xFFFFFFFF).to_bytes(4, "big")

    def move_b_imm_abs(self, value: int, address: int) -> None:
        self.w(0x13FC); self.w(value & 0xFF); self.l(address)

    def move_w_imm_abs(self, value: int, address: int) -> None:
        self.w(0x33FC); self.w(value); self.l(address)

    def move_b_abs_dn(self, address: int, reg: int = 0) -> None:
        self.w(0x1039 | ((reg & 7) << 9)); self.l(address)

    def move_w_abs_dn(self, address: int, reg: int = 0) -> None:
        self.w(0x3039 | ((reg & 7) << 9)); self.l(address)

    def move_b_dn_abs(self, reg: int, address: int) -> None:
        self.w(0x13C0 | (reg & 7)); self.l(address)

    def move_w_dn_abs(self, reg: int, address: int) -> None:
        self.w(0x33C0 | (reg & 7)); self.l(address)

    def andi_b(self, value: int, reg: int = 0) -> None:
        self.w(0x0200 | (reg & 7)); self.w(value & 0xFF)

    def andi_w(self, value: int, reg: int = 0) -> None:
        self.w(0x0240 | (reg & 7)); self.w(value & 0xFFFF)

    def cmpi_b(self, value: int, reg: int = 0) -> None:
        self.w(0x0C00 | (reg & 7)); self.w(value & 0xFF)

    def addq_w_1(self, reg: int = 0) -> None:
        self.w(0x5240 | (reg & 7))

    def subq_w_1(self, reg: int = 0) -> None:
        self.w(0x5340 | (reg & 7))

    def stop(self, sr: int = 0x2000) -> None:
        # Genuine Motorola 68000 STOP #<sr>. The DMS scheduler wakes the CPU
        # on the console VBlank interrupt line; this replaces expensive busy-wait.
        self.w(0x4E72); self.w(sr & 0xFFFF)

    def branch(self, condition: str, label: str) -> None:
        op = {"bra": 0x6000, "bne": 0x6600, "beq": 0x6700}[condition]
        self.w(op)  # 16-bit displacement extension follows
        fixup_at = len(self.data)
        self.w(0)
        self.fixups.append((fixup_at, label))

    def finish(self) -> bytes:
        for offset, label in self.fixups:
            if label not in self.labels:
                raise ValueError(f"label 68k absent: {label}")
            extension_address = self.origin + offset
            pc_after_extension = extension_address + 2
            displacement = self.labels[label] - pc_after_extension
            if not -32768 <= displacement <= 32767:
                raise ValueError("branche 68k hors portee")
            self.data[offset:offset + 2] = int(displacement & 0xFFFF).to_bytes(2, "big")
        return bytes(self.data)


def build_m68k_boot(*, start_mode: int = 0, scroll_a_step: int = 2, scroll_b_step: int = 1, player_tiles: bytes | None = None, player_palette: list[int] | None = None) -> bytes:
    """Build the P0.8 cartridge boot/game loop as genuine 68000 opcodes.

    The boot ROM itself loads CRAM/VRAM through the mapped video bus.  Once
    initialized it scrolls BG A/B independently, moves sprite 0 from the pad,
    toggles STANDARD/HIGH COLOR with C during VBlank, and keeps the P0.6
    68000->Z80 PLAY/STOP mailbox contract intact.
    """
    from dms_console90_vdp_assets import build_cram, vram_initial_writes
    if start_mode not in range(5):
        raise ValueError("start_mode doit etre 0..4")
    if not 0 <= scroll_a_step <= 8 or not 0 <= scroll_b_step <= 8:
        raise ValueError("scroll step doit etre 0..8")

    entry = 0x000100
    stack = 0x10FFFC
    image = bytearray(entry)
    image[0:4] = stack.to_bytes(4, "big")
    image[4:8] = entry.to_bytes(4, "big")
    for vector in range(2, 64):
        image[vector * 4:vector * 4 + 4] = entry.to_bytes(4, "big")

    a = Asm68k(entry)

    # --- Real 68000 video bootstrap: CRAM then VRAM. ---
    cram = build_cram(player_palette)
    for offset in range(0, len(cram), 2):
        a.move_w_imm_abs(int.from_bytes(cram[offset:offset+2], "big"), CRAM_BASE + offset)
    for offset, value in vram_initial_writes(player_tiles):
        a.move_b_imm_abs(value, VRAM_BASE + offset)

    # P1.0 GDK may choose the boot mode. Store the request in RAM now and
    # commit it exactly once in the first VBlank, preserving the VDP rule.
    a.move_b_imm_abs(start_mode, WORK_BOOT_MODE)
    a.move_b_imm_abs(0, VDP_BACKDROP)
    a.move_w_imm_abs(0, VDP_SCROLL_A_X)
    a.move_w_imm_abs(0, VDP_SCROLL_A_Y)
    a.move_w_imm_abs(0, VDP_SCROLL_B_X)
    a.move_w_imm_abs(0, VDP_SCROLL_B_Y)

    a.move_w_imm_abs(152, WORK_X)
    a.move_w_imm_abs(96, WORK_Y)
    a.move_w_imm_abs(0, WORK_SCROLL_A)
    a.move_w_imm_abs(0, WORK_SCROLL_B)
    a.move_w_imm_abs(0, WORK_TRACK)
    a.move_b_imm_abs(0, WORK_PREV_PAD)
    a.move_b_imm_abs(0, MAIL_TRACK)

    # Sleep until the DMS-1 VBlank interrupt line wakes the 68000. This is a
    # genuine STOP opcode rather than a host-side gameplay shortcut, and gives
    # us one deterministic game update per 60 Hz VBlank without a polling loop.
    a.label("frame_wait_vblank")
    a.stop(0x2000)

    # Commit requested boot mode once, during a legal VBlank window.
    a.move_b_abs_dn(WORK_BOOT_MODE, 0); a.cmpi_b(0xFF, 0); a.branch("beq", "boot_mode_done")
    a.move_b_dn_abs(0, VDP_MODE); a.move_b_imm_abs(0xFF, WORK_BOOT_MODE)
    a.label("boot_mode_done")

    # Independent plane scrolling, one update per VBlank.
    a.move_w_abs_dn(WORK_SCROLL_A, 0)
    for _ in range(scroll_a_step): a.addq_w_1(0)
    a.move_w_dn_abs(0, WORK_SCROLL_A); a.move_w_dn_abs(0, VDP_SCROLL_A_X)
    a.move_w_abs_dn(WORK_SCROLL_B, 0)
    for _ in range(scroll_b_step): a.addq_w_1(0)
    a.move_w_dn_abs(0, WORK_SCROLL_B); a.move_w_dn_abs(0, VDP_SCROLL_B_X)

    # D-pad moves the first hardware sprite.
    a.move_b_abs_dn(PAD0, 0); a.andi_b(PAD_LEFT, 0); a.branch("beq", "no_left")
    a.move_w_abs_dn(WORK_X, 0); a.subq_w_1(0); a.move_w_dn_abs(0, WORK_X)
    a.label("no_left")
    a.move_b_abs_dn(PAD0, 0); a.andi_b(PAD_RIGHT, 0); a.branch("beq", "no_right")
    a.move_w_abs_dn(WORK_X, 0); a.addq_w_1(0); a.move_w_dn_abs(0, WORK_X)
    a.label("no_right")
    a.move_b_abs_dn(PAD0, 0); a.andi_b(PAD_UP, 0); a.branch("beq", "no_up_move")
    a.move_w_abs_dn(WORK_Y, 0); a.subq_w_1(0); a.move_w_dn_abs(0, WORK_Y)
    a.label("no_up_move")
    a.move_b_abs_dn(PAD0, 0); a.andi_b(PAD_DOWN, 0); a.branch("beq", "no_down_move")
    a.move_w_abs_dn(WORK_Y, 0); a.addq_w_1(0); a.move_w_dn_abs(0, WORK_Y)
    a.label("no_down_move")
    a.move_w_abs_dn(WORK_Y, 0); a.move_w_dn_abs(0, SPRITE0_Y)
    a.move_w_abs_dn(WORK_X, 0); a.move_w_dn_abs(0, SPRITE0_X)

    # UP/DOWN keep their music-console slot-selection edge behaviour.
    a.move_b_abs_dn(PAD0, 0); a.andi_b(PAD_UP, 0); a.branch("beq", "no_up")
    a.move_b_abs_dn(WORK_PREV_PAD, 0); a.andi_b(PAD_UP, 0); a.branch("bne", "no_up")
    a.move_w_abs_dn(WORK_TRACK, 0); a.subq_w_1(0); a.andi_w(0x0007, 0)
    a.move_w_dn_abs(0, WORK_TRACK); a.move_b_dn_abs(0, MAIL_TRACK)
    a.label("no_up")
    a.move_b_abs_dn(PAD0, 0); a.andi_b(PAD_DOWN, 0); a.branch("beq", "no_down")
    a.move_b_abs_dn(WORK_PREV_PAD, 0); a.andi_b(PAD_DOWN, 0); a.branch("bne", "no_down")
    a.move_w_abs_dn(WORK_TRACK, 0); a.addq_w_1(0); a.andi_w(0x0007, 0)
    a.move_w_dn_abs(0, WORK_TRACK); a.move_b_dn_abs(0, MAIL_TRACK)
    a.label("no_down")

    # C cycles all five frozen P0.8 video modes. The cartridge does the write
    # during VBlank; the VDP still rejects the same write during active display.
    a.move_b_abs_dn(PAD0, 0); a.andi_b(PAD_C, 0); a.branch("beq", "no_c")
    a.move_b_abs_dn(WORK_PREV_PAD, 0); a.andi_b(PAD_C, 0); a.branch("bne", "no_c")
    a.move_b_abs_dn(VDP_MODE, 0); a.andi_w(0x00FF, 0); a.cmpi_b(4, 0)
    a.branch("beq", "set_mode_zero")
    a.addq_w_1(0); a.move_b_dn_abs(0, VDP_MODE); a.branch("bra", "no_c")
    a.label("set_mode_zero"); a.move_b_imm_abs(0, VDP_MODE)
    a.label("no_c")

    # P0.6 native audio contract is unchanged.
    a.move_b_abs_dn(PAD0, 0); a.andi_b(PAD_A, 0); a.branch("beq", "no_a")
    a.move_b_abs_dn(WORK_PREV_PAD, 0); a.andi_b(PAD_A, 0); a.branch("bne", "no_a")
    a.move_b_imm_abs(1, MAIL_CMD)
    a.label("no_a")
    a.move_b_abs_dn(PAD0, 0); a.andi_b(PAD_B, 0); a.branch("beq", "no_b")
    a.move_b_abs_dn(WORK_PREV_PAD, 0); a.andi_b(PAD_B, 0); a.branch("bne", "no_b")
    a.move_b_imm_abs(2, MAIL_CMD)
    a.label("no_b")
    a.move_b_abs_dn(PAD0, 0); a.andi_b(PAD_START, 0); a.branch("beq", "no_start")
    a.move_b_abs_dn(WORK_PREV_PAD, 0); a.andi_b(PAD_START, 0); a.branch("bne", "no_start")
    a.move_b_imm_abs(1, MAIL_CMD)
    a.label("no_start")

    a.move_b_abs_dn(PAD0, 0); a.move_b_dn_abs(0, WORK_PREV_PAD)
    a.branch("bra", "frame_wait_vblank")
    image += a.finish()
    return bytes(image)


class AsmZ80:
    def __init__(self) -> None:
        self.data = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str]] = []
        self.jp_fixups: list[tuple[int, str]] = []

    def label(self, name: str) -> None:
        self.labels[name] = len(self.data)

    def b(self, *values: int) -> None:
        self.data += bytes(v & 0xFF for v in values)

    def ld_sp(self, value: int) -> None: self.b(0x31, value, value >> 8)
    def ld_hl(self, value: int) -> None: self.b(0x21, value, value >> 8)
    def ld_h_n(self, value: int) -> None: self.b(0x26, value)
    def ld_l_n(self, value: int) -> None: self.b(0x2E, value)
    def ld_a_n(self, value: int) -> None: self.b(0x3E, value)
    def ld_a_mem(self, address: int) -> None: self.b(0x3A, address, address >> 8)
    def ld_mem_a(self, address: int) -> None: self.b(0x32, address, address >> 8)
    def ld_a_hl(self) -> None: self.b(0x7E)
    def inc_hl(self) -> None: self.b(0x23)
    def ld_d_a(self) -> None: self.b(0x57)
    def ld_e_a(self) -> None: self.b(0x5F)
    def ld_b_a(self) -> None: self.b(0x47)
    def ld_c_a(self) -> None: self.b(0x4F)
    def ld_a_d(self) -> None: self.b(0x7A)
    def ld_a_e(self) -> None: self.b(0x7B)
    def ld_a_c(self) -> None: self.b(0x79)
    def ld_hl_a(self) -> None: self.b(0x77)
    def push_hl(self) -> None: self.b(0xE5)
    def pop_hl(self) -> None: self.b(0xE1)
    def add_hl_de(self) -> None: self.b(0x19)
    def ld_ix_mem(self, address: int) -> None: self.b(0xDD,0x2A,address,address>>8)
    def ld_a_ix(self) -> None: self.b(0xDD,0x7E,0)
    def ld_ix_a(self) -> None: self.b(0xDD,0x77,0)
    def inc_ix(self) -> None: self.b(0xDD,0x23)
    def halt(self) -> None: self.b(0x76)
    def xor_a(self) -> None: self.b(0xAF)
    def or_a(self) -> None: self.b(0xB7)
    def cp(self, value: int) -> None: self.b(0xFE, value)

    def djnz(self, label: str) -> None:
        self.b(0x10);at=len(self.data);self.b(0);self.fixups.append((at,label))

    def jr(self, condition: str, label: str) -> None:
        opcode = {"always": 0x18, "nz": 0x20, "z": 0x28}[condition]
        self.b(opcode)
        at = len(self.data)
        self.b(0)
        self.fixups.append((at, label))

    def jp(self, label: str) -> None:
        self.b(0xC3)
        at = len(self.data)
        self.b(0, 0)
        self.jp_fixups.append((at, label))

    def finish(self) -> bytes:
        for at, label in self.fixups:
            target = self.labels[label]
            pc_after = at + 1
            displacement = target - pc_after
            if not -128 <= displacement <= 127:
                raise ValueError(f"JR Z80 hors portee: {label} disp={displacement} at={at}")
            self.data[at] = displacement & 0xFF
        for at, label in self.jp_fixups:
            target = self.labels[label] & 0xFFFF
            self.data[at] = target & 0xFF
            self.data[at + 1] = (target >> 8) & 0xFF
        return bytes(self.data)


def build_z80_driver() -> bytes:
    CMD = 0x2000
    TRACK = 0x2001
    STATUS = 0x2002
    DSEQ_CONTROL = 0x5000
    DSEQ_TRACK = 0x5001

    a = AsmZ80()
    a.ld_sp(0x1FFF)
    a.ld_a_n(0x80); a.ld_mem_a(STATUS)
    a.label("loop")
    a.ld_a_mem(CMD); a.or_a(); a.jr("z", "loop")
    a.cp(1); a.jr("z", "play")
    a.cp(2); a.jr("z", "stop")
    a.xor_a(); a.ld_mem_a(CMD); a.jr("always", "loop")

    a.label("play")
    a.ld_a_mem(TRACK); a.ld_mem_a(DSEQ_TRACK)
    a.ld_a_n(0xA7); a.ld_mem_a(DSEQ_CONTROL)
    a.ld_a_n(0xC2); a.ld_mem_a(STATUS)
    a.xor_a(); a.ld_mem_a(CMD); a.jr("always", "loop")

    a.label("stop")
    a.xor_a(); a.ld_mem_a(DSEQ_CONTROL)
    a.ld_a_n(0x80); a.ld_mem_a(STATUS)
    a.xor_a(); a.ld_mem_a(CMD); a.jr("always", "loop")
    return a.finish()



def build_z80_native_driver() -> bytes:
    """DMS-1 P1.1 native music + ADPCM SFX firmware.

    Commands kept backwards compatible:
      1 PLAY DMR (mailbox +1 low / +3 high = native stream start bank), 2 STOP DMR,
      3 PLAY_SAMPLE, 4 REGISTER_PROGRAM,
      5 RESTORE_MUSIC_SHADOW.

    PLAY_SAMPLE mailbox (68000 $500000 / Z80 $2000):
      +3 codec 1=A / 2=B
      +4/+5 start page lo/hi (absolute sample-bus page)
      +6/+7 end page lo/hi
      +8 level, +9 pan
      +10/+11 ADPCM-B delta-N lo/hi
      +12 flags (bit0 loop B)

    The music command stream remains the existing NDRV stream.  SFX writes are
    injected between DMR events; when a SFX wakes a Z80 HALT, the already armed
    absolute music timer is preserved and the driver sleeps again afterwards.
    """
    CMD=0x2000; TRACK=0x2001; STATUS=0x2002; TRACK_HI=0x2003; SFX_CODEC=0x2003
    SFX_START_LO=0x2004; SFX_START_HI=0x2005
    SFX_END_LO=0x2006; SFX_END_HI=0x2007
    SFX_LEVEL=0x2008; SFX_PAN=0x2009
    SFX_DELTA_LO=0x200A; SFX_DELTA_HI=0x200B; SFX_FLAGS=0x200C
    PROGRAM_COUNT=0x200D;RESTORE_PTR=0x200E;PROGRAM_DATA=0x2010;SHADOW_BASE=0x1500
    AUDIO_ADDR_HI=0x4000; AUDIO_ADDR_LO=0x4001; AUDIO_DATA=0x4002
    TIMER_3=0x4010; TIMER_2=0x4011; TIMER_1=0x4012; TIMER_0=0x4013
    TIMER_CTRL=0x4014; SONG_RESET=0x4015; BANKREG=0x6000; BANKREG_HI=0x6001

    a=AsmZ80(); a.ld_sp(0x1FFF); a.ld_a_n(0x80); a.ld_mem_a(STATUS)

    def addr(reg:int)->None:
        a.ld_a_n((reg>>8)&0xFF); a.ld_mem_a(AUDIO_ADDR_HI)
        a.ld_a_n(reg&0xFF); a.ld_mem_a(AUDIO_ADDR_LO)
    def wr_const(reg:int, value:int)->None:
        addr(reg); a.ld_a_n(value); a.ld_mem_a(AUDIO_DATA)
    def wr_mem(reg:int, mem:int)->None:
        addr(reg); a.ld_a_mem(mem); a.ld_mem_a(AUDIO_DATA)

    a.label('wait_start')
    a.ld_a_mem(CMD); a.cp(1); a.jr('z','play')
    a.cp(2); a.jr('z','stop_idle')
    a.cp(3); a.jr('nz','wait_check_program');a.jp('sfx')
    a.label('wait_check_program');a.cp(4);a.jr('nz','wait_check_restore');a.jp('program')
    a.label('wait_check_restore');a.cp(5);a.jr('nz','wait_start');a.jp('restore')

    a.label('play')
    a.ld_a_mem(TRACK); a.ld_mem_a(BANKREG)
    a.ld_a_mem(TRACK_HI); a.ld_mem_a(BANKREG_HI); a.ld_hl(0x8000)
    a.ld_a_n(1); a.ld_mem_a(SONG_RESET)
    a.ld_a_n(0xC2); a.ld_mem_a(STATUS)
    a.xor_a(); a.ld_mem_a(CMD); a.jp('dispatch')

    a.label('stop_idle')
    a.xor_a(); a.ld_mem_a(CMD); a.ld_a_n(0x80); a.ld_mem_a(STATUS)
    a.jr('always','wait_start')

    a.label('dispatch')
    a.ld_a_mem(CMD); a.cp(2); a.jr('nz','dispatch_check_sfx'); a.jp('stop_running')
    a.label('dispatch_check_sfx'); a.cp(3); a.jr('nz','dispatch_check_program');a.jp('sfx')
    a.label('dispatch_check_program');a.cp(4);a.jr('nz','dispatch_check_restore');a.jp('program')
    a.label('dispatch_check_restore');a.cp(5);a.jr('nz','dispatch_stream');a.jp('restore')
    a.label('dispatch_stream')
    a.ld_a_hl(); a.inc_hl()
    a.cp(0x00); a.jr('z','end_song')
    a.cp(0x01); a.jr('z','wait_abs')
    a.cp(0x10); a.jr('z','write_reg')
    a.cp(0x7E); a.jr('z','switch_bank16')
    a.cp(0x7F); a.jr('z','switch_bank')
    a.ld_a_n(0xEE); a.ld_mem_a(STATUS); a.halt()

    a.label('write_reg')
    a.ld_a_hl(); a.inc_hl(); a.ld_d_a();a.ld_mem_a(AUDIO_ADDR_HI)
    a.ld_a_hl(); a.inc_hl(); a.ld_e_a();a.ld_mem_a(AUDIO_ADDR_LO)
    a.ld_a_hl(); a.inc_hl(); a.ld_c_a();a.ld_mem_a(AUDIO_DATA)
    a.push_hl();a.ld_hl(SHADOW_BASE);a.add_hl_de();a.ld_a_c();a.ld_hl_a();a.pop_hl()
    a.jp('dispatch')

    a.label('wait_abs')
    a.ld_a_hl(); a.inc_hl(); a.ld_mem_a(TIMER_3)
    a.ld_a_hl(); a.inc_hl(); a.ld_mem_a(TIMER_2)
    a.ld_a_hl(); a.inc_hl(); a.ld_mem_a(TIMER_1)
    a.ld_a_hl(); a.inc_hl(); a.ld_mem_a(TIMER_0)
    a.ld_a_n(1); a.ld_mem_a(TIMER_CTRL); a.halt(); a.jp('dispatch')

    a.label('switch_bank16')
    a.ld_a_hl(); a.inc_hl(); a.ld_mem_a(BANKREG_HI)
    a.ld_a_hl(); a.inc_hl(); a.ld_mem_a(BANKREG); a.ld_h_n(0x80); a.ld_l_n(0)
    a.jp('dispatch')

    a.label('switch_bank')
    a.ld_a_hl(); a.inc_hl(); a.ld_mem_a(BANKREG); a.ld_h_n(0x80); a.ld_l_n(0)
    a.jp('dispatch')

    a.label('stop_running')
    a.xor_a(); a.ld_mem_a(CMD); a.ld_a_n(0x80); a.ld_mem_a(STATUS); a.jp('wait_start')

    a.label('end_song')
    a.ld_a_n(0x80); a.ld_mem_a(STATUS); a.jp('wait_start')

    # Sample command.  Register sequence is identical to the validated Audio
    # Lab / DMR ADPCM protocol; only the trigger source is the gameplay mailbox.
    a.label('sfx')
    a.xor_a(); a.ld_mem_a(CMD)
    a.ld_a_mem(SFX_CODEC); a.cp(1); a.jr('z','sfx_a')
    a.cp(2); a.jr('nz','sfx_invalid')
    a.jp('sfx_b')
    a.label('sfx_invalid'); a.jp('sfx_return')

    a.label('sfx_a')
    wr_const(0x0120,0x02); wr_mem(0x0121,SFX_PAN); wr_mem(0x0122,SFX_LEVEL)
    wr_mem(0x0124,SFX_START_LO); wr_mem(0x0125,SFX_START_HI)
    wr_mem(0x0126,SFX_END_LO); wr_mem(0x0127,SFX_END_HI); wr_const(0x0120,0x01)
    a.jp('sfx_return')

    a.label('sfx_b')
    wr_const(0x0140,0x01); wr_mem(0x0141,SFX_PAN)
    wr_mem(0x0142,SFX_START_LO); wr_mem(0x0143,SFX_START_HI)
    wr_mem(0x0144,SFX_END_LO); wr_mem(0x0145,SFX_END_HI)
    wr_mem(0x0149,SFX_DELTA_LO); wr_mem(0x014A,SFX_DELTA_HI); wr_mem(0x014B,SFX_LEVEL)
    # P1.1 core starts B as one-shot. Loop flag is retained in the mailbox and
    # resource metadata; full runtime policy will use it in the next audio tier.
    wr_const(0x0140,0x80)

    a.label('sfx_return')
    # If music is active, preserve an armed WAIT_ABS after the injected SFX.
    a.ld_a_mem(STATUS); a.cp(0xC2); a.jr('z','sfx_music_active')
    a.jp('wait_start')
    a.label('sfx_music_active')
    a.ld_a_mem(TIMER_CTRL); a.or_a(); a.jr('nz','sfx_wait_timer')
    a.jp('dispatch')
    a.label('sfx_wait_timer'); a.halt(); a.jp('dispatch')

    # Generic register program. Each address is remembered per runtime voice;
    # command 5 later restores the CURRENT music shadow, including music writes
    # that happened while the short SFX owned the channel.
    a.label('program')
    a.xor_a();a.ld_mem_a(CMD);a.ld_ix_mem(RESTORE_PTR);a.ld_hl(PROGRAM_DATA);a.ld_a_mem(PROGRAM_COUNT);a.ld_b_a()
    a.label('program_loop')
    a.ld_a_hl();a.inc_hl();a.ld_d_a();a.ld_mem_a(AUDIO_ADDR_HI);a.ld_ix_a();a.inc_ix()
    a.ld_a_hl();a.inc_hl();a.ld_e_a();a.ld_mem_a(AUDIO_ADDR_LO);a.ld_ix_a();a.inc_ix()
    a.ld_a_hl();a.inc_hl();a.ld_mem_a(AUDIO_DATA);a.djnz('program_loop');a.jp('program_return')

    a.label('restore')
    a.xor_a();a.ld_mem_a(CMD);a.ld_ix_mem(RESTORE_PTR);a.ld_a_mem(PROGRAM_COUNT);a.ld_b_a()
    a.label('restore_loop')
    a.ld_a_ix();a.inc_ix();a.ld_d_a();a.ld_mem_a(AUDIO_ADDR_HI)
    a.ld_a_ix();a.inc_ix();a.ld_e_a();a.ld_mem_a(AUDIO_ADDR_LO)
    a.push_hl();a.ld_hl(SHADOW_BASE);a.add_hl_de();a.ld_a_hl();a.ld_mem_a(AUDIO_DATA);a.pop_hl();a.djnz('restore_loop')
    a.label('program_return')
    a.ld_a_mem(STATUS);a.cp(0xC2);a.jr('z','program_music_active');a.jp('wait_start')
    a.label('program_music_active');a.ld_a_mem(TIMER_CTRL);a.or_a();a.jr('nz','program_wait_timer');a.jp('dispatch')
    a.label('program_wait_timer');a.halt();a.jp('dispatch')
    return a.finish()

def make_sprite_tile() -> bytes:
    """One 16x16 packed 4bpp tile (two pixels per byte)."""
    pixels: list[int] = []
    for y in range(16):
        for x in range(16):
            dx = abs(x - 7.5)
            dy = abs(y - 7.5)
            if dx + dy < 5.0:
                color = 3
            elif dx + dy < 7.0:
                color = 2
            elif x in (0, 15) or y in (0, 15):
                color = 1
            else:
                color = 0
            pixels.append(color)
    packed = bytearray()
    for index in range(0, len(pixels), 2):
        packed.append((pixels[index] << 4) | pixels[index + 1])
    return bytes(packed)
