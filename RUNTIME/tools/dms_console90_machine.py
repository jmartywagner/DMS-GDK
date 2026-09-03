#!/usr/bin/env python3
"""DMS-1 P1.0.1 unified 24 MHz scheduler: 68000 + Z80 + VDP + native audio."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dms_console90_cpu import M68000, Z80
from dms_console90_format import Dmc2Image, load_image
from dms_console90_vdp import DmsVdp, VRAM_SIZE, CRAM_SIZE

ROM68K_BASE = 0x000000
WORK_RAM_BASE = 0x100000
WORK_RAM_SIZE = 0x10000
VRAM_BASE = 0x200000
CRAM_BASE = 0x220000
VDP_BASE = 0x300000
PAD_BASE = 0x400000
MAIL_BASE = 0x500000

Z80_RAM_SIZE = 0x2000
Z80_MAIL_BASE = 0x2000
Z80_AUDIO_ADDR_HI = 0x4000
Z80_AUDIO_ADDR_LO = 0x4001
Z80_AUDIO_DATA = 0x4002
Z80_TIMER_3 = 0x4010
Z80_TIMER_2 = 0x4011
Z80_TIMER_1 = 0x4012
Z80_TIMER_0 = 0x4013
Z80_TIMER_CTRL = 0x4014
Z80_SONG_RESET = 0x4015
Z80_BANK_REG = 0x6000
Z80_BANK_REG_HI = 0x6001
Z80_ROM_WINDOW = 0x8000

PAD_UP = 0x01
PAD_DOWN = 0x02
PAD_LEFT = 0x04
PAD_RIGHT = 0x08
PAD_A = 0x10
PAD_B = 0x20
PAD_C = 0x40
PAD_START = 0x80


@dataclass(frozen=True)
class AudioAction:
    kind: str
    track: int


class Console90Machine:
    CPU68K_HZ = 10_000_000
    CPUZ80_HZ = 4_000_000
    MASTER_HZ = 24_000_000
    FPS = 60
    MASTER_CYCLES_PER_FRAME = MASTER_HZ // FPS  # 400,000 exact
    ACTIVE_MASTER_CYCLES = MASTER_CYCLES_PER_FRAME * 4 // 5
    VBLANK_MASTER_CYCLES = MASTER_CYCLES_PER_FRAME - ACTIVE_MASTER_CYCLES

    def __init__(self, image: Dmc2Image) -> None:
        self.image = image
        self.metadata = image.metadata
        self.rom68k = image.chunk(b"M68K")
        self.z80_firmware = image.chunk(b"Z80 ")
        self.audio_rom = image.optional_chunk(b"DMR0") or b""
        self.audio_bank = image.optional_chunk(b"ABNK") or b""
        if self.audio_bank:
            pad = (-len(self.audio_rom)) & 0xFF
            self.audio_sample_bus = self.audio_rom + (b"\0" * pad) + self.audio_bank
            self.audio_sfx_page_base = (len(self.audio_rom) + pad) // 256
        else:
            self.audio_sample_bus = self.audio_rom
            self.audio_sfx_page_base = 0
        self.native_audio_stream = image.optional_chunk(b"NDRV") or b""
        self.music_cartridge = image.optional_chunk(b"MCAR") or b""

        self.work_ram = bytearray(WORK_RAM_SIZE)
        self.vdp = DmsVdp()
        # Compatibility aliases used by older engineering code.
        self.vram = self.vdp.vram
        self.cram = self.vdp.cram

        self.z80_ram = bytearray(Z80_RAM_SIZE)
        self.z80_ram[:min(len(self.z80_firmware), Z80_RAM_SIZE)] = self.z80_firmware[:Z80_RAM_SIZE]
        self.mailbox = bytearray(0x100)
        self.pad0 = 0
        self.vblank = 1
        self.z80_bank = 0
        self.z80_audio_address = 0
        self.z80_timer_bytes = [0, 0, 0, 0]
        self.z80_timer_target: int | None = None
        self.z80_song_start_master = 0
        self.z80_master_cycle = 0
        self.native_audio_events: list[tuple[int, int, int]] = []
        self.audio_running = False
        self.audio_actions: list[AudioAction] = []
        self.frame_counter = 0
        self.master_cycle = 0
        self._last_frame_metrics = {
            "m68k_budget": 0, "m68k_raw": 0, "m68k_wait": 0, "m68k_active": 0,
            "m68k_load_pct": 0.0, "z80_budget": 0, "z80_raw": 0, "z80_active": 0,
            "z80_load_pct": 0.0,
        }

        cpu_frontend = str(self.metadata.get("cpu_frontend", ""))
        if cpu_frontend.startswith("gcc-m68k-musashi"):
            from dms_m68k_full import M68000Full
            self.cpu68k = M68000Full(self, self.rom68k)
        else:
            # Backward compatibility: all validated P1.0/P1.1 cartridges keep
            # the exact bootstrap CPU path unless their metadata opts into GCC.
            self.cpu68k = M68000(self)
        self.cpuz80 = Z80(self)

    @classmethod
    def from_path(cls, path: str | Path) -> "Console90Machine":
        return cls(load_image(path))

    # ---- 68000 bus ----
    def read8_68k(self, address: int) -> int:
        address &= 0xFFFFFF
        if address < len(self.rom68k):
            return self.rom68k[address]
        if WORK_RAM_BASE <= address < WORK_RAM_BASE + WORK_RAM_SIZE:
            return self.work_ram[address - WORK_RAM_BASE]
        if VRAM_BASE <= address < VRAM_BASE + VRAM_SIZE:
            return self.vdp.read_vram8(address - VRAM_BASE)
        if CRAM_BASE <= address < CRAM_BASE + CRAM_SIZE:
            return self.vdp.read_cram8(address - CRAM_BASE)
        if VDP_BASE <= address < VDP_BASE + 0x100:
            return self.vdp.read_reg8(address - VDP_BASE, vblank=bool(self.vblank))
        if PAD_BASE <= address < PAD_BASE + 0x100:
            return self.pad0 if (address - PAD_BASE) == 0 else 0
        if MAIL_BASE <= address < MAIL_BASE + len(self.mailbox):
            return self.mailbox[address - MAIL_BASE]
        return 0xFF

    def write8_68k(self, address: int, value: int) -> None:
        address &= 0xFFFFFF
        value &= 0xFF
        if WORK_RAM_BASE <= address < WORK_RAM_BASE + WORK_RAM_SIZE:
            self.work_ram[address - WORK_RAM_BASE] = value
            return
        if VRAM_BASE <= address < VRAM_BASE + VRAM_SIZE:
            self.vdp.write_vram8(address - VRAM_BASE, value)
            return
        if CRAM_BASE <= address < CRAM_BASE + CRAM_SIZE:
            self.vdp.write_cram8(address - CRAM_BASE, value)
            return
        if VDP_BASE <= address < VDP_BASE + 0x100:
            self.vdp.write_reg8(address - VDP_BASE, value, vblank=bool(self.vblank))
            return
        if MAIL_BASE <= address < MAIL_BASE + len(self.mailbox):
            self.mailbox[address - MAIL_BASE] = value
            return
        # ROM/PAD/unmapped writes are ignored.

    # ---- Z80 bus ----
    def read8_z80(self, address: int) -> int:
        address &= 0xFFFF
        if address < Z80_RAM_SIZE:
            return self.z80_ram[address]
        if Z80_MAIL_BASE <= address < Z80_MAIL_BASE + len(self.mailbox):
            return self.mailbox[address - Z80_MAIL_BASE]
        if address == Z80_BANK_REG:
            return self.z80_bank & 0xFF
        if address == Z80_BANK_REG_HI:
            return (self.z80_bank >> 8) & 0xFF
        if address == Z80_TIMER_CTRL:
            return 1 if self.z80_timer_target is not None else 0
        if Z80_ROM_WINDOW <= address <= 0xFFFF:
            stream = self.native_audio_stream if self.native_audio_stream else self.audio_rom
            if stream:
                absolute = self.z80_bank * 0x8000 + (address - Z80_ROM_WINDOW)
                if absolute < len(stream):
                    return stream[absolute]
            return 0x00
        return 0xFF

    def write8_z80(self, address: int, value: int) -> None:
        address &= 0xFFFF
        value &= 0xFF
        if address < Z80_RAM_SIZE:
            self.z80_ram[address] = value
            return
        if Z80_MAIL_BASE <= address < Z80_MAIL_BASE + len(self.mailbox):
            offset = address - Z80_MAIL_BASE
            previous = self.mailbox[offset]
            self.mailbox[offset] = value
            if offset == 2 and value != previous:
                if value == 0xC2 and not self.audio_running:
                    self.audio_running = True
                    self.audio_actions.append(AudioAction("play", self.mailbox[1]))
                elif value == 0x80 and self.audio_running:
                    self.audio_running = False
                    self.audio_actions.append(AudioAction("stop", self.mailbox[1]))
            return
        if address == Z80_AUDIO_ADDR_HI:
            self.z80_audio_address = (self.z80_audio_address & 0x00FF) | (value << 8)
            return
        if address == Z80_AUDIO_ADDR_LO:
            self.z80_audio_address = (self.z80_audio_address & 0xFF00) | value
            return
        if address == Z80_AUDIO_DATA:
            if self.z80_audio_address <= 0x01FF:
                relative = max(0, self.z80_master_cycle - self.z80_song_start_master)
                self.native_audio_events.append((relative, self.z80_audio_address, value))
            return
        if Z80_TIMER_3 <= address <= Z80_TIMER_0:
            self.z80_timer_bytes[address - Z80_TIMER_3] = value
            return
        if address == Z80_SONG_RESET:
            if value:
                self.z80_song_start_master = self.z80_master_cycle
                self.z80_timer_target = None
                self.native_audio_events.clear()
            return
        if address == Z80_TIMER_CTRL:
            if value & 1:
                song_target = int.from_bytes(bytes(self.z80_timer_bytes), "big")
                self.z80_timer_target = self.z80_song_start_master + song_target
            else:
                self.z80_timer_target = None
            return
        if address == Z80_BANK_REG:
            self.z80_bank = (self.z80_bank & 0xFF00) | value
            return
        if address == Z80_BANK_REG_HI:
            self.z80_bank = (self.z80_bank & 0x00FF) | (value << 8)
            return

    def _run_z80_until_master(self, end_cycle: int) -> None:
        end_cycle = max(self.z80_master_cycle, int(end_cycle))
        while self.z80_master_cycle < end_cycle:
            if self.cpuz80.halted:
                target = self.z80_timer_target
                if target is None or target >= end_cycle:
                    delta = end_cycle - self.z80_master_cycle
                    self.cpuz80.cycles += delta // 6
                    self.z80_master_cycle = end_cycle
                    return
                wake = max(self.z80_master_cycle, target)
                rem = wake % 6
                if rem:
                    wake += 6 - rem
                if wake > end_cycle:
                    self.z80_master_cycle = end_cycle
                    return
                delta = wake - self.z80_master_cycle
                self.cpuz80.cycles += delta // 6
                self.z80_master_cycle = wake
                self.z80_timer_target = None
                self.cpuz80.halted = False
                continue
            cost = self.cpuz80.step()
            self.z80_master_cycle += cost * 6

    def set_pad(self, bits: int) -> None:
        self.pad0 = bits & 0xFF

    def pop_audio_actions(self) -> list[AudioAction]:
        actions = self.audio_actions[:]
        self.audio_actions.clear()
        return actions

    def _run_master_phase(self, master_cycles: int, *, vblank: int) -> None:
        """Advance every clock domain from the same 24 MHz master timeline."""
        self.vblank = 1 if vblank else 0
        phase_end = self.master_cycle + master_cycles

        # 68000 = 10/24 of master clock. Target-from-absolute-time avoids
        # fractional drift over long sessions even when an instruction crosses
        # a phase boundary by a few CPU cycles.
        target_68k = (phase_end * self.CPU68K_HZ) // self.MASTER_HZ
        # The video controller asserts a 60 Hz VBlank interrupt/wake line. The
        # bootstrap 68000 ROM sleeps with the real STOP opcode between frames.
        # Full vector/RTE interrupt semantics are a later full-ISA-core task;
        # for this bootstrap core the VBlank line resumes at the instruction
        # following STOP, which keeps the cartridge code in charge of gameplay.
        if vblank and self.cpu68k.stopped and not getattr(self.cpu68k, "is_full_core", False):
            self.cpu68k.stopped = False
        if self.cpu68k.cycles < target_68k:
            self.cpu68k.run(target_68k - self.cpu68k.cycles)

        # Z80 = 4 MHz = one Z80 cycle per six 24 MHz master cycles. Its native
        # music timers are already expressed in this master domain.
        if self.mailbox[0] in (2, 3, 4, 5) and self.cpuz80.halted:
            # STOP annule la temporisation musicale. Un SFX réveille seulement
            # le Z80 le temps de pousser ses écritures MMIO, puis le firmware
            # se rendort jusqu'à l'échéance musicale déjà armée.
            if self.mailbox[0] == 2:
                self.z80_timer_target = None
            self.cpuz80.halted = False
        self._run_z80_until_master(phase_end)
        self.master_cycle = phase_end

    def step_frame(self) -> None:
        start_master = self.master_cycle
        start_68k = int(self.cpu68k.cycles)
        start_68k_active = int(getattr(self.cpu68k, "active_cycles", start_68k))
        start_z80 = int(self.cpuz80.cycles)
        start_z80_active = int(getattr(self.cpuz80, "active_cycles", start_z80))
        if hasattr(self.cpu68k, "debug_profile_reset"):
            self.cpu68k.debug_profile_reset()

        self._run_master_phase(self.ACTIVE_MASTER_CYCLES, vblank=0)
        self._run_master_phase(self.VBLANK_MASTER_CYCLES, vblank=1)
        self.frame_counter += 1

        end_master = self.master_cycle
        budget68 = ((end_master * self.CPU68K_HZ) // self.MASTER_HZ) - ((start_master * self.CPU68K_HZ) // self.MASTER_HZ)
        budgetz = ((end_master * self.CPUZ80_HZ) // self.MASTER_HZ) - ((start_master * self.CPUZ80_HZ) // self.MASTER_HZ)
        raw68 = max(0, int(self.cpu68k.cycles) - start_68k)
        rawz = max(0, int(self.cpuz80.cycles) - start_z80)
        wait68 = 0
        if hasattr(self.cpu68k, "debug_profile"):
            profile68 = self.cpu68k.debug_profile()
            prof_total = int(profile68.get("total_cycles", raw68))
            wait68 = max(0, int(profile68.get("wait_cycles", 0)))
            active68 = max(0, prof_total - wait68)
        else:
            active68 = max(0, int(getattr(self.cpu68k, "active_cycles", int(self.cpu68k.cycles))) - start_68k_active)
        activez = max(0, int(getattr(self.cpuz80, "active_cycles", int(self.cpuz80.cycles))) - start_z80_active)
        self._last_frame_metrics = {
            "m68k_budget": int(budget68), "m68k_raw": raw68, "m68k_wait": wait68, "m68k_active": active68,
            "m68k_load_pct": (active68 * 100.0 / budget68) if budget68 else 0.0,
            "z80_budget": int(budgetz), "z80_raw": rawz, "z80_active": activez,
            "z80_load_pct": (activez * 100.0 / budgetz) if budgetz else 0.0,
        }

    def render_video(self) -> bytes:
        return self.vdp.render_rgb()

    def debug_state(self) -> dict:
        return {
            "frame": self.frame_counter,
            "master_cycle": self.master_cycle,
            "scheduler_68k_target": (self.master_cycle * self.CPU68K_HZ) // self.MASTER_HZ,
            "scheduler_z80_master": self.z80_master_cycle,
            "m68k_pc": self.cpu68k.pc,
            "m68k_cycles": self.cpu68k.cycles,
            "z80_pc": self.cpuz80.pc,
            "z80_cycles": self.cpuz80.cycles,
            "z80_status": self.mailbox[2],
            "audio_running": self.audio_running,
            "native_audio_writes": len(self.native_audio_events),
            "video_mode": self.vdp.mode,
            "video_palettes": self.vdp.palette_count,
            "video_width": self.vdp.active_width,
            "bg_b": self.vdp.bg_b_enabled,
            "line_scroll": self.vdp.line_scroll_enabled,
            "sprite_total_limit": self.vdp.sprite_total_limit,
            "sprite_scanline_limit": self.vdp.sprite_scanline_limit,
            "scroll_a_x": self.vdp.scroll_a_x,
            "scroll_b_x": self.vdp.scroll_b_x,
            "vdp_mode_write_rejected": self.vdp.mode_write_rejected,
            **self._last_frame_metrics,
        }
