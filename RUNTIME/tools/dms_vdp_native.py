#!/usr/bin/env python3
"""Host-side acceleration for the frozen DMS-1 VDP semantics.

This library is not hardware. It is only a native implementation of the exact
P0.8/P1.0 Python VDP raster rules, used to keep the emulator UI fluid while the
68000/Z80/audio scheduler continues in real time. The Python renderer remains
the reference/fallback and is regression-compared against this implementation.
"""
from __future__ import annotations
import ctypes
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_RGB = 320 * 224 * 3


class NativeVdpRenderer:
    def __init__(self, root: Path = ROOT) -> None:
        names = ["dms1_vdp_render.dll"] if os.name == "nt" else ["libdms1_vdp_render.so", "dms1_vdp_render.so"]
        path = next((root / "build" / name for name in names if (root / "build" / name).exists()), None)
        if path is None:
            raise FileNotFoundError("native DMS-1 VDP renderer not built")
        self.path = path
        self._dll_dir_handle = None
        if os.name == "nt" and hasattr(os, "add_dll_directory"):
            try:
                self._dll_dir_handle = os.add_dll_directory(str(path.parent.resolve()))
            except OSError:
                pass
        self.lib = ctypes.CDLL(str(path))
        fn = self.lib.dms1_vdp_render
        fn.argtypes = [
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
            ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_int),
        ]
        fn.restype = ctypes.c_int
        self.fn = fn
        self.present_fn = None
        if os.name == "nt" and hasattr(self.lib, "dms1_vdp_present_win32"):
            pfn = self.lib.dms1_vdp_present_win32
            pfn.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
                ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int,
            ]
            pfn.restype = ctypes.c_int
            self.present_fn = pfn
        self._out = (ctypes.c_uint8 * MAX_RGB)()
        self._vram_obj = None
        self._cram_obj = None
        self._vram_ref = None
        self._cram_ref = None

    def _buffers(self, vdp):
        if self._vram_ref is not vdp.vram:
            self._vram_obj = (ctypes.c_uint8 * len(vdp.vram)).from_buffer(vdp.vram)
            self._vram_ref = vdp.vram
        if self._cram_ref is not vdp.cram:
            self._cram_obj = (ctypes.c_uint8 * len(vdp.cram)).from_buffer(vdp.cram)
            self._cram_ref = vdp.cram
        return self._vram_obj, self._cram_obj

    def render(self, vdp) -> tuple[int, bytes]:
        vram, cram = self._buffers(vdp)
        width = ctypes.c_int(0)
        rc = self.fn(
            vram, len(vdp.vram), cram, len(vdp.cram),
            int(vdp.mode), int(vdp.backdrop),
            int(vdp.scroll_a_x), int(vdp.scroll_a_y), int(vdp.scroll_b_x), int(vdp.scroll_b_y),
            self._out, MAX_RGB, ctypes.byref(width),
        )
        if rc != 0:
            raise RuntimeError(f"native VDP renderer failed ({rc})")
        n = int(width.value) * 224 * 3
        return int(width.value), bytes(self._out[:n])

    @property
    def has_win32_presenter(self) -> bool:
        return self.present_fn is not None

    def present_win32(self, hwnd: int, vdp, target_width: int, target_height: int) -> None:
        if self.present_fn is None:
            raise RuntimeError("native Win32 VDP presenter unavailable")
        vram, cram = self._buffers(vdp)
        rc = self.present_fn(
            ctypes.c_void_p(int(hwnd)),
            vram, len(vdp.vram), cram, len(vdp.cram),
            int(vdp.mode), int(vdp.backdrop),
            int(vdp.scroll_a_x), int(vdp.scroll_a_y), int(vdp.scroll_b_x), int(vdp.scroll_b_y),
            int(target_width), int(target_height),
        )
        if rc != 0:
            raise RuntimeError(f"native Win32 VDP presenter failed ({rc})")
