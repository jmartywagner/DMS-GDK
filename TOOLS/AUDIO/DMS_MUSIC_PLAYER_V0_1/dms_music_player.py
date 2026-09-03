#!/usr/bin/env python3
"""DMS Music Player V0.4.0 - canonical realtime DMR playback.

DIRECT uses the single DMS-1 realtime audio engine from RUNTIME/build.
No player-local audio engine, no compile-on-launch path, no full-song WAV render.
The V0.4 contract fixes YM2414/OPZ pan as 00=L, 01=C, 10=R, 11=C.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import tempfile
from dataclasses import dataclass
from collections import deque
from pathlib import Path
from typing import Callable

SYSTEM_CLOCK = 24_000_000
OUTPUT_RATE = 44_100
TAIL_SECONDS = 0.5
INITIAL_PRIME_SECONDS = 0.095
STREAM_LEAD_SECONDS = 0.100
POLL_MS = 20
STREAM_TX_EVENT_CHUNK = 256
TOOL_DIR = Path(__file__).resolve().parent

CHANNEL_NAMES = (
    "FM 1", "FM 2", "FM 3", "FM 4",
    "SSG A", "SSG B", "SSG C",
    "ADPCM-A", "ADPCM-B",
)
MIXER_GAIN_ADDRS = {
    "fm": 0x0188,
    "ssg": 0x0189,
    "a": 0x018A,
    "b": 0x018B,
}
MIXER_DEFAULTS = {
    "fm": 12,
    "ssg": 20,
    "a": 12,
    "b": 12,
}
SOLO_KEEP = {
    "all": frozenset(("fm", "ssg", "a", "b")),
    "fm": frozenset(("fm",)),
    "ssg": frozenset(("ssg",)),
    "samples": frozenset(("a", "b")),
}
ADDRESS_TO_BUS = {address: bus for bus, address in MIXER_GAIN_ADDRS.items()}


def locate_dms_root(start: Path) -> Path:
    """Find the installed DMS-GDK root structurally, independent of folder name."""
    candidates = (start, *start.parents)
    for candidate in candidates:
        build = candidate / "RUNTIME" / "build"
        rt_ok = (build / "dms1_rt_audio.exe").is_file() or (build / "dms1_rt_audio").is_file()
        if rt_ok and (candidate / "GDK").is_dir() and (candidate / "TOOLS").is_dir():
            return candidate
    for candidate in candidates:
        if (candidate / "RUNTIME").is_dir() and (candidate / "GDK").is_dir() and (candidate / "TOOLS").is_dir():
            return candidate
    return start.parents[2]


def locate_rt_bridge(root: Path) -> Path:
    build = root / "RUNTIME" / "build"
    preferred = build / ("dms1_rt_audio.exe" if os.name == "nt" else "dms1_rt_audio")
    alternate = build / ("dms1_rt_audio" if preferred.suffix else "dms1_rt_audio.exe")
    return preferred if preferred.is_file() or not alternate.is_file() else alternate


ROOT = locate_dms_root(TOOL_DIR)
RUNTIME = ROOT / "RUNTIME"
CANONICAL_RT_BRIDGE = locate_rt_bridge(ROOT)
PLAYER_VERSION = "0.4.10"

# Binary opcode signature installed by the V0.4 add-on.  We verify the actual
# engine used for playback, instead of trusting a label or stale executable.
PAN_V04_ROUTE_SIGNATURE = bytes.fromhex(
    "48 8b 7c 24 78 41 f6 c6 01 75 0c 42 f6 44 2a 11 80 75 06 "
    "01 07 eb 0f 01 07 01 47 04 eb 08 90 90 90 90 90 90 90 90"
)
PAN_V04_EARLY_SIGNATURE = bytes.fromhex(
    "41 89 ca c1 e9 07 44 89 f6 83 e6 01 09 f1 90 90 90 90 90 90"
)

STABLE_BRIDGE_MARKER = RUNTIME / "build" / "DMS1_RT_AUDIO_STABLE_V036.txt"
LEGACY_STABLE_BRIDGE_SHA256 = "9047fc7ab51c255f208443f074aa8d1f2b1993bab5d6a853f6bfb1241586f521"
AUDIO_CORE_V080_STAMP = RUNTIME / "build" / "dms_audio_core_v080.stamp"
OFFLINE_RENDERER = RUNTIME / "build" / ("dms1emu.exe" if os.name == "nt" else "dms1emu")
AUDIO_CORE_SYNC_TOOL = RUNTIME / "tools" / "dms_audio_core_sync_v080.py"
REPORT_DIR = RUNTIME / "DOCS_REPORTS" / "music_player"

def expected_bridge_sha256() -> str:
    if AUDIO_CORE_V080_STAMP.is_file():
        try:
            for line in AUDIO_CORE_V080_STAMP.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("bridge_sha256="):
                    value = line.split("=", 1)[1].strip().lower()
                    if len(value) == 64:
                        return value
        except OSError:
            pass
    return LEGACY_STABLE_BRIDGE_SHA256


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

EXTENDED_AUDIO_MMIO = frozenset({0x018E, 0x018F, 0x0190, 0x0191, 0x0192, 0x0193})

def audio_core_v080_ready(path: Path = CANONICAL_RT_BRIDGE) -> bool:
    """Strict check for the core that accepts DAC Full V0.8 extended audio MMIO."""
    if not AUDIO_CORE_V080_STAMP.is_file() or not path.is_file():
        return False
    try:
        expected = None
        for line in AUDIO_CORE_V080_STAMP.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("bridge_sha256="):
                expected = line.split("=", 1)[1].strip().lower()
                break
        return bool(expected and len(expected) == 64 and sha256_file(path).lower() == expected)
    except OSError:
        return False

def program_requires_audio_v080(program: "DmrProgram") -> bool:
    return any(address in EXTENDED_AUDIO_MMIO for _cycle, address, _value in program.events)

def describe_mmio(address: int, value: int) -> str:
    labels = {
        0x0120: "ADPCM-A CTRL", 0x0121: "ADPCM-A PAN", 0x0122: "ADPCM-A LEVEL",
        0x0124: "ADPCM-A START LO", 0x0125: "ADPCM-A START HI",
        0x0126: "ADPCM-A END LO", 0x0127: "ADPCM-A END HI",
        0x0140: "ADPCM-B CTRL", 0x0141: "ADPCM-B PAN",
        0x018D: "SSG ROUTE GLOBAL", 0x018E: "SSG A PAN", 0x018F: "SSG B PAN",
        0x0190: "SSG C PAN", 0x0191: "ADPCM-A VOICE SELECT",
        0x0142: "ADPCM-B START LO", 0x0143: "ADPCM-B START HI",
        0x0144: "ADPCM-B END LO", 0x0145: "ADPCM-B END HI",
        0x0149: "ADPCM-B DELTA LO", 0x014A: "ADPCM-B DELTA HI", 0x014B: "ADPCM-B LEVEL",
        0x0188: "MIX FM", 0x0189: "MIX SSG", 0x018A: "MIX ADPCM-A", 0x018B: "MIX ADPCM-B",
        0x018E: "SSG-A PAN", 0x018F: "SSG-B PAN", 0x0190: "SSG-C PAN", 0x0191: "ADPCM-A VOICE SELECT",
    }
    label = labels.get(address, "MMIO")
    return f"{label} ${address:04X}=${value:02X}"

def canonical_engine_has_pan_v04(path: Path = CANONICAL_RT_BRIDGE) -> bool:
    # V0.7.4+ : the updater writes the SHA of the exact bridge it compiled and
    # probed against the extended DMS audio contract. This is authoritative and
    # intentionally compiler-independent.
    if AUDIO_CORE_V080_STAMP.is_file() and path.is_file():
        try:
            expected = expected_bridge_sha256()
            if expected and sha256_file(path).lower() == expected.lower():
                return True
        except OSError:
            return False

    # Legacy bridge families kept for backward compatibility.
    if STABLE_BRIDGE_MARKER.is_file() and path.is_file():
        try:
            data = path.read_bytes()
            if b"CAPS ANALOG90 V0.3.5" in data and b"DMS1_RT_AUDIO_ERROR:" in data:
                return True
        except OSError:
            return False
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return data.count(PAN_V04_ROUTE_SIGNATURE) == 1 and data.count(PAN_V04_EARLY_SIGNATURE) == 1


class DmrError(ValueError):
    pass


@dataclass(frozen=True)
class SampleEntry:
    sample_id: int
    codec: int
    start_page: int
    end_page: int
    source_rate: int

    @property
    def pages(self) -> int:
        return self.end_page - self.start_page + 1


@dataclass(frozen=True)
class DmrInfo:
    path: Path
    title: str
    author: str
    compiler: str
    metadata: dict[str, str]
    rom_bytes: int
    duration_seconds: float
    halt_seconds: float
    event_count: int | None
    samples_a: int
    samples_b: int
    activity_counts: dict[str, int]


@dataclass(frozen=True)
class DmrProgram:
    events: tuple[tuple[int, int, int], ...]
    halt_cycle: int
    end_cycle: int


class IntervalTracker:
    def __init__(self) -> None:
        self.intervals: dict[str, list[tuple[float, float]]] = {name: [] for name in CHANNEL_NAMES}
        self.opened: dict[str, float | None] = {name: None for name in CHANNEL_NAMES}

    def set(self, channel: str, active: bool, seconds: float) -> None:
        opened = self.opened[channel]
        if active and opened is None:
            self.opened[channel] = seconds
        elif not active and opened is not None:
            if seconds > opened:
                self.intervals[channel].append((opened, seconds))
            self.opened[channel] = None

    def retrigger(self, channel: str, seconds: float) -> None:
        self.set(channel, False, seconds)
        self.set(channel, True, seconds)

    def close_all(self, seconds: float) -> None:
        for channel in CHANNEL_NAMES:
            self.set(channel, False, seconds)


def be16(data: bytes | bytearray, offset: int) -> int:
    if offset < 0 or offset + 2 > len(data):
        raise DmrError("lecture 16 bits hors ROM")
    return struct.unpack_from(">H", data, offset)[0]


def be32(data: bytes | bytearray, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise DmrError("lecture 32 bits hors ROM")
    return struct.unpack_from(">I", data, offset)[0]


def require(data: bytes | bytearray, offset: int, size: int, label: str) -> None:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise DmrError(f"{label} hors limites")


def read_uleb(data: bytes | bytearray, cursor: int, end: int) -> tuple[int, int]:
    value = 0
    shift = 0
    for _ in range(10):
        if cursor >= end:
            raise DmrError("ULEB128 tronqué")
        byte = data[cursor]
        cursor += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, cursor
        shift += 7
    raise DmrError("ULEB128 invalide")


def parse_chunks(data: bytes | bytearray) -> dict[bytes, tuple[int, int]]:
    if len(data) < 64 or data[:4] != b"DMR0":
        raise DmrError("ce fichier n'est pas une ROM DMR")
    if data[0x10:0x14] != b"DMS1":
        raise DmrError("hardware ID différent de DMS1")
    if be32(data, 0x0C) != len(data):
        raise DmrError("taille DMR incohérente")
    if be32(data, 0x24) != SYSTEM_CLOCK:
        raise DmrError("timebase différente de 24 MHz")
    directory = be32(data, 0x18)
    count = be16(data, 0x1C)
    entry_size = be16(data, 0x1E)
    if entry_size != 16:
        raise DmrError("répertoire DMR incompatible")
    require(data, directory, count * entry_size, "répertoire")
    chunks: dict[bytes, tuple[int, int]] = {}
    for index in range(count):
        kind, offset, size, _flags = struct.unpack_from(">4sIII", data, directory + index * 16)
        require(data, offset, size, f"chunk {kind!r}")
        if kind in chunks:
            raise DmrError(f"chunk DMR dupliqué : {kind!r}")
        chunks[kind] = (offset, size)
    if b"CODE" not in chunks:
        raise DmrError("chunk CODE absent")
    return chunks


def metadata_from(data: bytes | bytearray, chunks: dict[bytes, tuple[int, int]]) -> dict[str, str]:
    result: dict[str, str] = {}
    entry = chunks.get(b"META")
    if not entry:
        return result
    offset, size = entry
    text = bytes(data[offset:offset + size]).decode("utf-8", errors="replace")
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def sample_entries(data: bytes | bytearray, chunks: dict[bytes, tuple[int, int]]) -> dict[int, SampleEntry]:
    if (b"SDIR" in chunks) != (b"SAMP" in chunks):
        raise DmrError("SDIR et SAMP doivent être présents ensemble")
    result: dict[int, SampleEntry] = {}
    if b"SDIR" not in chunks:
        return result
    offset, size = chunks[b"SDIR"]
    if size % 16:
        raise DmrError("SDIR non multiple de 16")
    for cursor in range(offset, offset + size, 16):
        sid, codec, _flags, start, end, rate, _level, _pan, _root, _fine = struct.unpack_from(
            ">HBBHHIBBBb", data, cursor
        )
        if start > end:
            raise DmrError(f"sample {sid}: pages inversées")
        result[sid] = SampleEntry(sid, codec, start, end, rate)
    return result


def decode_instruction(data: bytes | bytearray, cursor: int, code_end: int) -> tuple[int, int, dict[str, int]]:
    if not cursor < code_end:
        raise DmrError("instruction DSEQ hors CODE")
    start = cursor
    opcode = data[cursor]
    cursor += 1
    fields: dict[str, int] = {"opcode": opcode, "start": start}
    if opcode == 0x00:
        return opcode, cursor, fields
    if opcode == 0x01:
        value, cursor = read_uleb(data, cursor, code_end)
        fields["duration"] = value
        return opcode, cursor, fields
    if opcode == 0x10:
        require(data, cursor, 3, "WR8")
        fields["address"] = be16(data, cursor)
        fields["data_offset"] = cursor + 2
        return opcode, cursor + 3, fields
    if opcode == 0x11:
        require(data, cursor, 3, "WRN")
        fields["address"] = be16(data, cursor)
        length = data[cursor + 2]
        fields["length"] = length
        fields["data_offset"] = cursor + 3
        cursor += 3
        require(data, cursor, length, "données WRN")
        return opcode, cursor + length, fields
    if opcode == 0x20:
        require(data, cursor, 4, "PLAY_A")
        fields["sample_id"] = be16(data, cursor)
        fields["level"] = data[cursor + 2]
        fields["pan"] = data[cursor + 3]
        return opcode, cursor + 4, fields
    if opcode in (0x21, 0x23):
        return opcode, cursor, fields
    if opcode == 0x22:
        require(data, cursor, 7, "PLAY_B")
        fields["sample_id"] = be16(data, cursor)
        fields["delta_n"] = be16(data, cursor + 2)
        fields["level"] = data[cursor + 4]
        fields["pan"] = data[cursor + 5]
        fields["flags"] = data[cursor + 6]
        return opcode, cursor + 7, fields
    if opcode == 0x30:
        require(data, cursor, 4, "JUMP")
        fields["target"] = be32(data, cursor)
        return opcode, cursor + 4, fields
    if opcode == 0x31:
        require(data, cursor, 7, "LOOP")
        fields["slot"] = data[cursor]
        fields["count"] = be16(data, cursor + 1)
        fields["target"] = be32(data, cursor + 3)
        return opcode, cursor + 7, fields
    raise DmrError(f"opcode DSEQ inconnu ${opcode:02X} à ${start:08X}")


def expand_play_a(cycle: int, sample: SampleEntry, level: int, pan: int) -> list[tuple[int, int, int]]:
    return [
        (cycle, 0x0120, 0x02),
        (cycle, 0x0121, pan),
        (cycle, 0x0122, level),
        (cycle, 0x0124, sample.start_page & 0xFF),
        (cycle, 0x0125, (sample.start_page >> 8) & 0xFF),
        (cycle, 0x0126, sample.end_page & 0xFF),
        (cycle, 0x0127, (sample.end_page >> 8) & 0xFF),
        (cycle, 0x0120, 0x01),
    ]


def expand_play_b(cycle: int, sample: SampleEntry, delta_n: int, level: int, pan: int, flags: int) -> list[tuple[int, int, int]]:
    return [
        (cycle, 0x0140, 0x01),
        (cycle, 0x0141, pan),
        (cycle, 0x0142, sample.start_page & 0xFF),
        (cycle, 0x0143, (sample.start_page >> 8) & 0xFF),
        (cycle, 0x0144, sample.end_page & 0xFF),
        (cycle, 0x0145, (sample.end_page >> 8) & 0xFF),
        (cycle, 0x0149, delta_n & 0xFF),
        (cycle, 0x014A, (delta_n >> 8) & 0xFF),
        (cycle, 0x014B, level),
        (cycle, 0x0140, 0x80 | (0x10 if (flags & 1) else 0)),
    ]


def analyse_dmr(path: Path) -> tuple[DmrInfo, DmrProgram]:
    path = path.resolve()
    data = path.read_bytes()
    chunks = parse_chunks(data)
    meta = metadata_from(data, chunks)
    samples = sample_entries(data, chunks)
    code_offset, code_size = chunks[b"CODE"]
    code_end = code_offset + code_size
    cursor = be32(data, 0x20)
    if not code_offset <= cursor < code_end:
        raise DmrError("entrypoint hors CODE")

    tracker = IntervalTracker()
    events: list[tuple[int, int, int]] = []
    loops: dict[int, tuple[int, int]] = {}
    cycle = 0
    a_end: float | None = None
    b_end: float | None = None
    instruction_budget = 4_000_000

    def now() -> float:
        return cycle / SYSTEM_CLOCK

    def expire_samples(seconds: float) -> None:
        nonlocal a_end, b_end
        if a_end is not None and a_end <= seconds:
            tracker.set("ADPCM-A", False, a_end)
            a_end = None
        if b_end is not None and b_end <= seconds:
            tracker.set("ADPCM-B", False, b_end)
            b_end = None

    def observe_mmio(address: int, value: int, seconds: float) -> None:
        if 0x0020 <= address <= 0x0023:
            tracker.set(f"FM {address - 0x001F}", bool(value & 0x40), seconds)
        elif 0x0108 <= address <= 0x010A:
            tracker.set(f"SSG {chr(ord('A') + address - 0x0108)}", bool(value & 0x1F), seconds)

    halt_cycle: int | None = None
    while instruction_budget:
        instruction_budget -= 1
        if not code_offset <= cursor < code_end:
            raise DmrError("exécution DSEQ hors CODE")
        expire_samples(now())
        instruction_pc = cursor
        opcode, next_cursor, fields = decode_instruction(data, cursor, code_end)
        cursor = next_cursor

        if opcode == 0x00:
            halt_cycle = cycle
            break
        if opcode == 0x01:
            cycle += fields["duration"]
            continue
        if opcode == 0x10:
            address = fields["address"]
            value = data[fields["data_offset"]]
            events.append((cycle, address, value))
            observe_mmio(address, value, now())
            continue
        if opcode == 0x11:
            address = fields["address"]
            off = fields["data_offset"]
            for index in range(fields["length"]):
                value = data[off + index]
                target = address + index
                events.append((cycle, target, value))
                observe_mmio(target, value, now())
            continue
        if opcode == 0x20:
            sample = samples.get(fields["sample_id"])
            if sample is None or sample.codec != 1:
                raise DmrError("PLAY_A cible un sample invalide")
            events.extend(expand_play_a(cycle, sample, fields["level"], fields["pan"]))
            tracker.retrigger("ADPCM-A", now())
            a_end = now() + sample.pages * 512 / (8_000_000 / 432)
            continue
        if opcode == 0x21:
            events.append((cycle, 0x0120, 0x02))
            tracker.set("ADPCM-A", False, now())
            a_end = None
            continue
        if opcode == 0x22:
            sample = samples.get(fields["sample_id"])
            if sample is None or sample.codec != 2 or fields["delta_n"] == 0:
                raise DmrError("PLAY_B cible un sample invalide")
            events.extend(expand_play_b(
                cycle, sample, fields["delta_n"], fields["level"], fields["pan"], fields["flags"]
            ))
            tracker.retrigger("ADPCM-B", now())
            if fields["flags"] & 1:
                b_end = None
            else:
                rate = (8_000_000 / 144) * fields["delta_n"] / 65536
                b_end = now() + sample.pages * 512 / rate
            continue
        if opcode == 0x23:
            events.append((cycle, 0x0140, 0x00))
            tracker.set("ADPCM-B", False, now())
            b_end = None
            continue
        if opcode == 0x30:
            target = fields["target"]
            if not code_offset <= target < code_end:
                raise DmrError("cible JUMP hors CODE")
            cursor = target
            continue
        if opcode == 0x31:
            slot = fields["slot"]
            count_value = fields["count"]
            target = fields["target"]
            if slot >= 8 or count_value == 0 or not code_offset <= target < code_end:
                raise DmrError("paramètres LOOP invalides")
            state = loops.get(slot)
            if state is None or state[0] != instruction_pc:
                state = (instruction_pc, count_value)
            if state[1] > 1:
                loops[slot] = (state[0], state[1] - 1)
                cursor = target
            else:
                loops.pop(slot, None)
            continue

    if halt_cycle is None:
        raise DmrError("DSEQ sans HALT ou budget d'analyse dépassé")

    halt_seconds = halt_cycle / SYSTEM_CLOCK
    duration_seconds = halt_seconds + TAIL_SECONDS
    expire_samples(duration_seconds)
    tracker.close_all(duration_seconds)
    counts = {name: len(tracker.intervals[name]) for name in CHANNEL_NAMES}
    event_count = int(meta["event_count"]) if meta.get("event_count", "").isdigit() else None
    end_cycle = halt_cycle + int(round(TAIL_SECONDS * SYSTEM_CLOCK))

    info = DmrInfo(
        path=path,
        title=meta.get("title", path.stem),
        author=meta.get("author", "Auteur non renseigné"),
        compiler=meta.get("compiler", "DMR compiler non renseigné"),
        metadata=meta,
        rom_bytes=len(data),
        duration_seconds=duration_seconds,
        halt_seconds=halt_seconds,
        event_count=event_count,
        samples_a=sum(sample.codec == 1 for sample in samples.values()),
        samples_b=sum(sample.codec == 2 for sample in samples.values()),
        activity_counts=counts,
    )
    return info, DmrProgram(tuple(events), halt_cycle, end_cycle)


def format_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    minutes, rem = divmod(total_ms, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{minutes:02d}:{secs:02d}.{millis:03d}"


class WindowsFileDrop:
    """Pure-ctypes WM_DROPFILES support; no tkinterdnd dependency."""
    WM_DROPFILES = 0x0233
    GWLP_WNDPROC = -4

    def __init__(self, root, callback: Callable[[Path], None]) -> None:
        self.root = root
        self.callback = callback
        self.enabled = False
        self.old_proc = None
        self.proc = None
        if os.name != "nt":
            return
        root.update_idletasks()
        hwnd = int(root.winfo_id())
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        LONG_PTR = ctypes.c_ssize_t
        WPARAM = ctypes.c_size_t
        LPARAM = ctypes.c_ssize_t
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_void_p, ctypes.c_uint, WPARAM, LPARAM)
        SetWindowLongPtr = user32.SetWindowLongPtrW
        SetWindowLongPtr.argtypes = [ctypes.c_void_p, ctypes.c_int, LONG_PTR]
        SetWindowLongPtr.restype = LONG_PTR
        CallWindowProc = user32.CallWindowProcW
        CallWindowProc.argtypes = [LONG_PTR, ctypes.c_void_p, ctypes.c_uint, WPARAM, LPARAM]
        CallWindowProc.restype = LRESULT
        shell32.DragAcceptFiles.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
        shell32.DragQueryFileW.restype = ctypes.c_uint
        shell32.DragFinish.argtypes = [ctypes.c_void_p]

        @WNDPROC
        def wndproc(hwnd_arg, msg, wparam, lparam):
            if msg == self.WM_DROPFILES:
                hdrop = ctypes.c_void_p(wparam)
                try:
                    count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
                    if count:
                        needed = shell32.DragQueryFileW(hdrop, 0, None, 0) + 1
                        buf = ctypes.create_unicode_buffer(needed)
                        shell32.DragQueryFileW(hdrop, 0, buf, needed)
                        dropped = Path(buf.value)
                        root.after(0, lambda p=dropped: callback(p))
                finally:
                    shell32.DragFinish(hdrop)
                return 0
            return CallWindowProc(self.old_proc, hwnd_arg, msg, wparam, lparam)

        self.proc = wndproc
        new_ptr = ctypes.cast(self.proc, ctypes.c_void_p).value
        ctypes.set_last_error(0)
        self.old_proc = SetWindowLongPtr(ctypes.c_void_p(hwnd), self.GWLP_WNDPROC, LONG_PTR(new_ptr))
        if not self.old_proc and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        shell32.DragAcceptFiles(ctypes.c_void_p(hwnd), True)
        self.hwnd = hwnd
        self.shell32 = shell32
        self.SetWindowLongPtr = SetWindowLongPtr
        self.LONG_PTR = LONG_PTR
        self.enabled = True

    def close(self) -> None:
        if self.enabled and self.old_proc:
            try:
                self.shell32.DragAcceptFiles(ctypes.c_void_p(self.hwnd), False)
                self.SetWindowLongPtr(ctypes.c_void_p(self.hwnd), self.GWLP_WNDPROC, self.LONG_PTR(self.old_proc))
            except Exception:
                pass
        self.enabled = False


class RealtimeAudioClient:
    """Async stdin/stdout bridge to dms1_rt_audio.exe without console windows."""
    def __init__(self, executable: Path, sample_rom: Path, cwd: Path) -> None:
        if os.name != "nt":
            raise RuntimeError("le moteur waveOut temps réel est réservé à Windows")
        flags = subprocess.CREATE_NO_WINDOW
        env = os.environ.copy()
        env["PATH"] = str(executable.parent) + os.pathsep + str(RUNTIME / "build") + os.pathsep + env.get("PATH", "")
        self.proc = subprocess.Popen(
            [str(executable), str(sample_rom)], cwd=str(cwd), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=flags,
        )
        if self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("impossible d'ouvrir les pipes du moteur temps réel")
        self.status: queue.Queue[str] = queue.Queue()
        self.errors: queue.Queue[str] = queue.Queue()
        self.tx: queue.Queue[str | None] = queue.Queue(maxsize=1024)
        self.closed = False
        self.writer_error: str | None = None
        self.exit_reported = False
        self.command = [str(executable), str(sample_rom)]
        self.executable = executable
        self.sample_rom = sample_rom
        self.started_monotonic = time.perf_counter()
        self.stdout_tail: deque[str] = deque(maxlen=32)
        self.stderr_tail: deque[str] = deque(maxlen=32)
        self.total_events_sent = 0
        self.last_feed_count = 0
        self.last_feed_first: tuple[int, int, int] | None = None
        self.last_feed_last: tuple[int, int, int] | None = None
        self.last_fed_until = 0
        threading.Thread(target=self._read_stdout, daemon=True, name="dms-player-rt-out").start()
        threading.Thread(target=self._read_stderr, daemon=True, name="dms-player-rt-err").start()
        threading.Thread(target=self._writer, daemon=True, name="dms-player-rt-writer").start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            text = line.strip()
            if text:
                self.stdout_tail.append(text)
                self.status.put(text)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            text = line.strip()
            if text:
                self.stderr_tail.append(text)
                self.errors.put(text)

    def _writer(self) -> None:
        assert self.proc.stdin is not None
        while True:
            payload = self.tx.get()
            if payload is None:
                return
            try:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"moteur temps réel arrêté ({self.proc.returncode})")
                self.proc.stdin.write(payload)
                self.proc.stdin.flush()
            except Exception as exc:
                self.writer_error = str(exc)
                self.errors.put("writer: " + self.writer_error)
                return

    def send(self, payload: str) -> None:
        if self.closed or self.proc.poll() is not None:
            raise RuntimeError("moteur temps réel arrêté")
        if self.writer_error:
            raise RuntimeError(self.writer_error)
        try:
            self.tx.put_nowait(payload)
        except queue.Full as exc:
            raise RuntimeError("file d'événements audio saturée") from exc

    def play_reset(self) -> None:
        self.send("P\n")

    def stop(self) -> None:
        self.send("S\n")

    def feed(self, events: list[tuple[int, int, int]], fed_until: int) -> None:
        # Do not push a huge multi-thousand-line payload through one Windows pipe
        # write.  Chunking keeps the protocol identical while reducing burst pressure.
        self.last_feed_count = len(events)
        self.last_feed_first = events[0] if events else None
        self.last_feed_last = events[-1] if events else None
        self.last_fed_until = int(fed_until)
        self.total_events_sent += len(events)
        for start in range(0, len(events), STREAM_TX_EVENT_CHUNK):
            part = events[start:start + STREAM_TX_EVENT_CHUNK]
            payload = "".join(
                f"E {int(cycle)} {int(address):04x} {int(data):02x}\n"
                for cycle, address, data in part
            )
            if payload:
                self.send(payload)
        self.send(f"F {int(fed_until)}\n")

    def diagnostic_snapshot(self) -> dict[str, object]:
        try:
            digest = sha256_file(self.executable)
        except Exception as exc:
            digest = f"ERROR:{exc}"
        return {
            "command": list(self.command),
            "executable": str(self.executable),
            "executable_sha256": digest,
            "expected_stable_sha256": expected_bridge_sha256(),
            "sha256_matches_expected": digest == expected_bridge_sha256(),
            "returncode": self.proc.poll(),
            "uptime_seconds": round(time.perf_counter() - self.started_monotonic, 3),
            "total_events_sent": self.total_events_sent,
            "last_feed_count": self.last_feed_count,
            "last_feed_first": self.last_feed_first,
            "last_feed_last": self.last_feed_last,
            "last_fed_until": self.last_fed_until,
            "stdout_tail": list(self.stdout_tail),
            "stderr_tail": list(self.stderr_tail),
            "writer_error": self.writer_error,
        }

    def poll_status(self) -> list[str]:
        out: list[str] = []
        while True:
            try:
                out.append(self.status.get_nowait())
            except queue.Empty:
                break
        while True:
            try:
                out.append("ERROR " + self.errors.get_nowait())
            except queue.Empty:
                break
        if self.proc.poll() not in (None, 0) and not self.exit_reported:
            self.exit_reported = True
            out.append(f"ERROR bridge exited {self.proc.returncode}")
        return out

    def close(self) -> None:
        if self.closed:
            return
        try:
            self.tx.put_nowait("Q\n")
            self.tx.put_nowait(None)
        except queue.Full:
            pass
        self.closed = True
        try:
            self.proc.wait(timeout=1.0)
        except Exception:
            self.proc.kill()


class PlayerApp:
    BG = "#080c0a"
    PANEL = "#0d1511"
    GREEN = "#7dff9b"
    DIM = "#62816d"
    WHITE = "#d8e7dc"
    CYAN = "#67dfff"
    AMBER = "#ffd27a"
    RED = "#ff7a7a"

    def __init__(self, initial: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox

        self.tk = tk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.root = tk.Tk()
        self.root.title("DMS Music Player")
        self.root.geometry("900x610")
        self.root.minsize(760, 520)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.queue: queue.Queue[tuple] = queue.Queue()
        self.generation = 0
        self.info: DmrInfo | None = None
        self.program: DmrProgram | None = None
        self.client: RealtimeAudioClient | None = None
        self.event_index = 0
        self.sent_until_cycle = 0
        self.last_scheduled_cycle = 0
        self.base_gains = dict(MIXER_DEFAULTS)
        self.current_mode = "all"
        self.loop_enabled = False
        self.transport_running = False
        self.paused = False
        self.position_seconds = 0.0
        self.started_at = 0.0
        self.runtime_status = "IDLE"
        self.drop_target = None
        self.mode_buttons: dict[str, object] = {}
        self.engine_bridge = CANONICAL_RT_BRIDGE
        self.engine_state = "canonical"
        self.bridge_failure_handled = False
        self.fallback_mode = False
        self.fallback_running = False
        self.fallback_wav: Path | None = None
        self.fallback_started_at = 0.0
        self.fallback_rendering = False
        self.last_bridge_report: Path | None = None
        self.sync_auto_resume = False

        self._build_ui()
        try:
            self.drop_target = WindowsFileDrop(self.root, self.drop_file)
        except Exception as exc:
            self.log(f"[DROP] WM_DROPFILES indisponible : {exc}", self.AMBER)
        self.root.after(POLL_MS, self._poll)
        self._prepare_engine_state()
        if initial is not None:
            self.root.after(120, lambda: self.load_dmr(initial))

    def _build_ui(self) -> None:
        tk = self.tk
        font = ("Consolas", 10)
        title_font = ("Consolas", 14, "bold")
        head = tk.Frame(self.root, bg=self.BG)
        head.pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(head, text="DMS MUSIC PLAYER // V0.4.6 CANONICAL", bg=self.BG, fg=self.GREEN, font=title_font).pack(anchor="w")
        tk.Label(head, text="drop .dmr here  |  44.1 kHz / 16-bit stereo  |  DMS-1 HARDWARE", bg=self.BG, fg=self.DIM, font=font).pack(anchor="w", pady=(2, 0))

        self.console = tk.Text(
            self.root, bg=self.PANEL, fg=self.WHITE, insertbackground=self.GREEN,
            font=font, relief="flat", wrap="word", height=21, padx=12, pady=10,
            state="disabled", selectbackground="#294f39",
        )
        self.console.pack(fill="both", expand=True, padx=16, pady=8)
        for tag, color in (
            ("green", self.GREEN), ("cyan", self.CYAN), ("amber", self.AMBER),
            ("red", self.RED), ("dim", self.DIM), ("white", self.WHITE),
        ):
            self.console.tag_configure(tag, foreground=color)

        transport = tk.Frame(self.root, bg=self.BG)
        transport.pack(fill="x", padx=16, pady=(2, 4))
        self._button(transport, "OUVRIR", self.choose_file).pack(side="left", padx=(0, 5))
        self._button(transport, "PLAY", self.play).pack(side="left", padx=5)
        self._button(transport, "PAUSE", self.pause).pack(side="left", padx=5)
        self._button(transport, "RESTART", self.restart).pack(side="left", padx=5)
        self._button(transport, "EXPORT WAV", self.export_wav).pack(side="left", padx=5)
        self.loop_button = self._button(transport, "LOOP OFF", self.toggle_loop)
        self.loop_button.pack(side="left", padx=5)
        self.color_button = self._button(transport, "MODE: DIRECT V0.4", lambda: None)
        self.color_button.pack(side="left", padx=5)
        self.color_button.configure(state="disabled", disabledforeground=self.DIM)
        self.sync_button = self._button(transport, "SYNC AUDIO", self.sync_audio_core)
        self.sync_button.pack(side="left", padx=5)

        modes = tk.Frame(self.root, bg=self.BG)
        modes.pack(fill="x", padx=16, pady=(2, 6))
        for mode, label in (("all", "ALL"), ("fm", "FM SOLO"), ("ssg", "SSG SOLO"), ("samples", "SAMPLES SOLO")):
            button = self._button(modes, label, lambda m=mode: self.request_mode(m))
            button.pack(side="left", padx=(0, 6))
            self.mode_buttons[mode] = button

        status = tk.Frame(self.root, bg=self.BG)
        status.pack(fill="x", padx=16, pady=(0, 12))
        self.time_var = tk.StringVar(value="00:00.000 / 00:00.000")
        self.status_var = tk.StringVar(value="DROP A .DMR FILE OR CLICK OUVRIR")
        tk.Label(status, textvariable=self.time_var, bg=self.BG, fg=self.CYAN, font=font).pack(side="left")
        tk.Label(status, textvariable=self.status_var, bg=self.BG, fg=self.DIM, font=font).pack(side="right")
        self._refresh_mode_buttons()
        self._print_banner()

    def _button(self, parent, text: str, command):
        return self.tk.Button(
            parent, text=text, command=command, bg="#142019", fg=self.GREEN,
            activebackground="#223429", activeforeground=self.WHITE,
            relief="flat", bd=0, padx=12, pady=6, font=("Consolas", 9, "bold"),
            highlightthickness=1, highlightbackground="#294332",
        )

    def _print_banner(self) -> None:
        self.clear_console()
        self.log("DAC MASTER / DMS-1", self.GREEN)
        self.log("------------------------------------------------------------", self.DIM)
        self.log("Lecteur temps réel de ROM musicales .dmr", self.WHITE)
        self.log("Moteur : RealtimeCore + ymfm + Yamaha ADPCM-A/B + waveOut", self.WHITE)
        self.log("Temps réel prioritaire ; rendu WAV de secours automatique si le bridge tombe.", self.CYAN)
        self.log("MODE DIRECT V0.4 = moteur DMS-1 canonique / OPZ L-C-R corrigé.", self.WHITE)
        self.log("Dépose un fichier .dmr : le core audio V0.8 est synchronisé automatiquement si nécessaire.", self.CYAN)

    def clear_console(self) -> None:
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def log(self, text: str, color: str | None = None) -> None:
        tag = "white"
        if color == self.GREEN: tag = "green"
        elif color == self.CYAN: tag = "cyan"
        elif color == self.AMBER: tag = "amber"
        elif color == self.RED: tag = "red"
        elif color == self.DIM: tag = "dim"
        self.console.configure(state="normal")
        self.console.insert("end", text + "\n", tag)
        self.console.see("end")
        self.console.configure(state="disabled")

    def choose_file(self) -> None:
        filename = self.filedialog.askopenfilename(
            title="Ouvrir une musique DMS",
            filetypes=(("DMS Music ROM", "*.dmr"), ("Tous les fichiers", "*.*")),
        )
        if filename:
            self.load_dmr(Path(filename))

    def drop_file(self, path: Path) -> None:
        if path.suffix.lower() != ".dmr":
            self.status_var.set("FICHIER REFUSÉ - .DMR ATTENDU")
            self.log(f"[REFUS] {path.name} : extension .dmr attendue", self.RED)
            return
        self.load_dmr(path)

    def _display_info(self, info: DmrInfo, program: DmrProgram) -> None:
        self.clear_console()
        active_fm = sum(info.activity_counts[f"FM {i}"] > 0 for i in range(1, 5))
        active_ssg = sum(info.activity_counts[f"SSG {c}"] > 0 for c in "ABC")
        event_text = f"{info.event_count:,}" if info.event_count is not None else "n/a"
        lines = [
            ("[DMS MUSIC ROM LOADED]", self.GREEN),
            (f"FILE       : {info.path}", self.WHITE),
            (f"TITLE      : {info.title}", self.CYAN),
            (f"AUTHOR     : {info.author}", self.WHITE),
            ("FORMAT     : DMR 0.1 / DMS1 / DSEQ", self.WHITE),
            ("CLOCK      : 24.000 MHz", self.WHITE),
            ("OUTPUT     : 44.100 Hz / 16-bit stereo / DMS-1 HARDWARE", self.WHITE),
            ("MODE       : DIRECT V0.4 / moteur canonique", self.WHITE),
            (f"FM         : OPZ (ymfm) / {active_fm}/4 channels active", self.WHITE),
            (f"SSG        : YM2149 (ymfm) / {active_ssg}/3 channels active", self.WHITE),
            (f"SAMPLES    : ADPCM-A {info.samples_a} resource(s) / ADPCM-B {info.samples_b} resource(s)", self.WHITE),
            (f"EVENTS     : {event_text} source / {len(program.events):,} MMIO realtime", self.WHITE),
            (f"DURATION   : {format_time(info.duration_seconds)}", self.WHITE),
            (f"ROM SIZE   : {info.rom_bytes:,} bytes", self.WHITE),
            (f"COMPILER   : {info.compiler}", self.DIM),
            ("------------------------------------------------------------", self.DIM),
            ("[REALTIME] DSEQ transport -> RealtimeCore canonique -> waveOut", self.GREEN),
            ("OPZ PAN    : 00=LEFT / 01=CENTER / 10=RIGHT / 11=CENTER", self.CYAN),
        ]
        for text, color in lines:
            self.log(text, color)
        try:
            bridge_hash = sha256_file(CANONICAL_RT_BRIDGE)
            state = "OK" if bridge_hash == expected_bridge_sha256() else "DIFFÉRENT"
            self.log(f"BRIDGE     : {CANONICAL_RT_BRIDGE}", self.DIM)
            self.log(f"BRIDGE SHA : {bridge_hash} [{state}]", self.GREEN if state == "OK" else self.AMBER)
        except Exception as exc:
            self.log(f"BRIDGE SHA : impossible ({exc})", self.RED)
        self.log(f"RENDER WAV : {OFFLINE_RENDERER if OFFLINE_RENDERER.is_file() else 'ABSENT'}", self.DIM)

    def load_dmr(self, path: Path) -> None:
        if not path.is_file():
            self.messagebox.showerror("DMS Music Player", f"Fichier introuvable :\n{path}")
            return
        if path.suffix.lower() != ".dmr":
            self.drop_file(path)
            return
        if not CANONICAL_RT_BRIDGE.is_file():
            self.messagebox.showerror(
                "DMS Music Player",
                f"Moteur temps réel DMS absent :\n{CANONICAL_RT_BRIDGE}\n\nLe GDK doit contenir RUNTIME\\build\\dms1_rt_audio.exe.",
            )
            return

        self._stop_fallback_audio()
        self._close_client()
        self.generation += 1
        generation = self.generation
        self.info = None
        self.program = None
        self.position_seconds = 0.0
        self.transport_running = False
        self.paused = False
        self.bridge_failure_handled = False
        self.fallback_mode = False
        self.fallback_running = False
        self.fallback_wav = None
        self.fallback_rendering = False
        self.time_var.set("00:00.000 / 00:00.000")
        self.status_var.set("ANALYSE DMR...")
        self.clear_console()
        self.log(f"> LOAD {path}", self.GREEN)

        def worker() -> None:
            try:
                started = time.perf_counter()
                info, program = analyse_dmr(path)
                elapsed = time.perf_counter() - started
                self.queue.put(("loaded", generation, info, program, elapsed))
            except Exception as exc:
                self.queue.put(("error", generation, str(exc)))

        threading.Thread(target=worker, name="dms-player-analyse", daemon=True).start()

    def _start_realtime(self) -> None:
        if not self.info or not self.program:
            return
        self._close_client()
        if program_requires_audio_v080(self.program):
            if not audio_core_v080_ready():
                raise RuntimeError(
                    "ce DMR utilise le contrat audio DAC Full V0.8 mais le runtime GDK est ancien ; synchronisation requise"
                )
        elif not canonical_engine_has_pan_v04():
            raise RuntimeError(
                "moteur DMS audio V0.4 non détecté : le Player refuse de lire avec l'ancien routage OPZ"
            )
        self.client = RealtimeAudioClient(CANONICAL_RT_BRIDGE, self.info.path, RUNTIME)
        self.bridge_failure_handled = False
        self.fallback_mode = False
        self.log(f"[ENGINE] lancement : {CANONICAL_RT_BRIDGE}", self.DIM)
        self._reset_stream_state()
        self.client.play_reset()
        prime = min(self.program.end_cycle, int(INITIAL_PRIME_SECONDS * SYSTEM_CLOCK))
        self._feed_to(prime)
        self.transport_running = True
        self.paused = False
        self.position_seconds = 0.0
        self.started_at = time.perf_counter()
        self.runtime_status = "PRIMING"
        self.status_var.set("PLAYBACK : ALL / DIRECT V0.4 / REALTIME")

    def _reset_stream_state(self) -> None:
        self.event_index = 0
        self.sent_until_cycle = 0
        self.last_scheduled_cycle = 0
        self.base_gains = dict(MIXER_DEFAULTS)

    def _masked_gain(self, bus: str, value: int) -> int:
        return value if bus in SOLO_KEEP[self.current_mode] else 0x80

    def _feed_to(self, target_cycle: int) -> None:
        if not self.client or not self.program:
            return
        target_cycle = max(self.sent_until_cycle, int(target_cycle))
        outgoing: list[tuple[int, int, int]] = []
        # At a fresh core reset, establish the requested bus isolation before
        # the first musical write. Source mixer automation later in the same
        # block is still transformed below, so solo cannot leak at startup.
        if self.event_index == 0 and self.sent_until_cycle == 0:
            outgoing.extend(
                (0, MIXER_GAIN_ADDRS[bus], self._masked_gain(bus, self.base_gains[bus]))
                for bus in ("fm", "ssg", "a", "b")
            )
        events = self.program.events
        index = self.event_index
        while index < len(events) and events[index][0] <= target_cycle:
            cycle, address, value = events[index]
            bus = ADDRESS_TO_BUS.get(address)
            if bus is not None:
                self.base_gains[bus] = value
                value = self._masked_gain(bus, value)
            outgoing.append((cycle, address, value))
            self.last_scheduled_cycle = max(self.last_scheduled_cycle, cycle)
            index += 1
        self.event_index = index
        self.client.feed(outgoing, target_cycle)
        self.sent_until_cycle = target_cycle

    def _inject_mixer_state(self) -> None:
        if not self.client:
            return
        cycle = max(self.sent_until_cycle, self.last_scheduled_cycle) + 1
        writes = [
            (cycle, MIXER_GAIN_ADDRS[bus], self._masked_gain(bus, self.base_gains[bus]))
            for bus in ("fm", "ssg", "a", "b")
        ]
        self.client.feed(writes, cycle)
        self.sent_until_cycle = cycle
        self.last_scheduled_cycle = cycle

    def request_mode(self, mode: str) -> None:
        if mode not in SOLO_KEEP or not self.info:
            return
        self.current_mode = mode
        self._refresh_mode_buttons()
        try:
            self._inject_mixer_state()
            label = "ALL" if mode == "all" else mode.upper() + " SOLO"
            self.status_var.set(f"PLAYBACK : {label} / REALTIME")
            self.log(f"[MIXER] {label}", self.CYAN)
        except Exception as exc:
            self._runtime_error(str(exc))

    def _current_position(self) -> float:
        if not self.info:
            return 0.0
        if self.fallback_mode and self.fallback_running:
            return min(self.info.duration_seconds, max(0.0, time.perf_counter() - self.fallback_started_at))
        if self.transport_running and not self.paused:
            return min(self.info.duration_seconds, max(0.0, time.perf_counter() - self.started_at))
        return min(self.info.duration_seconds, max(0.0, self.position_seconds))

    def play(self) -> None:
        if not self.info or not self.program:
            return
        if self.fallback_mode and self.fallback_wav and self.fallback_wav.is_file():
            self._play_fallback_wav()
            return
        if self.client is None or self.client.proc.poll() is not None:
            try:
                self._start_realtime()
            except Exception as exc:
                self._runtime_error(str(exc))
            return
        if self.paused:
            self.started_at = time.perf_counter() - self.position_seconds
            self.paused = False
            self.transport_running = True
            self.status_var.set("PLAYBACK : REALTIME")
        elif not self.transport_running:
            self.restart()

    def pause(self) -> None:
        if self.fallback_mode:
            self._stop_fallback_audio()
            self.status_var.set("SECOURS WAV STOP")
            return
        if not self.info or not self.transport_running or self.paused:
            return
        # Let the already-submitted short waveOut look-ahead drain, then the
        # core blocks at sent_until_cycle without losing synth/sample state.
        self.position_seconds = min(self.info.duration_seconds, self.sent_until_cycle / SYSTEM_CLOCK)
        self.paused = True
        self.status_var.set("PAUSE")
        # The bridge is deliberately not reset: it drains only the short live
        # look-ahead then waits BUFFERING, preserving FM/SSG/ADPCM state.

    def restart(self, automatic: bool = False) -> None:
        if not self.info or not self.program:
            return
        if self.fallback_mode and self.fallback_wav and self.fallback_wav.is_file():
            self._play_fallback_wav()
            return
        try:
            if self.client is None or self.client.proc.poll() is not None:
                self._start_realtime()
                return
            self._reset_stream_state()
            self.client.play_reset()
            prime_end = self.program.halt_cycle if self.loop_enabled else self.program.end_cycle
            prime = min(prime_end, int(INITIAL_PRIME_SECONDS * SYSTEM_CLOCK))
            self._feed_to(prime)
            self.transport_running = True
            self.paused = False
            self.position_seconds = 0.0
            self.started_at = time.perf_counter()
            if not automatic:
                self.status_var.set("RESTART / REALTIME")
        except Exception as exc:
            self._runtime_error(str(exc))

    def sync_audio_core(self, auto_resume: bool = False) -> None:
        if not AUDIO_CORE_SYNC_TOOL.is_file():
            self.messagebox.showerror(
                "DMS Music Player",
                f"Outil de synchronisation absent :\n{AUDIO_CORE_SYNC_TOOL}",
            )
            return
        self._stop_fallback_audio()
        self._close_client()
        self.sync_auto_resume = bool(auto_resume)
        self.sync_button.configure(state="disabled")
        self.status_var.set("SYNC AUDIO CORE...")
        self.log("[SYNC] Mise à jour du core audio DMS-1...", self.CYAN)
        self.log("[SYNC] Bridge et renderer ne seront remplacés qu'après compilation + probes PASS.", self.DIM)
        generation = self.generation

        def worker() -> None:
            try:
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                proc = subprocess.run(
                    [sys.executable, str(AUDIO_CORE_SYNC_TOOL)],
                    cwd=str(ROOT),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=flags, timeout=600,
                )
                output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
                self.queue.put(("core_sync", generation, proc.returncode, output[-12000:]))
            except Exception as exc:
                self.queue.put(("core_sync", generation, 99, str(exc)))

        threading.Thread(target=worker, name="dms-audio-core-sync", daemon=True).start()

    def _prepare_engine_state(self) -> None:
        self.engine_bridge = CANONICAL_RT_BRIDGE
        self.engine_state = "canonical"
        if canonical_engine_has_pan_v04():
            self.log("[ENGINE] DMS-1 DIRECT V0.4 prêt / OPZ L-C-R vérifié", self.GREEN)
            self.status_var.set("DIRECT V0.4 READY")
        else:
            self.log("[ENGINE] ERREUR : ancien moteur OPZ détecté", self.RED)
            self.status_var.set("ENGINE UPDATE REQUIRED")
        self.color_button.configure(text="MODE: DIRECT V0.4", state="disabled")

    def toggle_loop(self) -> None:
        self.loop_enabled = not self.loop_enabled
        self.loop_button.configure(text="LOOP ON" if self.loop_enabled else "LOOP OFF")
        self.status_var.set("LOOP ON" if self.loop_enabled else "LOOP OFF")

    def _service_stream(self) -> None:
        if self.fallback_mode:
            return
        if not self.info or not self.program or not self.client:
            return
        if self.client.proc.poll() is not None:
            return
        position = self._current_position()
        if self.transport_running and not self.paused:
            loop_end = self.info.halt_seconds
            if self.loop_enabled and position >= loop_end:
                self.restart(automatic=True)
                return
            if not self.loop_enabled and position >= self.info.duration_seconds:
                self.position_seconds = self.info.duration_seconds
                self.transport_running = False
                try:
                    self.client.stop()
                except Exception:
                    pass
                self.status_var.set("END")
                return

            stream_end = self.program.halt_cycle if self.loop_enabled else self.program.end_cycle
            target = int((position + STREAM_LEAD_SECONDS) * SYSTEM_CLOCK)
            target = min(stream_end, target)
            if target > self.sent_until_cycle:
                self._feed_to(target)

    def _write_bridge_report(self, client: RealtimeAudioClient) -> Path | None:
        if not self.info or not self.program:
            return None
        try:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            snap = client.diagnostic_snapshot()
            last = snap.get("last_feed_last")
            first = snap.get("last_feed_first")
            # Recover the most recent physical ADPCM sample selection from the
            # actual MMIO writes already scheduled by the Player.
            recent = self.program.events[max(0, self.event_index - 96):self.event_index]
            regs = {}
            for _cy, _addr, _val in recent:
                if _addr in (0x0124, 0x0125, 0x0126, 0x0127, 0x0142, 0x0143, 0x0144, 0x0145):
                    regs[_addr] = _val
            last_a_sample = None
            last_b_sample = None
            try:
                raw = self.info.path.read_bytes()
                entries = sample_entries(raw, parse_chunks(raw))
                if all(a in regs for a in (0x0124, 0x0125, 0x0126, 0x0127)):
                    a_start = regs[0x0124] | (regs[0x0125] << 8)
                    a_end = regs[0x0126] | (regs[0x0127] << 8)
                    last_a_sample = next((sid for sid, ent in entries.items() if ent.codec == 1 and ent.start_page == a_start and ent.end_page == a_end), None)
                if all(a in regs for a in (0x0142, 0x0143, 0x0144, 0x0145)):
                    b_start = regs[0x0142] | (regs[0x0143] << 8)
                    b_end = regs[0x0144] | (regs[0x0145] << 8)
                    last_b_sample = next((sid for sid, ent in entries.items() if ent.codec == 2 and ent.start_page == b_start and ent.end_page == b_end), None)
            except Exception:
                pass
            payload = {
                "player_version": PLAYER_VERSION,
                "timestamp_local": time.strftime("%Y-%m-%d %H:%M:%S"),
                "dmr": str(self.info.path),
                "dmr_bytes": self.info.rom_bytes,
                "dmr_duration_seconds": self.info.duration_seconds,
                "dmr_mmio_events": len(self.program.events),
                "player_event_index": self.event_index,
                "player_sent_until_cycle": self.sent_until_cycle,
                "player_sent_until_seconds": self.sent_until_cycle / SYSTEM_CLOCK,
                "last_adpcm_a_sample_id": last_a_sample,
                "last_adpcm_b_sample_id": last_b_sample,
                "last_event_description": describe_mmio(last[1], last[2]) if last else None,
                "first_event_description": describe_mmio(first[1], first[2]) if first else None,
                "bridge": snap,
                "offline_renderer": str(OFFLINE_RENDERER),
                "offline_renderer_present": OFFLINE_RENDERER.is_file(),
            }
            report = REPORT_DIR / "LAST_BRIDGE_FAILURE.json"
            report.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=list), encoding="utf-8")
            text = REPORT_DIR / "LAST_BRIDGE_FAILURE.txt"
            lines = [
                "DMS MUSIC PLAYER - RAPPORT BRIDGE",
                "================================",
                f"Player : {PLAYER_VERSION}",
                f"DMR : {self.info.path}",
                f"Durée : {self.info.duration_seconds:.3f} s",
                f"Événements MMIO : {len(self.program.events)}",
                f"Bridge : {snap.get('executable')}",
                f"SHA256 : {snap.get('executable_sha256')}",
                f"SHA attendu : {expected_bridge_sha256()}",
                f"SHA correspond : {snap.get('sha256_matches_expected')}",
                f"Retour bridge : {snap.get('returncode')}",
                f"Uptime bridge : {snap.get('uptime_seconds')} s",
                f"Événements envoyés : {snap.get('total_events_sent')}",
                f"Index Player : {self.event_index}",
                f"Envoyé jusqu'à : {self.sent_until_cycle} cycles / {self.sent_until_cycle / SYSTEM_CLOCK:.6f} s",
                f"Dernier paquet : {snap.get('last_feed_count')} événements",
                f"Premier événement paquet : {first}",
                f"Dernier événement paquet : {last}",
                f"Dernier événement décrit : {describe_mmio(last[1], last[2]) if last else 'n/a'}",
                f"Dernier sample ADPCM-A identifié : {last_a_sample}",
                f"Dernier sample ADPCM-B identifié : {last_b_sample}",
                "",
                "STDERR (fin) :",
                *[str(x) for x in snap.get("stderr_tail", [])],
                "",
                "STDOUT (fin) :",
                *[str(x) for x in snap.get("stdout_tail", [])],
            ]
            text.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.last_bridge_report = text
            return text
        except Exception as exc:
            self.log(f"[DIAG] impossible d'écrire le rapport : {exc}", self.RED)
            return None

    def _handle_bridge_failure(self, client: RealtimeAudioClient) -> None:
        if self.bridge_failure_handled:
            return
        self.bridge_failure_handled = True
        self.transport_running = False
        self.paused = False
        report = self._write_bridge_report(client)
        snap = client.diagnostic_snapshot()
        last = snap.get("last_feed_last")
        self.log("[DIAG] le DMR reste chargé ; le bridge temps réel a quitté.", self.AMBER)
        self.log(f"[DIAG] bridge : {snap.get('executable')}", self.DIM)
        self.log(f"[DIAG] retour : {snap.get('returncode')} / SHA attendu={snap.get('sha256_matches_expected')}", self.AMBER)
        if last:
            self.log(
                f"[DIAG] dernier paquet -> {format_time(int(last[0]) / SYSTEM_CLOCK)} / {describe_mmio(last[1], last[2])}",
                self.CYAN,
            )
        if report:
            self.log(f"[DIAG] rapport : {report}", self.CYAN)
        self.status_var.set("BRIDGE HS -> SECOURS WAV")
        self._start_fallback_render(auto_play=True)

    def _fallback_path(self) -> Path:
        assert self.info is not None
        temp_dir = Path(tempfile.gettempdir()) / "DMS_MUSIC_PLAYER"
        temp_dir.mkdir(parents=True, exist_ok=True)
        token = hashlib.sha1((str(self.info.path) + str(self.info.path.stat().st_mtime_ns)).encode("utf-8")).hexdigest()[:10]
        return temp_dir / f"{self.info.path.stem}_{token}.wav"

    def _render_wav_worker(self, destination: Path, auto_play: bool, user_export: bool) -> None:
        try:
            if not self.info:
                raise RuntimeError("aucun DMR chargé")
            if not OFFLINE_RENDERER.is_file():
                raise RuntimeError(f"renderer offline absent : {OFFLINE_RENDERER}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            # dms1emu defaults to a 60 s render budget, which is too short
            # for normal full-length DMR songs.  The player already knows the
            # exact DSEQ HALT + tail duration, so give the renderer a budget
            # derived from that analysis.  Keep the renderer's hard 600 s cap.
            render_limit_seconds = min(600, max(60, int(self.info.duration_seconds) + 2))
            cmd = [
                str(OFFLINE_RENDERER),
                str(self.info.path),
                str(destination),
                "--max-seconds",
                str(render_limit_seconds),
            ]
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            proc = subprocess.run(
                cmd, cwd=str(RUNTIME), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", creationflags=flags, timeout=240,
            )
            if proc.returncode != 0 or not destination.is_file() or destination.stat().st_size < 44:
                detail = (proc.stderr or proc.stdout or "aucun détail").strip()[-1200:]
                raise RuntimeError(f"renderer offline code {proc.returncode} : {detail}")
            self.queue.put(("wav_ready", self.generation, destination, auto_play, user_export))
        except Exception as exc:
            self.queue.put(("wav_error", self.generation, str(exc), user_export))

    def _start_fallback_render(self, auto_play: bool = True) -> None:
        if not self.info or self.fallback_rendering:
            return
        if not OFFLINE_RENDERER.is_file():
            self.log(f"[SECOURS] renderer WAV absent : {OFFLINE_RENDERER}", self.RED)
            self.status_var.set("BRIDGE HS / RENDERER ABSENT")
            return
        destination = self._fallback_path()
        if destination.is_file() and destination.stat().st_size > 44:
            self.fallback_wav = destination
            self.fallback_mode = True
            self.log(f"[SECOURS] WAV déjà prêt : {destination}", self.GREEN)
            if auto_play:
                self._play_fallback_wav()
            return
        self.fallback_rendering = True
        self.log("[SECOURS] rendu WAV offline en cours...", self.AMBER)
        self.status_var.set("RENDU WAV DE SECOURS...")
        threading.Thread(
            target=self._render_wav_worker, args=(destination, auto_play, False),
            daemon=True, name="dms-player-fallback-render",
        ).start()

    def _play_fallback_wav(self) -> None:
        if not self.fallback_wav or not self.fallback_wav.is_file():
            return
        if os.name != "nt":
            return
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
            winsound.PlaySound(str(self.fallback_wav), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            self.fallback_mode = True
            self.fallback_running = True
            self.fallback_started_at = time.perf_counter()
            self.position_seconds = 0.0
            self.status_var.set("SECOURS WAV / PLAY")
            self.log("[SECOURS] lecture WAV démarrée. Le DMR original n'est pas modifié.", self.GREEN)
        except Exception as exc:
            self.log(f"[SECOURS] lecture WAV impossible : {exc}", self.RED)

    def _stop_fallback_audio(self) -> None:
        if os.name == "nt":
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        self.fallback_running = False

    def export_wav(self) -> None:
        if not self.info:
            return
        destination = self.filedialog.asksaveasfilename(
            title="Exporter le rendu WAV DMS",
            defaultextension=".wav",
            initialfile=self.info.path.stem + ".wav",
            filetypes=(("WAV", "*.wav"),),
        )
        if not destination:
            return
        self.log(f"[EXPORT] rendu WAV -> {destination}", self.CYAN)
        threading.Thread(
            target=self._render_wav_worker, args=(Path(destination), False, True),
            daemon=True, name="dms-player-wav-export",
        ).start()

    def _refresh_mode_buttons(self) -> None:
        for mode, button in self.mode_buttons.items():
            active = mode == self.current_mode
            button.configure(bg="#2b5737" if active else "#142019", fg=self.WHITE if active else self.GREEN)

    def _runtime_error(self, message: str) -> None:
        self.status_var.set("ERROR")
        self.log("[ERROR] " + message, self.RED)
        self.messagebox.showerror("DMS Music Player", message)
        self._close_client()

    def _poll(self) -> None:
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                generation = item[1]
                if generation != self.generation:
                    continue
                if kind == "loaded":
                    self.info, self.program = item[2], item[3]
                    analysis_ms = item[4] * 1000.0
                    self.current_mode = "all"
                    self._refresh_mode_buttons()
                    self._display_info(self.info, self.program)
                    self.log(f"[ANALYSE] {analysis_ms:.0f} ms", self.DIM)
                    if program_requires_audio_v080(self.program) and not audio_core_v080_ready():
                        try:
                            old_hash = sha256_file(CANONICAL_RT_BRIDGE)
                        except Exception:
                            old_hash = "inconnu"
                        self.log("[CORE] DMR DAC Full V0.8 détecté : registres audio étendus requis.", self.AMBER)
                        self.log(f"[CORE] bridge actuel : {old_hash}", self.DIM)
                        self.log("[CORE] ancien runtime bloqué avant lecture ; synchronisation automatique.", self.CYAN)
                        self.sync_audio_core(auto_resume=True)
                    else:
                        try:
                            self._start_realtime()
                        except Exception as exc:
                            self._runtime_error(str(exc))
                elif kind == "error":
                    self._runtime_error(item[2])
                elif kind == "wav_ready":
                    destination, auto_play, user_export = item[2], item[3], item[4]
                    self.fallback_rendering = False
                    if user_export:
                        self.log(f"[EXPORT] WAV terminé : {destination}", self.GREEN)
                        self.status_var.set("EXPORT WAV OK")
                    else:
                        self.fallback_wav = destination
                        self.fallback_mode = True
                        self.log(f"[SECOURS] WAV prêt : {destination}", self.GREEN)
                        if auto_play:
                            self._play_fallback_wav()
                elif kind == "wav_error":
                    self.fallback_rendering = False
                    self.log(f"[SECOURS] {item[2]}", self.RED)
                    self.status_var.set("ERREUR RENDU WAV")
                elif kind == "core_sync":
                    rc, output = item[2], item[3]
                    self.sync_button.configure(state="normal")
                    if rc == 0:
                        self.log("[SYNC] PASS - core audio DAC Full V0.8 synchronisé.", self.GREEN)
                        for line in output.splitlines()[-12:]:
                            if line.strip():
                                self.log("[SYNC] " + line, self.DIM)
                        try:
                            new_hash = sha256_file(CANONICAL_RT_BRIDGE)
                            self.log(f"[SYNC] nouveau BRIDGE SHA : {new_hash}", self.CYAN)
                        except Exception as exc:
                            self.log(f"[SYNC] SHA impossible : {exc}", self.AMBER)
                        self._prepare_engine_state()
                        self.status_var.set("AUDIO CORE V0.8 READY")
                        resume = self.sync_auto_resume
                        self.sync_auto_resume = False
                        if resume and self.info and self.program:
                            try:
                                self.log("[SYNC] reprise automatique du DMR avec le nouveau runtime.", self.CYAN)
                                self._start_realtime()
                            except Exception as exc:
                                self._runtime_error(str(exc))
                        else:
                            self.messagebox.showinfo(
                                "DMS Music Player",
                                "Audio Core V0.8 synchronisé.\n\nLe nouveau runtime est prêt.",
                            )
                    else:
                        self.sync_auto_resume = False
                        detail = output[-3500:] if output else "aucun détail"
                        self.log(f"[SYNC] ECHEC code {rc}", self.RED)
                        for line in detail.splitlines():
                            self.log(line, self.RED)
                        self.status_var.set("SYNC AUDIO ECHEC")
                        self.messagebox.showerror(
                            "DMS Music Player",
                            "La synchronisation audio a échoué.\nL'ancien runtime a été conservé.\n\nLe détail est affiché dans le Player.",
                        )
        except queue.Empty:
            pass

        if self.client:
            client = self.client
            for status in client.poll_status():
                if status.startswith("ERROR"):
                    self.log("[RT] " + status, self.RED)
                    self.status_var.set("RUNTIME ERROR")
                else:
                    self.runtime_status = status
                    if status in ("READY", "PRIMING", "PLAYING", "BUFFERING", "STOPPED"):
                        self.log("[RT] " + status, self.DIM)
                    else:
                        self.log("[RT] " + status, self.DIM)
            if client.proc.poll() not in (None, 0):
                self._handle_bridge_failure(client)

        try:
            self._service_stream()
        except Exception as exc:
            self._runtime_error(str(exc))

        if self.info:
            position = self._current_position()
            self.time_var.set(f"{format_time(position)} / {format_time(self.info.duration_seconds)}")
        self.root.after(POLL_MS, self._poll)

    def _close_client(self) -> None:
        client = self.client
        self.client = None
        if client:
            try:
                client.close()
            except Exception:
                pass
        self.transport_running = False
        self.paused = False

    def close(self) -> None:
        self.generation += 1
        self._stop_fallback_audio()
        self._close_client()
        if self.drop_target:
            self.drop_target.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def self_test(path: Path) -> int:
    started = time.perf_counter()
    info, program = analyse_dmr(path)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    last_cycle = -1
    ordered = True
    gains_seen: set[int] = set()
    for cycle, address, _value in program.events:
        if cycle < last_cycle:
            ordered = False
            break
        last_cycle = cycle
        if address in ADDRESS_TO_BUS:
            gains_seen.add(address)
    payload = {
        "title": info.title,
        "duration_seconds": round(info.duration_seconds, 6),
        "halt_seconds": round(info.halt_seconds, 6),
        "source_event_count": info.event_count,
        "realtime_mmio_events": len(program.events),
        "ordered": ordered,
        "last_event_before_or_at_halt": last_cycle <= program.halt_cycle,
        "mixer_gain_addresses": sorted(gains_seen),
        "samples_a": info.samples_a,
        "samples_b": info.samples_b,
        "analysis_ms": round(elapsed_ms, 2),
        "player_version": PLAYER_VERSION,
        "canonical_pan_v04": canonical_engine_has_pan_v04(),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if ordered and last_cycle <= program.halt_cycle and canonical_engine_has_pan_v04() else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", nargs="?", type=Path)
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.inspect:
        if not args.rom:
            parser.error("--inspect requiert une .dmr")
        info, program = analyse_dmr(args.rom)
        print(json.dumps({
            **info.__dict__,
            "path": str(info.path),
            "realtime_mmio_events": len(program.events),
            "halt_cycle": program.halt_cycle,
            "end_cycle": program.end_cycle,
        }, ensure_ascii=False, indent=2, default=list))
        return 0
    if args.self_test:
        if not args.rom:
            parser.error("--self-test requiert une .dmr")
        return self_test(args.rom)
    if os.name != "nt":
        print("DMS Music Player : l'interface de lecture temps réel est destinée à Windows.", file=sys.stderr)
        return 2
    PlayerApp(args.rom).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
