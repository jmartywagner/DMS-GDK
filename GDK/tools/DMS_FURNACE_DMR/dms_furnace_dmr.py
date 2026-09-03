#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DMS Furnace -> DMR 0.3.1.

Bridge focused on the DMS-1 audio profile:
- Furnace .fur module (instrument/sample metadata)
- Furnace Command Stream .bin / FCS (musical timeline)
- strict DMS channel validation
- bridge to the existing DMSPROJ-0.1 compiler

No external Python dependency is required.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import shutil
import struct
import subprocess
import sys
import threading
import wave
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

APP_NAME = "DMS Furnace → DMR"
APP_VERSION = "0.3.1"
BUILD_ID = "FURNACE-DMR-0.3.1"
MIDI_PPQN = 30000
MIDI_TEMPO_US = 500000  # 120 BPM -> 60,000 MIDI ticks / second

CHIP_INFO = {
    0x80: ("AY8910", 3),
    0x98: ("OPZ/YM2414", 8),
    0xA5: ("YM2610", 14),
}

DMS_ALLOWED = {
    ("OPZ", 0): "FM1",
    ("OPZ", 1): "FM2",
    ("OPZ", 2): "FM3",
    ("OPZ", 3): "FM4",
    ("AY", 0): "SSG-A",
    ("AY", 1): "SSG-B",
    ("AY", 2): "SSG-C",
    ("YM2610", 7): "ADPCM-A",
    ("YM2610", 13): "ADPCM-B",
}

INSTRUMENT_TYPES = {
    6: "AY",
    19: "OPZ",
    37: "ADPCM-A",
    38: "ADPCM-B",
}


class ConversionError(RuntimeError):
    pass


@dataclass
class FurSample:
    index: int
    name: str
    length: int
    rate: int
    c4_rate: int
    depth: int
    loop_start: int
    loop_end: int
    pcm: bytes


@dataclass
class FurInstrument:
    index: int
    type_id: int
    kind: str
    name: str
    features: dict[str, bytes] = field(default_factory=dict)
    initial_sample: int | None = None
    sample_map: list[int | None] | None = None
    fm_patch: dict[str, Any] | None = None


@dataclass
class FurModule:
    version: int
    timebase: int
    speed1: int
    speed2: int
    tick_rate: float
    pattern_length: int
    order_length: int
    chip_ids: list[int]
    channel_descriptors: list[tuple[str, int, str]]
    instruments: list[FurInstrument]
    samples: list[FurSample]


@dataclass
class FcsEvent:
    tick: int
    order: int
    kind: str
    value: int | tuple[int, ...] | None = None
    instrument: int | None = None
    volume: int = 255
    raw_note: int | None = None


@dataclass
class FcsChannel:
    index: int
    events: list[FcsEvent]
    end_tick: int
    unsupported: list[str]

    @property
    def notes(self) -> list[FcsEvent]:
        return [e for e in self.events if e.kind == "note_on"]

    @property
    def active(self) -> bool:
        return bool(self.notes)


@dataclass
class FcsFile:
    flavor: str
    channel_count: int
    channels: list[FcsChannel]
    end_tick: int


@dataclass
class Analysis:
    module: FurModule
    fcs: FcsFile
    errors: list[str]
    warnings: list[str]
    channel_lines: list[str]

    @property
    def compatible(self) -> bool:
        return not self.errors


def u16(data: bytes, pos: int) -> int:
    return struct.unpack_from("<H", data, pos)[0]


def u32(data: bytes, pos: int) -> int:
    return struct.unpack_from("<I", data, pos)[0]


def i32(data: bytes, pos: int) -> int:
    return struct.unpack_from("<i", data, pos)[0]


def read_c_string(data: bytes, pos: int, end: int) -> tuple[str, int]:
    try:
        q = data.index(b"\0", pos, end)
    except ValueError as exc:
        raise ConversionError("Chaîne Furnace non terminée.") from exc
    return data[pos:q].decode("utf-8", errors="replace"), q + 1


def load_fur_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    if raw.startswith(b"-Furnace module-"):
        return raw
    try:
        out = zlib.decompress(raw)
    except zlib.error as exc:
        raise ConversionError("Le .fur n'est ni un module Furnace brut ni un flux zlib valide.") from exc
    if not out.startswith(b"-Furnace module-"):
        raise ConversionError("Signature Furnace absente après décompression.")
    return out


def scan_blocks(data: bytes, sig: bytes) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    p = 0
    while True:
        p = data.find(sig, p)
        if p < 0:
            break
        if p + 8 <= len(data):
            size = u32(data, p + 4)
            end = p + 8 + size
            if size >= 4 and end <= len(data):
                found.append((p, end))
        p += 1
    # remove signatures accidentally found inside an already accepted block payload
    clean: list[tuple[int, int]] = []
    for item in found:
        if not any(a < item[0] < b for a, b in clean):
            clean.append(item)
    return clean


def parse_sample_feature(blob: bytes, module_version: int) -> tuple[int | None, list[int | None] | None]:
    if len(blob) < 4:
        return None, None
    initial = u16(blob, 0)
    flags = blob[2]
    has_map = bool(flags & 1)
    if not has_map:
        return initial if initial != 0xFFFF else None, None
    count = 120 if module_version < 246 else 128
    need = 4 + count * 4
    if len(blob) < need:
        raise ConversionError(f"Mapping sample Furnace tronqué ({len(blob)} < {need}).")
    mapping: list[int | None] = []
    for idx in range(count):
        base = 4 + idx * 4
        _note_to_play = u16(blob, base)
        smp = u16(blob, base + 2)
        mapping.append(None if smp == 0xFFFF else smp)
    return initial if initial != 0xFFFF else None, mapping


def neutral_opz_patch() -> dict[str, Any]:
    # Deliberately simple OPZ fallback for Furnace instruments whose default state
    # is implicit and therefore absent from INS2. It is marked in the report.
    return {
        "display_name": "Furnace default OPZ - neutral P0.3",
        "algorithm": 7,
        "feedback": 0,
        "multiples": [1, 1, 1, 1],
        "fine": [0, 0, 0, 0],
        "waves": [0, 0, 0, 0],
        "levels": [42, 54, 66, 78],
        "attacks": [31, 31, 31, 31],
        "decays": [0, 0, 0, 0],
        "sustains": [0, 0, 0, 0],
        "sustain_levels": [0, 0, 0, 0],
        "releases": [6, 6, 6, 6],
        "reverb_rates": [0, 0, 0, 0],
        "detunes": [0, 0, 0, 0],
        "key_scale_rates": [0, 0, 0, 0],
        "fixed_modes": [0, 0, 0, 0],
        "fixed_ranges": [0, 0, 0, 0],
        "fixed_frequencies": [0, 0, 0, 0],
        "am_enables": [0, 0, 0, 0],
        "detune2": [0, 0, 0, 0],
        "eg_shifts": [0, 0, 0, 0],
        "velocity_sensitivities": [0, 0, 0, 0],
    }


def parse_fm_feature(blob: bytes, module_version: int) -> dict[str, Any] | None:
    """Decode Furnace INS2/FM exactly for the OPZ fields represented by DMS.

    Furnace stores OPZ 4-op data in internal order OP1/OP3/OP2/OP4.
    DMS-OPZ-BANK-0.1 is consumed by dms_compile in native OPZ register order.
    That order is the same 1/3/2/4 order used by Furnace for OPN/OPM/OPZ.

    For Furnace >=224 the compact FM feature contains:
      flags + 4 bytes base data + 4 * 8-byte operator records.
    """
    if len(blob) < 4:
        return None
    op_count = blob[0] & 0x0F
    if op_count != 4:
        return None

    header = 5 if module_version >= 224 else 4
    expected = header + op_count * 8
    if len(blob) < expected:
        return None

    # Reject non-padding tail bytes rather than guessing another layout.
    tail = blob[expected:]
    if tail and any(tail):
        return None

    global0 = blob[1]
    algorithm = (global0 >> 4) & 0x07
    feedback = global0 & 0x07

    raw_ops = [
        blob[header + i * 8: header + (i + 1) * 8]
        for i in range(4)
    ]
    # Furnace internal: OP1, OP3, OP2, OP4.
    # DMS compiler writes operator register groups in the same native
    # 1/3/2/4 order, so no permutation is allowed here.
    ops = raw_ops

    def values(fn):
        return [fn(op) for op in ops]

    fixed_modes = values(lambda op: (op[4] >> 7) & 0x01)

    patch = {
        "display_name": "Furnace OPZ",
        "algorithm": algorithm,
        "feedback": feedback,
        "multiples": values(lambda op: op[0] & 0x0F),
        # On OPZ Furnace reuses DVB as the static Fine control.
        "fine": values(lambda op: (op[6] >> 4) & 0x0F),
        "waves": values(lambda op: op[7] & 0x07),
        "levels": values(lambda op: op[1] & 0x7F),
        "attacks": values(lambda op: op[2] & 0x1F),
        "decays": values(lambda op: op[3] & 0x1F),
        # D2R/SR is the sustain rate. P0.1 incorrectly read the SUS flag here.
        "sustains": values(lambda op: op[4] & 0x1F),
        "sustain_levels": values(lambda op: (op[5] >> 4) & 0x0F),
        "releases": values(lambda op: op[5] & 0x0F),
        # On OPZ Furnace reuses DAM as REV.
        "reverb_rates": values(lambda op: (op[7] >> 5) & 0x07),
        "detunes": values(lambda op: (op[0] >> 4) & 0x07),
        # DMS expects the 2-bit envelope rate scaling value, not Furnace's KSR bool.
        "key_scale_rates": values(lambda op: (op[2] >> 6) & 0x03),
        "fixed_modes": fixed_modes,
        # Furnace's compact OPZ feature does not expose the TX81Z-style
        # fixed range/coarse fields separately. Never invent them.
        "fixed_ranges": [0, 0, 0, 0],
        "fixed_frequencies": [0, 0, 0, 0],
        "am_enables": values(lambda op: (op[3] >> 7) & 0x01),
        "detune2": values(lambda op: (op[7] >> 3) & 0x03),
        # On OPZ Furnace reuses KSL as EG Shift.
        "eg_shifts": values(lambda op: (op[3] >> 5) & 0x03),
        # Furnace stores KVS as an off/on/auto software mode, not the TX81Z
        # 0..7 sensitivity used by DMS. FCS note commands carry no velocity,
        # so P0.3 keeps hardware KVS neutral instead of inventing a value.
        "velocity_sensitivities": [0, 0, 0, 0],
    }

    # Fixed-frequency OPZ patches require a separate exact mapping that is not
    # present in DMSPROJ P0.4.1. Mark them so analysis can refuse them.
    if any(fixed_modes):
        patch["_furnace_fixed_frequency_unmapped"] = True
    return patch


def parse_instrument(data: bytes, off: int, end: int, index: int, module_version: int) -> FurInstrument:
    if off + 12 > end:
        raise ConversionError("Bloc INS2 tronqué.")
    _fmt_version = u16(data, off + 8)
    type_id = u16(data, off + 10)
    kind = INSTRUMENT_TYPES.get(type_id, f"TYPE-{type_id}")
    features: dict[str, bytes] = {}
    name = f"Instrument {index}"
    p = off + 12
    while p + 4 <= end:
        code_b = data[p:p+2]
        if not all(32 <= b < 127 for b in code_b):
            break
        code = code_b.decode("ascii", errors="replace")
        ln = u16(data, p + 2)
        q = p + 4 + ln
        if q > end:
            break
        payload = data[p+4:q]
        features[code] = payload
        if code == "NA":
            name = payload.split(b"\0", 1)[0].decode("utf-8", errors="replace") or name
        p = q
    inst = FurInstrument(index=index, type_id=type_id, kind=kind, name=name, features=features)
    if "SM" in features:
        inst.initial_sample, inst.sample_map = parse_sample_feature(features["SM"], module_version)
    if "FM" in features:
        inst.fm_patch = parse_fm_feature(features["FM"], module_version)
        if inst.fm_patch is not None:
            inst.fm_patch["display_name"] = name
    return inst


def parse_sample(data: bytes, off: int, end: int, index: int) -> FurSample:
    p = off + 8
    name, p = read_c_string(data, p, end)
    if p + 40 > end:
        raise ConversionError(f"Sample Furnace #{index} tronquée.")
    length, rate, c4_rate = struct.unpack_from("<III", data, p); p += 12
    depth = data[p]; p += 1
    _loop_direction = data[p]; p += 1
    _flags = data[p]; p += 1
    _flags2 = data[p]; p += 1
    loop_start, loop_end = struct.unpack_from("<ii", data, p); p += 8
    p += 16  # presence/compat bytes
    remain = end - p
    if depth == 16:
        need = length * 2
        if remain < need:
            raise ConversionError(f"Sample 16-bit #{index} tronquée ({remain} < {need}).")
        pcm = data[p:p+need]
    elif depth == 8:
        need = length
        if remain < need:
            raise ConversionError(f"Sample 8-bit #{index} tronquée ({remain} < {need}).")
        # Furnace PCM8 is signed; WAV PCM8 is unsigned.
        pcm = bytes((b + 128) & 0xFF for b in data[p:p+need])
    else:
        raise ConversionError(
            f"Sample Furnace #{index} '{name}' depth={depth}: P0.3 accepte seulement PCM 8/16 bits."
        )
    return FurSample(index, name, length, rate, c4_rate, depth, loop_start, loop_end, pcm)


def parse_fur(path: Path) -> FurModule:
    data = load_fur_bytes(path)
    version = u16(data, 16)
    info_ptr = u32(data, 20)
    if info_ptr + 64 > len(data) or data[info_ptr:info_ptr+4] != b"INFO":
        raise ConversionError("Bloc INFO Furnace introuvable.")
    p = info_ptr + 8
    timebase = data[p]; speed1 = data[p+1]; speed2 = data[p+2]; p += 4
    tick_rate = struct.unpack_from("<f", data, p)[0]; p += 4
    pattern_length = u16(data, p); p += 2
    order_length = u16(data, p); p += 2
    p += 2  # highlights
    instrument_count = u16(data, p); p += 2
    _wavetable_count = u16(data, p); p += 2
    sample_count = u16(data, p); p += 2
    _pattern_count = u32(data, p); p += 4
    chip_slots = list(data[p:p+32])
    chip_ids = [c for c in chip_slots if c]

    descriptors: list[tuple[str, int, str]] = []
    for chip in chip_ids:
        if chip not in CHIP_INFO:
            name, count = f"CHIP-0x{chip:02X}", 0
        else:
            name, count = CHIP_INFO[chip]
        if chip == 0x98:
            descriptors.extend(("OPZ", local, name) for local in range(count))
        elif chip == 0x80:
            descriptors.extend(("AY", local, name) for local in range(count))
        elif chip == 0xA5:
            descriptors.extend(("YM2610", local, name) for local in range(count))

    ins_blocks = scan_blocks(data, b"INS2")
    smp_blocks = scan_blocks(data, b"SMP2")
    if len(ins_blocks) < instrument_count:
        raise ConversionError(f"INS2: {len(ins_blocks)} blocs trouvés, {instrument_count} attendus.")
    if len(smp_blocks) < sample_count:
        raise ConversionError(f"SMP2: {len(smp_blocks)} blocs trouvés, {sample_count} attendus.")
    instruments = [parse_instrument(data, a, b, i, version) for i, (a,b) in enumerate(ins_blocks[:instrument_count])]
    samples = [parse_sample(data, a, b, i) for i, (a,b) in enumerate(smp_blocks[:sample_count])]
    return FurModule(
        version=version, timebase=timebase, speed1=speed1, speed2=speed2,
        tick_rate=tick_rate, pattern_length=pattern_length, order_length=order_length,
        chip_ids=chip_ids, channel_descriptors=descriptors,
        instruments=instruments, samples=samples,
    )


def _plausible_long_ptrs(data: bytes, channel_count: int) -> list[int] | None:
    need = 8 + 4 * channel_count
    if len(data) < need or channel_count <= 0:
        return None
    ptrs = list(struct.unpack_from("<" + "I" * channel_count, data, 8))
    if not ptrs or ptrs[0] < need or ptrs[-1] >= len(data):
        return None
    if any(a >= b for a, b in zip(ptrs, ptrs[1:])):
        return None
    return ptrs


def parse_fcs_dev_channel(data: bytes, index: int, start: int, end: int) -> FcsChannel:
    p=start; tick=0; order=0; inst=None; vol=255
    events: list[FcsEvent]=[]; unsupported=[]
    while p < end:
        pos=p; op=data[p]; p+=1; order+=1
        if op <= 0xB3:
            events.append(FcsEvent(tick, order, "note_on", op, inst, vol, op))
        elif op == 0xB4:
            pass
        elif op in (0xB5,0xB6,0xB7):
            events.append(FcsEvent(tick, order, "note_off", op, inst, vol))
        elif op == 0xB8:
            if p >= end: unsupported.append(f"0xB8 tronqué @0x{pos:X}"); break
            inst=data[p]; p+=1
            events.append(FcsEvent(tick, order, "instrument", inst, inst, vol))
        elif op == 0xC6:
            if p+2 > end: unsupported.append(f"0xC6 tronqué @0x{pos:X}"); break
            args=(data[p],data[p+1]);p+=2
            if args != (0,0): unsupported.append(f"arpège 0xC6 {args} @tick {tick}")
        elif op == 0xC7:
            if p >= end: unsupported.append(f"0xC7 tronqué @0x{pos:X}"); break
            vol=data[p];p+=1
            events.append(FcsEvent(tick, order, "volume", vol, inst, vol))
        elif op == 0xCF:
            if p+2 > end: unsupported.append(f"0xCF tronqué @0x{pos:X}"); break
            args=(data[p],data[p+1]);p+=2
            if args not in ((0,0),(0x80,0x80),(0xFF,0xFF)):
                unsupported.append(f"pan 0xCF {args} @tick {tick}")
        elif op == 0xFE:
            tick += 1
        elif op == 0xFF:
            break
        else:
            unsupported.append(f"opcode 0x{op:02X} @0x{pos:X} tick {tick}")
            break
    return FcsChannel(index, events, tick, unsupported)


def parse_fcs_current_channel(data: bytes, index: int, start: int, end: int, preset_delays: list[int], preset_ins: list[int], preset_vol: list[int]) -> FcsChannel:
    p=start; tick=0; order=0; inst=None; vol=255
    events=[]; unsupported=[]
    while p < end:
        pos=p; op=data[p];p+=1;order+=1
        if op <= 0xB3:
            events.append(FcsEvent(tick, order, "note_on", op, inst, vol, op))
        elif op == 0xB4:
            pass
        elif op in (0xB5,0xB6,0xB7):
            events.append(FcsEvent(tick, order, "note_off", op, inst, vol))
        elif op == 0xB8:
            if p>=end: unsupported.append("instrument tronqué");break
            inst=data[p];p+=1;events.append(FcsEvent(tick,order,"instrument",inst,inst,vol))
        elif op == 0xC6:
            if p>=end: unsupported.append("arpège tronqué");break
            arg=data[p];p+=1
            if arg != 0: unsupported.append(f"arpège packed 0x{arg:02X} @tick {tick}")
        elif op == 0xC7:
            if p>=end: unsupported.append("volume tronqué");break
            vol=data[p];p+=1;events.append(FcsEvent(tick,order,"volume",vol,inst,vol))
        elif op == 0xDC:
            if p+2>end: unsupported.append("wait16 tronqué");break
            tick += u16(data,p);p+=2
        elif op == 0xDD:
            if p>=end: unsupported.append("wait8 tronqué");break
            tick += data[p];p+=1
        elif op == 0xDE:
            tick += 1
        elif op == 0xDF:
            break
        elif 0xE0 <= op <= 0xE5:
            inst=preset_ins[op-0xE0]
            events.append(FcsEvent(tick,order,"instrument",inst,inst,vol))
        elif 0xE6 <= op <= 0xEB:
            vol=preset_vol[op-0xE6]
            events.append(FcsEvent(tick,order,"volume",vol,inst,vol))
        elif 0xF0 <= op <= 0xFF:
            tick += preset_delays[op-0xF0]
        else:
            unsupported.append(f"opcode 0x{op:02X} @0x{pos:X} tick {tick}")
            break
    return FcsChannel(index,events,tick,unsupported)


def parse_fcs(path: Path) -> FcsFile:
    data=path.read_bytes()
    if len(data)<8 or data[:4] != b"FCS\0":
        raise ConversionError("Signature FCS\\0 absente du .bin.")
    n=u16(data,4); flags=data[6]
    long_ptrs=_plausible_long_ptrs(data,n)
    if long_ptrs is not None:
        channels=[]
        for i,start in enumerate(long_ptrs):
            end=long_ptrs[i+1] if i+1<n else len(data)
            channels.append(parse_fcs_dev_channel(data,i,start,end))
        return FcsFile("FCS dev232 / long pointers",n,channels,max((c.end_tick for c in channels),default=0))

    # Current documented layout.
    if len(data) < 40:
        raise ConversionError("En-tête FCS trop court.")
    preset_delays=list(data[8:24])
    preset_ins=list(data[24:30])
    preset_vol=list(data[30:36])
    ptr_size=4 if (flags & 1) else 2
    endian=">" if (flags & 2) else "<"
    ptr_off=40
    need=ptr_off+ptr_size*n
    if len(data)<need:
        raise ConversionError("Table de pointeurs FCS tronquée.")
    fmt=endian+("I" if ptr_size==4 else "H")*n
    ptrs=list(struct.unpack_from(fmt,data,ptr_off))
    if not ptrs or any(p>=len(data) for p in ptrs):
        raise ConversionError("Pointeurs FCS invalides.")
    channels=[]
    for i,start in enumerate(ptrs):
        end=ptrs[i+1] if i+1<n else len(data)
        channels.append(parse_fcs_current_channel(data,i,start,end,preset_delays,preset_ins,preset_vol))
    return FcsFile("FCS current",n,channels,max((c.end_tick for c in channels),default=0))


def dms_voice_for(desc: tuple[str,int,str]) -> tuple[str | None, str]:
    kind, local, _ = desc
    voice=DMS_ALLOWED.get((kind,local))
    if voice:
        return voice, "autorisé"
    if kind=="OPZ":
        return None, f"OPZ{local+1} interdit (DMS utilise OPZ1–4 seulement)"
    if kind=="YM2610":
        if 0 <= local <= 3:
            return None, f"YM2610 FM{local+1} interdit"
        if 4 <= local <= 6:
            return None, f"YM2610 SSG{local-3} interdit (utiliser AY externe)"
        if 8 <= local <= 12:
            return None, f"ADPCM-A{local-6} interdit (DMS garde A1 seulement)"
        return None, f"YM2610 canal local {local} interdit"
    return None, f"canal {kind}{local+1} non supporté"


def analyze(fur_path: Path, fcs_path: Path) -> Analysis:
    module=parse_fur(fur_path); fcs=parse_fcs(fcs_path)
    errors=[];warnings=[];lines=[]
    if len(module.channel_descriptors)!=fcs.channel_count:
        errors.append(
            f"Nombre de canaux différent : .fur={len(module.channel_descriptors)}, FCS={fcs.channel_count}."
        )
    count=min(len(module.channel_descriptors),fcs.channel_count)
    for i in range(count):
        desc=module.channel_descriptors[i]; ch=fcs.channels[i]
        voice,reason=dms_voice_for(desc)
        if desc[0] == "YM2610":
            local = desc[1]
            if local == 7:
                chip_label = "YM2610-A1"
            elif local == 13:
                chip_label = "YM2610-B"
            elif 0 <= local <= 3:
                chip_label = f"YM2610-FM{local+1}"
            elif 4 <= local <= 6:
                chip_label = f"YM2610-SSG{local-3}"
            elif 7 <= local <= 12:
                chip_label = f"YM2610-A{local-6}"
            else:
                chip_label = f"YM2610-{local+1}"
        else:
            chip_label = f"{desc[0]}{desc[1]+1}"
        label=f"CH{i:02d} {chip_label}"
        if ch.active:
            if voice:
                lines.append(f"✓ {label:<18} → {voice:<8} | {len(ch.notes)} notes")
            else:
                errors.append(f"{label} ACTIF : {reason}.")
                lines.append(f"✗ {label:<18} → INTERDIT | {len(ch.notes)} notes")
            if ch.unsupported:
                errors.append(f"{label}: commandes FCS non converties : " + "; ".join(ch.unsupported[:5]))
        elif ch.unsupported:
            warnings.append(f"{label} inactif contient une commande inconnue ignorée : {ch.unsupported[0]}")
    active_voice_names=[]
    for i in range(count):
        if fcs.channels[i].active:
            voice,_=dms_voice_for(module.channel_descriptors[i])
            if voice:
                if voice in active_voice_names:
                    errors.append(f"Voix DMS {voice} affectée à plusieurs canaux actifs.")
                active_voice_names.append(voice)

    used_instruments={e.instrument for c in fcs.channels for e in c.notes if e.instrument is not None}
    for idx in sorted(i for i in used_instruments if i is not None):
        if idx >= len(module.instruments):
            errors.append(f"FCS référence l'instrument {idx}, absent du .fur.")
            continue
        inst=module.instruments[idx]
        if inst.kind=="OPZ" and inst.fm_patch is None:
            warnings.append(
                f"Instrument OPZ #{idx} '{inst.name}' : état Furnace par défaut non sérialisé ; "
                "fallback OPZ neutre DMS P0.3 utilisé."
            )
        elif inst.kind=="OPZ" and inst.fm_patch is not None:
            if inst.fm_patch.get("_furnace_fixed_frequency_unmapped"):
                errors.append(
                    f"Instrument OPZ #{idx} '{inst.name}' utilise le mode fréquence fixe Furnace ; "
                    "mapping exact non disponible dans le bridge DMSPROJ P0.4.1."
                )
    if module.tick_rate <= 0 or not math.isfinite(module.tick_rate):
        errors.append(f"Tick rate Furnace invalide : {module.tick_rate}.")
    return Analysis(module,fcs,errors,warnings,lines)


def safe_name(text: str, fallback: str) -> str:
    out="".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in text).strip("_")
    return out or fallback


def midi_varlen(value: int) -> bytes:
    if value < 0:
        raise ValueError(value)
    buffer=value & 0x7F
    out=[]
    while (value := value >> 7):
        buffer <<= 8
        buffer |= ((value & 0x7F) | 0x80)
    while True:
        out.append(buffer & 0xFF)
        if buffer & 0x80:
            buffer >>= 8
        else:
            break
    return bytes(out)


def meta_event(kind: int, payload: bytes) -> bytes:
    return bytes([0xFF,kind])+midi_varlen(len(payload))+payload


def make_track(name: str, messages: list[tuple[int,int,bytes]]) -> bytes:
    # Priority 0 = note off / housekeeping; 1 = note on.
    messages=sorted(messages,key=lambda x:(x[0],x[1]))
    body=bytearray()
    body += b"\x00"+meta_event(0x03,name.encode("utf-8"))
    last=0
    for at,_priority,msg in messages:
        if at<last: raise ConversionError("Ordre MIDI interne invalide.")
        body += midi_varlen(at-last)+msg
        last=at
    body += b"\x00\xFF\x2F\x00"
    return b"MTrk"+struct.pack(">I",len(body))+bytes(body)


def fcs_tick_to_midi(tick: int, tick_rate: float) -> int:
    return int(round(tick * (MIDI_PPQN * 1_000_000 / MIDI_TEMPO_US) / tick_rate))


def fcs_note_to_midi(raw_note: int) -> int:
    # Furnace FCS pitch 60 == C0; Standard MIDI C0 == 12.
    return max(0,min(127,raw_note-48))


def note_segments(channel: FcsChannel, tick_rate: float, one_shot: bool=False) -> list[tuple[int,int,int,int|None]]:
    """Return (start_midi, end_midi, note, instrument)."""
    out=[]; active: tuple[int,int,int|None] | None=None
    for ev in sorted(channel.events,key=lambda e:(e.tick,e.order)):
        if ev.kind=="note_on" and ev.raw_note is not None:
            start=fcs_tick_to_midi(ev.tick,tick_rate); note=fcs_note_to_midi(ev.raw_note)
            if active is not None and not one_shot:
                a,n,ins=active
                out.append((a,max(a+1,start),n,ins))
            if one_shot:
                out.append((start,start+1,note,ev.instrument))
                active=None
            else:
                active=(start,note,ev.instrument)
        elif ev.kind=="note_off" and active is not None:
            end=fcs_tick_to_midi(ev.tick,tick_rate)
            a,n,ins=active;out.append((a,max(a+1,end),n,ins));active=None
    if active is not None:
        a,n,ins=active
        end=fcs_tick_to_midi(channel.end_tick,tick_rate)
        out.append((a,max(a+1,end),n,ins))
    return out


def volume_at_note(ev: FcsEvent) -> int:
    return max(1,min(127,round(ev.volume*127/255)))


def write_midi(path: Path, analysis: Analysis, track_specs: list[dict[str,Any]]) -> None:
    tracks=[]
    conductor=bytearray()
    conductor += b"\x00"+meta_event(0x03,b"DMS Furnace Bridge")
    tempo=MIDI_TEMPO_US.to_bytes(3,"big")
    conductor += b"\x00"+meta_event(0x51,tempo)
    conductor += b"\x00"+meta_event(0x58,bytes([4,2,24,8]))
    conductor += b"\x00\xFF\x2F\x00"
    tracks.append(b"MTrk"+struct.pack(">I",len(conductor))+bytes(conductor))

    for spec in track_specs:
        ch=analysis.fcs.channels[spec["channel"]]
        one_shot=spec["voice"]=="ADPCM-A"
        segs=note_segments(ch,analysis.module.tick_rate,one_shot=one_shot)
        msgs=[]
        # Resolve velocity directly from note event nearest same tick.
        note_events=ch.notes
        for start,end,note,_ins in segs:
            vel=110
            # FCS volume state is mapped to MIDI velocity when available.
            raw_tick_est=round(start*analysis.module.tick_rate/(MIDI_PPQN*1_000_000/MIDI_TEMPO_US))
            candidates=[e for e in note_events if e.tick==raw_tick_est]
            if candidates: vel=volume_at_note(candidates[0])
            msgs.append((start,1,bytes([0x90,note,vel])))
            msgs.append((end,0,bytes([0x80,note,0])))
        tracks.append(make_track(spec["name"],msgs))
    header=b"MThd"+struct.pack(">IHHH",6,1,len(tracks),MIDI_PPQN)
    path.write_bytes(header+b"".join(tracks))


def write_sample_wav(sample: FurSample, path: Path) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(path),"wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2 if sample.depth==16 else 1)
        wf.setframerate(sample.rate or sample.c4_rate or 44100)
        wf.writeframes(sample.pcm)


def sample_for_a_note(module: FurModule, instrument_index: int | None, midi_note: int) -> int:
    if instrument_index is None or instrument_index >= len(module.instruments):
        raise ConversionError(f"ADPCM-A note {midi_note}: instrument absent.")
    inst=module.instruments[instrument_index]
    if inst.kind!="ADPCM-A":
        raise ConversionError(f"ADPCM-A utilise instrument #{instrument_index} de type {inst.kind}.")
    if inst.sample_map is not None:
        # Furnace sample-map index 0 = C0 -> MIDI 12.
        idx=midi_note-12
        if not 0 <= idx < len(inst.sample_map):
            raise ConversionError(f"ADPCM-A MIDI {midi_note}: hors sample map Furnace.")
        smp=inst.sample_map[idx]
        if smp is None:
            raise ConversionError(f"ADPCM-A MIDI {midi_note}: aucune sample Furnace mappée.")
        return smp
    if inst.initial_sample is None:
        raise ConversionError(f"ADPCM-A instrument #{instrument_index}: aucune sample initiale.")
    return inst.initial_sample


def active_instruments(channel: FcsChannel) -> set[int]:
    return {e.instrument for e in channel.notes if e.instrument is not None}


def build_bridge(fur_path: Path, fcs_path: Path, output_dmr: Path, compiler: Path | None=None, compile_dmr: bool=True) -> tuple[Path, Analysis, str]:
    ana=analyze(fur_path,fcs_path)
    if not ana.compatible:
        raise ConversionError("Conversion bloquée par le diagnostic.\n\n"+render_report(ana,fur_path,fcs_path))
    work=output_dmr.parent/(output_dmr.stem+"_FURNACE_BRIDGE")
    if work.exists(): shutil.rmtree(work)
    (work/"samples").mkdir(parents=True,exist_ok=True)
    for sample in ana.module.samples:
        write_sample_wav(sample,work/"samples"/f"sample_{sample.index:03d}_{safe_name(sample.name,'sample')}.wav")

    track_specs=[]; project_tracks={}; fm_patches={}; fallback_used=False
    for ch_index,(desc,ch) in enumerate(zip(ana.module.channel_descriptors,ana.fcs.channels)):
        if not ch.active: continue
        voice,_=dms_voice_for(desc)
        if not voice: continue
        name=f"{voice} Furnace CH{ch_index:02d}"
        spec={"channel":ch_index,"voice":voice,"name":name}
        track_specs.append(spec)
        if voice.startswith("FM"):
            ins_set=active_instruments(ch)
            if len(ins_set)>1:
                raise ConversionError(f"{voice}: changements d'instrument en cours de piste non supportés en P0.3: {sorted(ins_set)}")
            ins_idx=next(iter(ins_set),None)
            patch_key=f"furnace_opz_{ins_idx if ins_idx is not None else 'default'}"
            patch=None
            if ins_idx is not None and ins_idx < len(ana.module.instruments):
                inst=ana.module.instruments[ins_idx]
                if inst.kind!="OPZ":
                    raise ConversionError(f"{voice}: instrument #{ins_idx} est {inst.kind}, pas OPZ.")
                patch=inst.fm_patch
            if patch is None:
                patch=neutral_opz_patch();fallback_used=True
            # Le compilateur DMS ne reçoit que le contrat DMS-OPZ public.
            patch={k:v for k,v in patch.items() if not k.startswith("_")}
            fm_patches[patch_key]=patch
            project_tracks[name]={"voice":voice,"patch":patch_key}
        elif voice.startswith("SSG"):
            project_tracks[name]={"voice":voice,"tone":True,"noise":False,"volume":15}
        elif voice=="ADPCM-A":
            note_map={}
            for start,end,note,ins_idx in note_segments(ch,ana.module.tick_rate,one_shot=True):
                smp_idx=sample_for_a_note(ana.module,ins_idx,note)
                if smp_idx>=len(ana.module.samples):
                    raise ConversionError(f"ADPCM-A sample #{smp_idx} absente.")
                smp=ana.module.samples[smp_idx]
                rel=f"samples/sample_{smp.index:03d}_{safe_name(smp.name,'sample')}.wav"
                key=str(note)
                entry={"wav":rel,"level":0,"pan":"C"}
                if key in note_map and note_map[key]!=entry:
                    raise ConversionError(f"ADPCM-A MIDI {note}: mapping change en cours de morceau non supporté P0.3.")
                note_map[key]=entry
            project_tracks[name]={"voice":"ADPCM-A","note_map":note_map}
        elif voice=="ADPCM-B":
            ins_set=active_instruments(ch)
            if len(ins_set)>1:
                raise ConversionError(f"ADPCM-B: changements d'instrument non supportés P0.3: {sorted(ins_set)}")
            ins_idx=next(iter(ins_set),None)
            if ins_idx is None or ins_idx>=len(ana.module.instruments):
                raise ConversionError("ADPCM-B: instrument absent.")
            inst=ana.module.instruments[ins_idx]
            if inst.kind!="ADPCM-B" or inst.initial_sample is None:
                raise ConversionError(f"ADPCM-B: instrument #{ins_idx} ne référence pas une sample B exploitable.")
            smp_idx=inst.initial_sample
            if smp_idx>=len(ana.module.samples): raise ConversionError(f"ADPCM-B sample #{smp_idx} absente.")
            smp=ana.module.samples[smp_idx]
            rel=f"samples/sample_{smp.index:03d}_{safe_name(smp.name,'sample')}.wav"
            project_tracks[name]={
                "voice":"ADPCM-B","wav":rel,"encode_rate":26000,
                "root_note":60,"level":220,"pan":"C",
                "loop":bool(smp.loop_start>=0 and smp.loop_end>smp.loop_start),
            }

    midi_path=work/(output_dmr.stem+".mid")
    write_midi(midi_path,ana,track_specs)
    fm_bank_path=work/"furnace_opz_bank.json"
    fm_bank={"format":"DMS-OPZ-BANK-0.1","name":"Furnace bridge P0.3","source":fur_path.name,"patches":fm_patches}
    fm_bank_path.write_text(json.dumps(fm_bank,indent=2,ensure_ascii=False),encoding="utf-8")
    project={
        "format":"DMSPROJ-0.1","title":fur_path.stem,"author":"Furnace → DMS bridge",
        "midi":midi_path.name,"fm_bank":fm_bank_path.name,
        "halt_padding_ms":250,"ssg_noise_period":4,
        "mixer":{"fm":12,"ssg":20,"adpcm_a":8,"adpcm_b":12,"master":4},
        "tracks":project_tracks,
    }
    proj_path=work/(output_dmr.stem+".dmsproj")
    proj_path.write_text(json.dumps(project,indent=2,ensure_ascii=False),encoding="utf-8")
    report=render_report(ana,fur_path,fcs_path)
    report += "\n\nBRIDGE\n------\n"
    report += f"MIDI : {midi_path.name}\nDMSPROJ : {proj_path.name}\nOPZ bank : {fm_bank_path.name}\n"
    if fallback_used:
        report += "ATTENTION : fallback OPZ neutre P0.3 réellement utilisé.\n"
    report_path=work/"FURNACE_DMR_REPORT.txt"
    report_path.write_text(report,encoding="utf-8")

    if not compile_dmr:
        return work,ana,report+"\nDMR : étape de compilation non demandée.\n"
    comp=compiler or find_dms_compiler()
    if comp is None:
        raise ConversionError(
            "Bridge généré avec succès, mais dms_compile.py est introuvable dans cette installation DMS.\n"
            f"Bridge conservé ici : {work}\n\n"
            "Le convertisseur cherche le compilateur DMSPROJ existant ; il n'invente pas un second format DMR."
        )
    output_dmr.parent.mkdir(parents=True,exist_ok=True)
    proc=subprocess.run([sys.executable,str(comp),str(proj_path),"--out",str(output_dmr)],cwd=str(work),text=True,capture_output=True)
    log=(proc.stdout or "")+("\n"+proc.stderr if proc.stderr else "")
    (work/"DMS_COMPILER_LOG.txt").write_text(log,encoding="utf-8",errors="replace")
    if proc.returncode!=0 or not output_dmr.exists():
        raise ConversionError(
            f"Le bridge Furnace est valide, mais le compilateur DMS a échoué (code {proc.returncode}).\n"
            f"Log : {work/'DMS_COMPILER_LOG.txt'}\n\n{log[-3000:]}"
        )
    report += f"\nDMR : OK -> {output_dmr}\nCompilateur : {comp}\n"
    report_path.write_text(report,encoding="utf-8")
    return work,ana,report


def find_dms_compiler() -> Path | None:
    here=Path(__file__).resolve()
    roots=[]
    if len(here.parents)>=4: roots.append(here.parents[3])
    roots += [Path.cwd()]
    seen=set()
    for root in roots:
        try: root=root.resolve()
        except OSError: pass
        if root in seen: continue
        seen.add(root)
        for rel in (Path("GDK/tools/dms_compile.py"),Path("tools/dms_compile.py"),Path("RUNTIME/tools/dms_compile.py")):
            p=root/rel
            if p.is_file(): return p
        # bounded discovery of historical audio bridge retained in the canonical tree
        try:
            for p in root.rglob("dms_compile.py"):
                if any(part.lower() in {"backup","backups","build","__pycache__"} for part in p.parts):
                    continue
                return p
        except OSError:
            pass
    return None


def render_report(ana: Analysis, fur_path: Path, fcs_path: Path) -> str:
    m=ana.module; f=ana.fcs
    chip_names=[]
    for c in m.chip_ids:
        chip_names.append(CHIP_INFO.get(c,(f"0x{c:02X}",0))[0])
    active=sum(1 for c in f.channels if c.active)
    duration=f.end_tick/m.tick_rate if m.tick_rate>0 else 0
    lines=[
        f"{APP_NAME} - {APP_VERSION}",
        "="*60,
        f"FUR : {fur_path}",f"FCS : {fcs_path}",
        f"Furnace version : {m.version}",
        f"Chips : {', '.join(chip_names) or 'aucun'}",
        f"Canaux : {len(m.channel_descriptors)} | FCS : {f.channel_count} ({f.flavor})",
        f"Timing : {m.tick_rate:g} Hz | {f.end_tick} ticks | {duration:.3f} s",
        f"Instruments : {len(m.instruments)} | Samples : {len(m.samples)} | Canaux actifs : {active}",
        "",
        "ROUTAGE DMS-1",
        "-------------",
    ]
    lines += ana.channel_lines or ["(aucun canal musical actif)"]
    lines += ["","INSTRUMENTS","-----------"]
    for inst in m.instruments:
        extra=""
        if inst.initial_sample is not None: extra+=f" sample={inst.initial_sample}"
        if inst.sample_map is not None: extra+=" sample-map"
        if inst.fm_patch is not None: extra+=" FM sérialisé"
        lines.append(f"#{inst.index:02d} {inst.kind:<8} {inst.name}{extra}")
    lines += ["","SAMPLES","-------"]
    for smp in m.samples:
        lines.append(f"#{smp.index:02d} {smp.name} | {smp.length} frames | {smp.rate} Hz | PCM{smp.depth}")
    if ana.warnings:
        lines += ["","AVERTISSEMENTS","--------------"]+["! "+x for x in ana.warnings]
    if ana.errors:
        lines += ["","ERREURS BLOQUANTES","------------------"]+["✗ "+x for x in ana.errors]
    lines += ["", "STATUT : "+("COMPATIBLE DMS-1 P0.3" if ana.compatible else "BLOQUÉ")]
    return "\n".join(lines)


def self_test() -> None:
    assert midi_varlen(0)==b"\x00"
    assert midi_varlen(127)==b"\x7f"
    assert midi_varlen(128)==b"\x81\x00"
    assert fcs_note_to_midi(60)==12
    assert fcs_note_to_midi(96)==48

    # Regression P0.3: real Furnace v232 OPZ feature from the validation song.
    blob=bytes.fromhex(
        "f4 25 92 40 00 "
        "55 12 1c 09 4e 53 00 00 "
        "56 36 1f 0a 49 b1 00 00 "
        "05 0a 0e 0a 50 84 00 00 "
        "21 02 c9 08 40 f9 80 08"
    )
    p=parse_fm_feature(blob,232)
    assert p is not None
    assert p["algorithm"]==2 and p["feedback"]==5
    # Native OPZ register order is Furnace internal 1/3/2/4.
    assert p["multiples"]==[5,6,5,1]
    assert p["levels"]==[18,54,10,2]
    assert p["sustains"]==[14,9,16,0]
    assert p["fine"]==[0,0,0,8]
    assert p["detune2"]==[0,0,0,1]
    assert p["key_scale_rates"]==[0,0,0,3]
    print(f"PASS {BUILD_ID}: tests internes + regression OPZ Furnace v232.")


def run_cli(args: argparse.Namespace) -> int:
    if args.self_test:
        self_test();return 0
    if not args.fur or not args.fcs:
        raise ConversionError("--fur et --fcs sont requis en mode CLI.")
    fur=Path(args.fur).resolve(); fcs=Path(args.fcs).resolve()
    if args.analyze_only:
        ana=analyze(fur,fcs);print(render_report(ana,fur,fcs));return 0 if ana.compatible else 2
    out=Path(args.out).resolve() if args.out else fur.with_suffix(".dmr")
    compiler=Path(args.compiler).resolve() if args.compiler else None
    _work,_ana,report=build_bridge(fur,fcs,out,compiler,compile_dmr=True)
    print(report);return 0


def launch_gui() -> None:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except Exception as exc:
        raise ConversionError(f"Tkinter indisponible : {exc}")

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(f"{APP_NAME} - {APP_VERSION}")
            self.geometry("1050x720"); self.minsize(850,600)
            self.fur=tk.StringVar();self.fcs=tk.StringVar();self.out=tk.StringVar();self.status=tk.StringVar(value="Choisis un .fur et son FCS .bin.")
            self.q: queue.Queue=queue.Queue(); self.busy=False
            self._style();self._ui();self.after(100,self._poll)
        def _style(self):
            self.configure(bg="#16191d")
            s=ttk.Style(self)
            try:s.theme_use("clam")
            except Exception:pass
            s.configure("TFrame",background="#16191d");s.configure("TLabel",background="#16191d",foreground="#e8edf2")
            s.configure("Title.TLabel",font=("Segoe UI Semibold",18),foreground="#f2f5f7")
            s.configure("Sub.TLabel",font=("Segoe UI",10),foreground="#aab5bf")
            s.configure("TButton",font=("Segoe UI Semibold",10),padding=(10,7))
        def _ui(self):
            top=ttk.Frame(self);top.pack(fill="x",padx=18,pady=(16,8))
            ttk.Label(top,text=APP_NAME,style="Title.TLabel").pack(anchor="w")
            ttk.Label(top,text="Furnace .fur + FCS .bin → validation DMS → DMSPROJ → .dmr",style="Sub.TLabel").pack(anchor="w")
            form=ttk.Frame(self);form.pack(fill="x",padx=18,pady=8)
            self._row(form,"Module Furnace (.fur)",self.fur,self.pick_fur,0)
            self._row(form,"Command Stream (.bin)",self.fcs,self.pick_fcs,1)
            self._row(form,"Sortie DMR",self.out,self.pick_out,2)
            actions=ttk.Frame(self);actions.pack(fill="x",padx=18,pady=8)
            ttk.Button(actions,text="Analyser la compatibilité",command=self.do_analyze).pack(side="left",padx=(0,8))
            ttk.Button(actions,text="Convertir en DMR",command=self.do_convert).pack(side="left",padx=(0,8))
            ttk.Button(actions,text="Ouvrir dossier sortie",command=self.open_output).pack(side="left")
            ttk.Label(self,textvariable=self.status,style="Sub.TLabel").pack(fill="x",padx=18,pady=(0,6))
            self.text=tk.Text(self,bg="#0f1215",fg="#d9e1e8",insertbackground="#fff",font=("Consolas",10),wrap="word",relief="flat")
            self.text.pack(fill="both",expand=True,padx=18,pady=(0,18))
            self.text.insert("1.0","P0.3 FM FIX BUILD\n\nLe bouton Analyse ne modifie rien.\nLe bouton Convertir crée un bridge puis appelle le compilateur DMR DMS existant.\n")
        def _row(self,parent,label,var,cmd,row):
            ttk.Label(parent,text=label).grid(row=row,column=0,sticky="w",pady=5)
            ttk.Entry(parent,textvariable=var).grid(row=row,column=1,sticky="ew",padx=8,pady=5)
            ttk.Button(parent,text="Choisir…",command=cmd).grid(row=row,column=2,pady=5)
            parent.columnconfigure(1,weight=1)
        def pick_fur(self):
            p=filedialog.askopenfilename(filetypes=[("Furnace module","*.fur"),("Tous","*.*")])
            if p:
                self.fur.set(p)
                if not self.out.get(): self.out.set(str(Path(p).with_suffix(".dmr")))
        def pick_fcs(self):
            p=filedialog.askopenfilename(filetypes=[("Furnace Command Stream","*.bin"),("Tous","*.*")])
            if p:self.fcs.set(p)
        def pick_out(self):
            p=filedialog.asksaveasfilename(defaultextension=".dmr",filetypes=[("DMS Music ROM","*.dmr")])
            if p:self.out.set(p)
        def inputs(self):
            fur=Path(self.fur.get());fcs=Path(self.fcs.get())
            if not fur.is_file() or not fcs.is_file(): raise ConversionError("Choisis un .fur et un .bin existants.")
            return fur,fcs
        def worker(self,fn):
            if self.busy:return
            self.busy=True;self.status.set("Travail en cours…")
            def run():
                try:self.q.put(("ok",fn()))
                except Exception as e:self.q.put(("err",e))
            threading.Thread(target=run,daemon=True).start()
        def do_analyze(self):
            try:fur,fcs=self.inputs()
            except Exception as e:messagebox.showerror(APP_NAME,str(e));return
            self.worker(lambda:render_report(analyze(fur,fcs),fur,fcs))
        def do_convert(self):
            try:fur,fcs=self.inputs()
            except Exception as e:messagebox.showerror(APP_NAME,str(e));return
            out=Path(self.out.get()) if self.out.get() else fur.with_suffix(".dmr");self.out.set(str(out))
            def run():
                _work,_ana,report=build_bridge(fur,fcs,out);return report
            self.worker(run)
        def _poll(self):
            try:kind,payload=self.q.get_nowait()
            except queue.Empty:self.after(100,self._poll);return
            self.busy=False
            if kind=="ok":
                self.status.set("Terminé.");self.text.delete("1.0","end");self.text.insert("1.0",str(payload))
            else:
                self.status.set("Échec - lis le diagnostic.");self.text.delete("1.0","end");self.text.insert("1.0",str(payload));messagebox.showerror(APP_NAME,str(payload))
            self.after(100,self._poll)
        def open_output(self):
            p=Path(self.out.get()).parent if self.out.get() else None
            if not p or not p.exists():return
            try:
                if os.name=="nt":os.startfile(str(p)) # type: ignore[attr-defined]
                elif sys.platform=="darwin":subprocess.Popen(["open",str(p)])
                else:subprocess.Popen(["xdg-open",str(p)])
            except Exception:pass
    App().mainloop()


def main() -> int:
    ap=argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--fur");ap.add_argument("--fcs");ap.add_argument("--out");ap.add_argument("--compiler")
    ap.add_argument("--analyze-only",action="store_true");ap.add_argument("--self-test",action="store_true")
    args=ap.parse_args()
    try:
        if any((args.fur,args.fcs,args.out,args.compiler,args.analyze_only,args.self_test)):
            return run_cli(args)
        launch_gui();return 0
    except ConversionError as exc:
        print(f"ERREUR {APP_NAME}: {exc}",file=sys.stderr);return 2

if __name__=="__main__":
    raise SystemExit(main())
