#!/usr/bin/env python3
"""DMS-1 Debugger / Performance Analyzer V0.1.

PC-side observer only. It never changes DMS-1 clocks, RAM/VRAM sizes, sprite
limits or audio capacity. GCC symbols are optional and live outside the ROM.
"""
from __future__ import annotations

import csv
import io
import json
import re
import shutil
import struct
import time
import traceback
import zipfile
import zlib
from collections import deque
from datetime import datetime
from pathlib import Path

from dms_console90_machine import WORK_RAM_BASE, WORK_RAM_SIZE
from dms_console90_vdp import HEIGHT, MODE_PROFILES

HISTORY_FRAMES = 120


def _pct(value: float) -> str:
    return f"{value:5.1f}%"


def _bar(value: float, width: int = 20) -> str:
    n = max(0, min(width, int(round(value * width / 100.0))))
    return "[" + ("#" * n) + ("-" * (width - n)) + "]"


def _spark(values: list[float], width: int = 60) -> str:
    if not values:
        return ""
    values = values[-width:]
    chars = " .:-=+*#%@"
    out = []
    for v in values:
        i = int(max(0.0, min(100.0, v)) * (len(chars) - 1) / 100.0)
        out.append(chars[i])
    return "".join(out)


def _png_rgb(width: int, height: int, rgb: bytes) -> bytes:
    """Minimal standard-library PNG writer for RGB888 screenshots."""
    if len(rgb) != width * height * 3:
        raise ValueError("invalid RGB buffer size")
    raw = b"".join(b"\x00" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


class GccSymbols:
    def __init__(self, map_path: Path | None = None) -> None:
        self.map_path = map_path
        self.wait_ranges: list[tuple[int, int]] = []
        self.ebss: int | None = None
        self.stack_top: int | None = None
        if map_path and map_path.is_file():
            self._parse(map_path)

    def _parse(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if ".text.SYS_waitVBlank" in line:
                for nxt in lines[i + 1:i + 4]:
                    m = re.search(r"0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)", nxt)
                    if m:
                        start = int(m.group(1), 16); size = int(m.group(2), 16)
                        if size:
                            self.wait_ranges.append((start, start + size))
                        break
            if "_ebss" in line:
                m = re.search(r"0x([0-9a-fA-F]+)\s+_ebss\b", line)
                if m: self.ebss = int(m.group(1), 16)
            if "__stack_top" in line:
                m = re.search(r"0x([0-9a-fA-F]+)\s+__stack_top\b", line)
                if m: self.stack_top = int(m.group(1), 16)


class DmsDebugger:
    VIEW_KEYS = {
        "F2": "collision", "F3": "vdp", "F4": "audio", "F5": "ram",
        "F6": "cpu", "F7": "performance",
    }

    def __init__(self, machine, rom_path: Path, runtime_root: Path) -> None:
        self.machine = machine
        self.rom_path = Path(rom_path).resolve()
        self.runtime_root = Path(runtime_root).resolve()
        self.visible = True
        self.view = "performance"
        self.paused = False
        self._step_requests = 0
        self.history: deque[dict] = deque(maxlen=HISTORY_FRAMES)
        self.events: deque[str] = deque(maxlen=512)
        self.last_sprite = self.machine.vdp.debug_sprite_metrics()
        self.host_stats: dict = {}
        self.audio_stats: dict = {}
        self.audio_status = "AUDIO: UNKNOWN"
        # Host-side counters are incremental: a long debug session must not
        # rescan the complete audio event history every 200 ms.
        self._audio_counted_events = 0
        self._audio_write_counts = {"OPZ": 0, "SSG": 0, "ADPCM_A": 0, "ADPCM_B": 0}
        self._last_warning_frame: dict[str, int] = {}
        self.map_path = self._find_map()
        self.symbols = GccSymbols(self.map_path)
        if hasattr(self.machine.cpu68k, "debug_set_wait_ranges"):
            self.machine.cpu68k.debug_set_wait_ranges(self.symbols.wait_ranges)
        self.capture_root = self.runtime_root / "DOCS_REPORTS" / "runtime" / "debug_captures"
        self.capture_root.mkdir(parents=True, exist_ok=True)
        self.last_capture: Path | None = None
        self.log("SYSTEM", f"Debugger V0.1 ROM={self.rom_path.name}")
        if self.map_path:
            self.log("SYSTEM", f"GCC symbols={self.map_path}")
        else:
            self.log("SYSTEM", "GCC symbols unavailable; CPU load is RAW for polling-based GCC ROMs")

    def _find_map(self) -> Path | None:
        sibling = self.rom_path.with_suffix(".map")
        if sibling.is_file(): return sibling
        build_sibling = self.rom_path.parent / (self.rom_path.stem + ".map")
        if build_sibling.is_file(): return build_sibling
        source = str(self.machine.metadata.get("source_project", "") or "").rstrip("\\/")
        project_name = source.replace("\\", "/").split("/")[-1] if source else self.rom_path.stem
        root = self.runtime_root.parent
        candidate = root / "SAMPLES" / project_name / "build" / f"{project_name}.map"
        if candidate.is_file(): return candidate
        # Also supports a user project copied beside the GDK.
        for base in (root / "PROJECTS", root / "SAMPLES"):
            if not base.is_dir(): continue
            candidate = base / project_name / "build" / f"{project_name}.map"
            if candidate.is_file(): return candidate
        return None

    def log(self, category: str, text: str) -> None:
        self.events.append(f"F{self.machine.frame_counter:08d} {category:<9} {text}")

    def set_runtime_context(self, *, host_stats: dict | None = None, audio_stats: dict | None = None, audio_status: str | None = None) -> None:
        if host_stats is not None: self.host_stats = dict(host_stats)
        if audio_stats is not None: self.audio_stats = dict(audio_stats)
        if audio_status is not None: self.audio_status = str(audio_status)

    def after_frame(self) -> None:
        state = self.machine.debug_state()
        spr = self.machine.vdp.debug_sprite_metrics()
        self.last_sprite = spr
        row = {
            "frame": int(state["frame"]),
            "m68k_budget": int(state.get("m68k_budget", 0)),
            "m68k_active": int(state.get("m68k_active", 0)),
            "m68k_wait": int(state.get("m68k_wait", 0)),
            "m68k_raw": int(state.get("m68k_raw", 0)),
            "m68k_load_pct": float(state.get("m68k_load_pct", 0.0)),
            "z80_budget": int(state.get("z80_budget", 0)),
            "z80_active": int(state.get("z80_active", 0)),
            "z80_load_pct": float(state.get("z80_load_pct", 0.0)),
            "sprites": int(spr["active"]),
            "sprite_limit": int(spr["total_limit"]),
            "scanline_peak": int(spr["peak_scanline"]),
            "scanline_limit": int(spr["scanline_limit"]),
            "scanline_overflows": int(spr["overflow_count"]),
            "video_mode": int(self.machine.vdp.mode),
        }
        self.history.append(row)
        frame = row["frame"]
        if row["m68k_load_pct"] >= 90.0:
            self._warn_once("m68k90", frame, f"68000 load {row['m68k_load_pct']:.1f}%")
        if row["scanline_overflows"]:
            self._warn_once("scanline", frame, f"sprite scanline overflow peak {row['scanline_peak']}/{row['scanline_limit']}")
        if row["sprites"] > row["sprite_limit"]:
            self._warn_once("sprites", frame, f"sprite total overflow {row['sprites']}/{row['sprite_limit']}")

    def _warn_once(self, key: str, frame: int, text: str) -> None:
        last = self._last_warning_frame.get(key, -1000)
        if frame - last >= 30:
            self._last_warning_frame[key] = frame
            self.log("WARNING", text)

    def handle_key(self, key: str) -> str | None:
        key = key.upper()
        if key == "F1":
            self.visible = not self.visible
            if self.visible: self.view = "performance"
            self.log("SYSTEM", f"HUD {'ON' if self.visible else 'OFF'}")
            return None
        if key in self.VIEW_KEYS:
            self.visible = True; self.view = self.VIEW_KEYS[key]
            self.log("SYSTEM", f"VIEW {self.view.upper()}")
            return None
        if key == "F8":
            self.paused = not self.paused
            self.log("SYSTEM", "PAUSE" if self.paused else "RESUME")
            return None
        if key == "F9":
            return "capture"
        if key == "F10":
            if self.paused:
                self._step_requests += 1
                self.log("SYSTEM", "FRAME STEP +1")
            return None
        return None

    def consume_step(self) -> bool:
        if self._step_requests <= 0: return False
        self._step_requests -= 1
        return True

    def _loads(self) -> tuple[float, float, float, float]:
        if not self.history: return 0.0, 0.0, 0.0, 0.0
        m = [r["m68k_load_pct"] for r in self.history]
        z = [r["z80_load_pct"] for r in self.history]
        return m[-1], sum(m) / len(m), max(m), z[-1]

    def _ram_info(self) -> dict:
        cpu = self.machine.cpu68k
        a = list(getattr(cpu, "a", [0] * 8))
        sp = int(a[7]) if len(a) >= 8 else 0
        ebss = self.symbols.ebss
        top = self.symbols.stack_top
        nonzero = sum(1 for b in self.machine.work_ram if b)
        out = {"known": False, "sp": sp, "ebss": ebss, "stack_top": top, "nonzero_bytes": nonzero}
        if ebss is None or top is None: return out
        if not (WORK_RAM_BASE <= ebss <= WORK_RAM_BASE + WORK_RAM_SIZE): return out
        if not (WORK_RAM_BASE <= top <= WORK_RAM_BASE + WORK_RAM_SIZE): return out
        if not (WORK_RAM_BASE <= sp <= WORK_RAM_BASE + WORK_RAM_SIZE): return out
        static = max(0, ebss - WORK_RAM_BASE)
        stack_used = max(0, top - min(top, sp))
        free_gap = max(0, sp - ebss)
        out.update({"known": True, "static": static, "stack_used": stack_used, "free_gap": free_gap})
        return out

    def _audio_counts(self) -> dict:
        events = self.machine.native_audio_events
        # A new song/reset may replace or truncate the event buffer.
        if len(events) < self._audio_counted_events:
            self._audio_counted_events = 0
            for key in self._audio_write_counts:
                self._audio_write_counts[key] = 0
        for _cycle, address, _value in events[self._audio_counted_events:]:
            if 0x0000 <= address <= 0x00FF: self._audio_write_counts["OPZ"] += 1
            elif 0x0100 <= address <= 0x010F: self._audio_write_counts["SSG"] += 1
            elif 0x0120 <= address <= 0x012F: self._audio_write_counts["ADPCM_A"] += 1
            elif 0x0140 <= address <= 0x015F: self._audio_write_counts["ADPCM_B"] += 1
        self._audio_counted_events = len(events)
        return dict(self._audio_write_counts)

    def hud_text(self) -> str:
        if not self.visible:
            return "DMS-1 DEBUG OFF | F1=DEBUG | F8=PAUSE | F9=CAPTURE | F12=FREEZE VIDEO"
        state = self.machine.debug_state(); spr = self.last_sprite
        profile = MODE_PROFILES[int(self.machine.vdp.mode)]
        m_now, m_avg, m_peak, z_now = self._loads()
        fps = float(self.host_stats.get("fps", 0.0) or 0.0)
        frame_ms = 1000.0 / fps if fps > 0.01 else 0.0
        paused = " PAUSED" if self.paused else ""
        if self.view == "performance":
            wait_note = "" if self.symbols.wait_ranges else " RAW(no wait symbols)"
            mhead = max(-999.0, 100.0 - m_now); zhead = max(-999.0, 100.0 - z_now)
            sprite_head = spr["total_limit"] - spr["active"]
            line_head = spr["scanline_limit"] - spr["peak_scanline"]
            return "\n".join([
                f"DMS-1 DEBUG / PERFORMANCE{paused} | F1 HUD F2 COLL F3 VDP F4 AUDIO F5 RAM F6 CPU F8 PAUSE F9 CAP F10 STEP",
                f"FPS {fps:5.2f}  FRAME {frame_ms:5.2f}ms  DMS FRAME {state['frame']}  VIDEO M{profile.mode} {profile.name}",
                f"68000 {_bar(m_now)} {_pct(m_now)}  {state.get('m68k_active',0):6d}/{state.get('m68k_budget',0):6d} cyc  WAIT {state.get('m68k_wait',0):6d}{wait_note}",
                f"      AVG {_pct(m_avg)} PEAK {_pct(m_peak)}  HEADROOM {mhead:5.1f}%",
                f"Z80   {_bar(z_now)} {_pct(z_now)}  {state.get('z80_active',0):6d}/{state.get('z80_budget',0):6d} cyc  HEADROOM {zhead:5.1f}%",
                f"SPRITES {spr['active']:3d}/{spr['total_limit']:3d}  HEADROOM {sprite_head:3d} | SCANLINE {spr['peak_scanline']:2d}/{spr['scanline_limit']:2d}  MARGIN {line_head:3d}  OVERFLOW {spr['overflow_count']}",
                f"68000 HIST |{_spark([r['m68k_load_pct'] for r in self.history])}|",
                f"Z80   HIST |{_spark([r['z80_load_pct'] for r in self.history])}|",
                f"AUDIO {self.audio_status[:105]}",
            ])
        if self.view == "collision":
            return "\n".join([
                f"DMS-1 DEBUG / HITBOX + COLLISION{paused}",
                "DACTOR/DCOLL SEMANTIC OVERLAY: NOT AVAILABLE IN THIS RUNTIME",
                "ACTOR_spawn/ACTOR_update and COLL_bind/COLL_update are stubs in the audited libdms base.",
                "No fake hitbox is drawn. Hook reserved for the real DACTOR/DCOLL runtime.",
                f"FRAME {state['frame']} | F3=VDP F5=RAM F6=CPU F8=PAUSE F9=CAPTURE",
            ])
        if self.view == "vdp":
            worst = "  ".join(f"L{y}:{count}/{spr['scanline_limit']}" for y, count in spr["worst_scanlines"][:6]) or "none"
            over = ",".join(str(x) for x in spr["overflow_lines"][:18]) or "none"
            return "\n".join([
                f"DMS-1 DEBUG / VDP{paused} | MODE {profile.mode} {profile.name} {profile.width}x224 {profile.palettes*16} active colours / RGB333 master 512",
                f"BG A ON | BG B {'ON' if profile.bg_b_base is not None else 'OFF'} | LINE SCROLL {'ON' if profile.line_scroll else 'OFF'}",
                f"SCROLL A {self.machine.vdp.scroll_a_x},{self.machine.vdp.scroll_a_y} | B {self.machine.vdp.scroll_b_x},{self.machine.vdp.scroll_b_y}",
                f"VRAM 128 KiB fixed | CRAM {profile.palettes}x16 active | allocation ownership UNKNOWN/STATIC",
                f"SPRITES {spr['active']}/{spr['total_limit']} | PEAK SCANLINE {spr['peak_scanline']}/{spr['scanline_limit']} | OVERFLOW LINES {spr['overflow_count']}",
                f"WORST {worst}",
                f"OVERFLOW SCANLINES {over}",
                f"MODE WRITE REJECTED {int(bool(self.machine.vdp.mode_write_rejected))} | F9=CAPTURE saves sprites.csv + palettes.txt",
            ])
        if self.view == "audio":
            c = self._audio_counts(); q = int(self.audio_stats.get("tx_queue_depth", 0) or 0)
            return "\n".join([
                f"DMS-1 DEBUG / AUDIO{paused}",
                self.audio_status[:118],
                f"MUSIC {'ACTIVE' if self.machine.audio_running else 'IDLE'} | MAIL CMD ${self.machine.mailbox[0]:02X} TRACK ${self.machine.mailbox[1]:02X} STATUS ${self.machine.mailbox[2]:02X}",
                f"Z80 PC ${self.machine.cpuz80.pc:04X} HALT {int(self.machine.cpuz80.halted)} | TX QUEUE {q}",
                f"WRITES OPZ {c['OPZ']} | SSG {c['SSG']} | ADPCM-A {c['ADPCM_A']} | ADPCM-B {c['ADPCM_B']}",
                "Voice names / stealing / LINK-8 semantics: NOT EXPORTED by current audio runtime (not invented).",
            ])
        if self.view == "ram":
            r = self._ram_info()
            if r["known"]:
                return "\n".join([
                    f"DMS-1 DEBUG / WORK RAM{paused} | HARDWARE 64 KiB",
                    f"STATIC .data/.bss {r['static']/1024:.2f} KiB | STACK USED {r['stack_used']/1024:.2f} KiB | FREE GAP {r['free_gap']/1024:.2f} KiB",
                    f"_ebss ${r['ebss']:06X} | SP ${r['sp']:06X} | __stack_top ${r['stack_top']:06X}",
                    f"NONZERO BYTES {r['nonzero_bytes']} (diagnostic only; NOT treated as allocation usage)",
                    "HEAP: none/general allocator not exposed | BUFFERS/ACTORS ownership: UNKNOWN/STATIC",
                ])
            return "\n".join([
                f"DMS-1 DEBUG / WORK RAM{paused} | HARDWARE 64 KiB",
                f"STATIC / FREE / STACK BREAKDOWN: UNKNOWN/STATIC | SP ${r['sp'] & 0xFFFFFF:06X}",
                f"NONZERO BYTES {r['nonzero_bytes']} (diagnostic only)",
                "Load a GCC build with its .map beside the .dmc for _ebss/__stack_top precision.",
            ])
        if self.view == "cpu":
            d = list(getattr(self.machine.cpu68k, "d", [0] * 8)); a = list(getattr(self.machine.cpu68k, "a", [0] * 8))
            return "\n".join([
                f"DMS-1 DEBUG / MOTOROLA 68000{paused} | PC ${int(self.machine.cpu68k.pc):06X} SR ${int(self.machine.cpu68k.sr):04X}",
                "D0-D3 " + " ".join(f"{int(x)&0xFFFFFFFF:08X}" for x in d[:4]),
                "D4-D7 " + " ".join(f"{int(x)&0xFFFFFFFF:08X}" for x in d[4:8]),
                "A0-A3 " + " ".join(f"{int(x)&0xFFFFFFFF:08X}" for x in a[:4]),
                "A4-A7 " + " ".join(f"{int(x)&0xFFFFFFFF:08X}" for x in a[4:8]),
                f"FRAME ACTIVE {state.get('m68k_active',0)} cyc | WAIT {state.get('m68k_wait',0)} | BUDGET {state.get('m68k_budget',0)} | LOAD {_pct(m_now)}",
                "Disassembly/breakpoints/watchpoints: next debugger layer; Musashi core is not duplicated.",
            ])
        return "DMS-1 DEBUG"

    def _summary_text(self, reason: str, exception: BaseException | None = None) -> str:
        state = self.machine.debug_state(); spr = self.last_sprite; ram = self._ram_info()
        m_now, m_avg, m_peak, z_now = self._loads()
        lines = [
            "DMS-1 DEBUG CAPTURE V0.1", "=" * 72,
            f"Reason: {reason}", f"ROM: {self.rom_path}", f"Frame: {state['frame']}",
            f"Video: MODE {self.machine.vdp.mode} {MODE_PROFILES[self.machine.vdp.mode].name}",
            f"68000 active: {state.get('m68k_active',0)} / {state.get('m68k_budget',0)} cycles = {m_now:.2f}%",
            f"68000 wait: {state.get('m68k_wait',0)} cycles | avg120={m_avg:.2f}% peak120={m_peak:.2f}%",
            f"Z80 active: {state.get('z80_active',0)} / {state.get('z80_budget',0)} cycles = {z_now:.2f}%",
            f"Sprites: {spr['active']}/{spr['total_limit']} | scanline peak {spr['peak_scanline']}/{spr['scanline_limit']} | overflow lines {spr['overflow_count']}",
            f"GCC map: {self.map_path or 'not available'}",
            f"RAM topology known: {ram['known']}",
            f"Audio: {self.audio_status}",
        ]
        if exception is not None:
            lines += ["", "EXCEPTION", repr(exception), traceback.format_exc()]
        return "\n".join(lines) + "\n"

    def capture(self, *, reason: str = "MANUAL", exception: BaseException | None = None) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        folder = self.capture_root / f"DMS_DEBUG_CAPTURE_{stamp}"
        folder.mkdir(parents=True, exist_ok=False)
        state = self.machine.debug_state(); spr = self.last_sprite
        (folder / "summary.txt").write_text(self._summary_text(reason, exception), encoding="utf-8")
        # Exact framebuffer screenshot, no debugger overlay and no palette mutation.
        rgb = self.machine.render_video(); width = self.machine.vdp.active_width
        (folder / "screenshot.png").write_bytes(_png_rgb(width, HEIGHT, rgb))

        fields = ["frame","m68k_budget","m68k_active","m68k_wait","m68k_raw","m68k_load_pct","z80_budget","z80_active","z80_load_pct","sprites","sprite_limit","scanline_peak","scanline_limit","scanline_overflows","video_mode"]
        with (folder / "runtime.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(list(self.history))

        d = list(getattr(self.machine.cpu68k, "d", [0] * 8)); a = list(getattr(self.machine.cpu68k, "a", [0] * 8))
        cpu_lines = [f"PC ${int(self.machine.cpu68k.pc):06X}", f"SR ${int(self.machine.cpu68k.sr):04X}"]
        cpu_lines += [f"D{i} ${int(v)&0xFFFFFFFF:08X}" for i, v in enumerate(d)]
        cpu_lines += [f"A{i} ${int(v)&0xFFFFFFFF:08X}" for i, v in enumerate(a)]
        cpu_lines += [f"FRAME_ACTIVE {state.get('m68k_active',0)}", f"FRAME_WAIT {state.get('m68k_wait',0)}", f"FRAME_BUDGET {state.get('m68k_budget',0)}"]
        (folder / "cpu_68000.txt").write_text("\n".join(cpu_lines) + "\n", encoding="utf-8")

        z = self.machine.cpuz80
        (folder / "z80.txt").write_text(
            f"PC ${z.pc:04X}\nSP ${z.sp:04X}\nA ${z.a:02X}\nHALT {int(z.halted)}\nCYCLES {z.cycles}\nACTIVE_FRAME {state.get('z80_active',0)}\nBUDGET_FRAME {state.get('z80_budget',0)}\nMAILBOX {bytes(self.machine.mailbox[:16]).hex(' ')}\n",
            encoding="utf-8")

        p = MODE_PROFILES[self.machine.vdp.mode]
        (folder / "vdp.txt").write_text(
            f"MODE {p.mode} {p.name}\nWIDTH {p.width}\nPALETTES {p.palettes}\nBG_A 1\nBG_B {int(p.bg_b_base is not None)}\nLINE_SCROLL {int(p.line_scroll)}\nSCROLL_A {self.machine.vdp.scroll_a_x} {self.machine.vdp.scroll_a_y}\nSCROLL_B {self.machine.vdp.scroll_b_x} {self.machine.vdp.scroll_b_y}\nSPRITES {spr['active']} / {spr['total_limit']}\nSCANLINE_PEAK {spr['peak_scanline']} / {spr['scanline_limit']}\nOVERFLOW_LINES {','.join(map(str,spr['overflow_lines']))}\n",
            encoding="utf-8")
        with (folder / "sprites.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f); w.writerow(["scanline","sprites","limit","overflow"])
            for y, count in enumerate(spr["scanlines"]): w.writerow([y, count, spr["scanline_limit"], int(count > spr["scanline_limit"])])
        pal = []
        for i in range(128):
            val = self.machine.vdp.cram_word(i)
            pal.append(f"PAL {i//16} IDX {i%16:02d} RGB333 ${val:03X}")
        (folder / "palettes.txt").write_text("\n".join(pal) + "\n", encoding="utf-8")
        (folder / "memory.txt").write_text(json.dumps(self._ram_info(), indent=2) + "\n", encoding="utf-8")
        audio = {"status": self.audio_status, "bridge": self.audio_stats, "counts": self._audio_counts(), "mailbox_first16": list(self.machine.mailbox[:16])}
        (folder / "audio.txt").write_text(json.dumps(audio, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (folder / "recent_events.txt").write_text("\n".join(self.events) + "\n", encoding="utf-8")
        (folder / "history_120.json").write_text(json.dumps(list(self.history), indent=2) + "\n", encoding="utf-8")
        if self.map_path:
            (folder / "symbols.txt").write_text(f"map={self.map_path}\nwait_ranges={self.symbols.wait_ranges}\n_ebss={self.symbols.ebss}\n__stack_top={self.symbols.stack_top}\n", encoding="utf-8")

        zip_path = folder.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for child in sorted(folder.iterdir()): zf.write(child, child.name)
        last = self.capture_root / "DMS_DEBUG_LAST.zip"
        shutil.copyfile(zip_path, last)
        self.last_capture = zip_path
        self.log("SYSTEM", f"CAPTURE {zip_path.name}")
        return zip_path

    def crash_report(self, exc: BaseException) -> Path:
        capture = self.capture(reason="CRASH", exception=exc)
        report = self.runtime_root / "DOCS_REPORTS" / "runtime" / "DMS1_LAST_CRASH_REPORT.txt"
        report.write_text(self._summary_text("CRASH", exc) + f"Capture: {capture}\n", encoding="utf-8")
        return report
