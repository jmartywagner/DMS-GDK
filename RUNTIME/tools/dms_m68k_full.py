#!/usr/bin/env python3
"""Native full 68000 adapter for DMS-1 P1.2.

Musashi executes entirely inside dms1_m68k.dll. At scheduler boundaries this
adapter synchronises the small shared state and replays native bus write events
into the already-validated Python VDP model. Old bootstrap ROMs do not import or
use this adapter.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

WORK_RAM_SIZE = 0x10000
VRAM_SIZE = 0x20000
CRAM_SIZE = 0x100
MAIL_SIZE = 0x100
VRAM_BASE = 0x200000
CRAM_BASE = 0x220000
VDP_BASE = 0x300000

EV_VRAM = 1
EV_CRAM = 2
EV_VDP = 3


class Full68000Error(RuntimeError):
    pass


def default_dll_path() -> Path:
    override = os.environ.get("DMS1_M68K_DLL")
    if override:
        return Path(override)
    runtime_root = Path(__file__).resolve().parents[1]
    return runtime_root / "build" / "dms1_m68k.dll"


class M68000Full:
    """Drop-in scheduler-facing 68000 using the native Musashi bridge."""

    stopped = False  # compatibility attribute; GCC P1.2 uses VBlank polling.
    is_full_core = True

    def __init__(self, machine, rom: bytes, dll_path: str | Path | None = None) -> None:
        self.machine = machine
        self.dll_path = Path(dll_path) if dll_path else default_dll_path()
        if not self.dll_path.exists():
            raise Full68000Error(
                "coeur 68000 complet local absent: lance ADMIN\\REBUILD_68000_CORE.bat "
                f"(attendu: {self.dll_path})"
            )
        self._dll_dir_handle = None
        if os.name == "nt" and hasattr(os, "add_dll_directory"):
            try:
                self._dll_dir_handle = os.add_dll_directory(str(self.dll_path.parent.resolve()))
            except OSError:
                pass
        try:
            self.lib = ctypes.CDLL(str(self.dll_path))
        except OSError as exc:
            raise Full68000Error(f"dms1_m68k.dll impossible a charger: {exc}") from exc
        self._bind()
        if not self.lib.dms68k_init():
            raise Full68000Error("initialisation Musashi impossible")
        rb = (ctypes.c_uint8 * len(rom)).from_buffer_copy(rom)
        if not self.lib.dms68k_load_rom(rb, len(rom)):
            raise Full68000Error("chargement ROM dans le coeur 68000 impossible")
        self.cycles = 0
        self._sync_all_to_native()
        self.lib.dms68k_reset()

    def _bind(self) -> None:
        L = self.lib
        L.dms68k_init.restype = ctypes.c_int
        L.dms68k_load_rom.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint32]
        L.dms68k_load_rom.restype = ctypes.c_int
        L.dms68k_reset.argtypes = []
        L.dms68k_run.argtypes = [ctypes.c_int]
        L.dms68k_run.restype = ctypes.c_int
        L.dms68k_set_pad.argtypes = [ctypes.c_uint8]
        L.dms68k_set_vblank.argtypes = [ctypes.c_uint8]
        L.dms68k_set_irq.argtypes = [ctypes.c_uint8]
        for name, n in (
            ("dms68k_set_mailbox", MAIL_SIZE), ("dms68k_get_mailbox", MAIL_SIZE),
            ("dms68k_set_ram", WORK_RAM_SIZE), ("dms68k_get_ram", WORK_RAM_SIZE),
            ("dms68k_set_vram", VRAM_SIZE), ("dms68k_set_cram", CRAM_SIZE),
        ):
            fn = getattr(L, name)
            fn.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint32]
        L.dms68k_set_vdp_reg.argtypes = [ctypes.c_uint8, ctypes.c_uint8]
        L.dms68k_set_mode_rejected.argtypes = [ctypes.c_uint8]
        L.dms68k_event_count.restype = ctypes.c_uint32
        L.dms68k_event_overflow.restype = ctypes.c_uint32
        L.dms68k_event_address.argtypes = [ctypes.c_uint32]
        L.dms68k_event_address.restype = ctypes.c_uint32
        L.dms68k_event_value.argtypes = [ctypes.c_uint32]
        L.dms68k_event_value.restype = ctypes.c_uint32
        L.dms68k_event_kind.argtypes = [ctypes.c_uint32]
        L.dms68k_event_kind.restype = ctypes.c_uint32
        L.dms68k_events_clear.argtypes = []
        L.dms68k_get_pc.restype = ctypes.c_uint32
        L.dms68k_get_sr.restype = ctypes.c_uint32

        # ABI P1.2 debug/profiling exports were added after the first Windows
        # dms1_m68k.dll shipped.  They are optional for normal game execution.
        # Keep the core ABI strict, but accept the legacy precompiled DLL and
        # gracefully degrade debugger registers/profiling until the DLL is
        # explicitly rebuilt.
        self._has_debug_regs = all(hasattr(L, name) for name in (
            "dms68k_get_d", "dms68k_get_a",
        ))
        if self._has_debug_regs:
            L.dms68k_get_d.argtypes = [ctypes.c_uint32]
            L.dms68k_get_d.restype = ctypes.c_uint32
            L.dms68k_get_a.argtypes = [ctypes.c_uint32]
            L.dms68k_get_a.restype = ctypes.c_uint32

        self._has_native_profile = all(hasattr(L, name) for name in (
            "dms68k_profile_reset",
            "dms68k_profile_total_cycles",
            "dms68k_profile_wait_cycles",
            "dms68k_profile_clear_wait_ranges",
            "dms68k_profile_set_wait_range",
        ))
        if self._has_native_profile:
            L.dms68k_profile_reset.argtypes = []
            L.dms68k_profile_total_cycles.restype = ctypes.c_uint64
            L.dms68k_profile_wait_cycles.restype = ctypes.c_uint64
            L.dms68k_profile_clear_wait_ranges.argtypes = []
            L.dms68k_profile_set_wait_range.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
        self._profile_cycle_base = 0

    @staticmethod
    def _arr(data: bytes | bytearray):
        return (ctypes.c_uint8 * len(data)).from_buffer_copy(data)

    def _sync_all_to_native(self) -> None:
        m = self.machine
        ram = self._arr(m.work_ram); self.lib.dms68k_set_ram(ram, WORK_RAM_SIZE)
        vram = self._arr(m.vdp.vram); self.lib.dms68k_set_vram(vram, VRAM_SIZE)
        cram = self._arr(m.vdp.cram); self.lib.dms68k_set_cram(cram, CRAM_SIZE)
        mail = self._arr(m.mailbox); self.lib.dms68k_set_mailbox(mail, MAIL_SIZE)
        self._sync_vdp_regs_to_native()

    def _sync_vdp_regs_to_native(self) -> None:
        v = self.machine.vdp
        values = {
            0x02: v.mode,
            0x04: v.backdrop,
            0x06: v.active_width & 0xFF,
            0x07: 1 if v.active_width == 256 else 0,
            0x08: v.sprite_total_limit & 0xFF,
            0x09: v.sprite_scanline_limit & 0xFF,
            0x10: (v.scroll_a_x >> 8) & 0xFF, 0x11: v.scroll_a_x & 0xFF,
            0x12: (v.scroll_a_y >> 8) & 0xFF, 0x13: v.scroll_a_y & 0xFF,
            0x14: (v.scroll_b_x >> 8) & 0xFF, 0x15: v.scroll_b_x & 0xFF,
            0x16: (v.scroll_b_y >> 8) & 0xFF, 0x17: v.scroll_b_y & 0xFF,
        }
        for off, val in values.items():
            self.lib.dms68k_set_vdp_reg(off, val)
        self.lib.dms68k_set_mode_rejected(1 if v.mode_write_rejected else 0)

    def _prepare_phase(self) -> None:
        m = self.machine
        self.lib.dms68k_set_pad(m.pad0 & 0xFF)
        self.lib.dms68k_set_vblank(1 if m.vblank else 0)
        mail = self._arr(m.mailbox)
        self.lib.dms68k_set_mailbox(mail, MAIL_SIZE)
        # Z80 cannot modify work RAM/VRAM/CRAM, so only shared/MMIO state needs
        # per-phase upload. VDP state is cheap and keeps read-after-phase exact.
        self._sync_vdp_regs_to_native()
        self.lib.dms68k_events_clear()

    def _finish_phase(self) -> None:
        m = self.machine
        if self.lib.dms68k_event_overflow():
            raise Full68000Error("tampon d'evenements bus 68000 sature")
        count = int(self.lib.dms68k_event_count())
        for i in range(count):
            kind = int(self.lib.dms68k_event_kind(i))
            address = int(self.lib.dms68k_event_address(i)) & 0xFFFFFF
            value = int(self.lib.dms68k_event_value(i)) & 0xFF
            if kind == EV_VRAM:
                m.vdp.write_vram8(address - VRAM_BASE, value)
            elif kind == EV_CRAM:
                m.vdp.write_cram8(address - CRAM_BASE, value)
            elif kind == EV_VDP:
                m.vdp.write_reg8(address - VDP_BASE, value, vblank=bool(m.vblank))
        # Copy RAM and mailbox back once, after all CPU accesses are complete.
        ram = (ctypes.c_uint8 * WORK_RAM_SIZE)()
        self.lib.dms68k_get_ram(ram, WORK_RAM_SIZE)
        m.work_ram[:] = bytes(ram)
        mail = (ctypes.c_uint8 * MAIL_SIZE)()
        self.lib.dms68k_get_mailbox(mail, MAIL_SIZE)
        m.mailbox[:] = bytes(mail)

    def run(self, budget: int) -> int:
        if budget <= 0:
            return 0
        self._prepare_phase()
        actual = int(self.lib.dms68k_run(int(budget)))
        self.cycles += actual
        self._finish_phase()
        return actual

    @property
    def pc(self) -> int:
        return int(self.lib.dms68k_get_pc()) & 0xFFFFFF

    @property
    def sr(self) -> int:
        return int(self.lib.dms68k_get_sr()) & 0xFFFF

    @property
    def d(self) -> list[int]:
        if not self._has_debug_regs:
            return [0] * 8
        return [int(self.lib.dms68k_get_d(i)) & 0xFFFFFFFF for i in range(8)]

    @property
    def a(self) -> list[int]:
        if not self._has_debug_regs:
            return [0] * 8
        return [int(self.lib.dms68k_get_a(i)) & 0xFFFFFFFF for i in range(8)]

    def debug_profile_reset(self) -> None:
        self._profile_cycle_base = int(self.cycles)
        if self._has_native_profile:
            self.lib.dms68k_profile_reset()

    def debug_profile(self) -> dict:
        if self._has_native_profile:
            total = int(self.lib.dms68k_profile_total_cycles())
            wait = int(self.lib.dms68k_profile_wait_cycles())
            return {"total_cycles": total, "wait_cycles": wait, "active_cycles": max(0, total - wait)}
        # Legacy DLL fallback: it can execute the game correctly but cannot
        # classify wait-loop cycles. Report the actual scheduler delta as active.
        total = max(0, int(self.cycles) - int(self._profile_cycle_base))
        return {"total_cycles": total, "wait_cycles": 0, "active_cycles": total}

    def debug_set_wait_ranges(self, ranges: list[tuple[int, int]]) -> None:
        if not self._has_native_profile:
            return
        self.lib.dms68k_profile_clear_wait_ranges()
        for i, (start, end) in enumerate(ranges[:8]):
            self.lib.dms68k_profile_set_wait_range(i, int(start), int(end))

    def set_irq(self, level: int) -> None:
        self.lib.dms68k_set_irq(level & 7)

    def reset(self) -> None:
        self.cycles = 0
        self._sync_all_to_native()
        self.lib.dms68k_reset()
