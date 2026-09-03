#!/usr/bin/env python3
"""Build the DMS-1 P0.2 demo ROM from real WAV and generated resources."""

from __future__ import annotations

import argparse
import math
import struct
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterable

from dms_audio import ADPCM_A_RATE, ADPCM_B_SERVICE_RATE, adpcm_b_encode, prepare_adpcm_a


SYSTEM_CLOCK = 24_000_000
ADPCM_B_SOURCE_RATE = 26_000
ADPCM_B_DELTA = round(Fraction(ADPCM_B_SOURCE_RATE * 65_536, 1) / ADPCM_B_SERVICE_RATE)
SAMPLE_ID_TEXTURE = 1
SAMPLE_ID_KICK = 2
SAMPLE_ID_SNARE = 3
SAMPLE_ID_HAT = 4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KICK = PROJECT_ROOT / "assets/source/Streets of Rage 1 Kick.wav"
DEFAULT_SNARE = PROJECT_ROOT / "assets/source/Streets of Rage 1 Snare.wav"
DEFAULT_HAT = PROJECT_ROOT / "assets/source/Streets of Rage 1 Hi Hat 1.wav"


def uleb128(value: int) -> bytes:
    if value < 0:
        raise ValueError("ULEB128 cannot encode a negative value")
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            result.append(byte | 0x80)
        else:
            result.append(byte)
            return bytes(result)


def wr8(address: int, value: int) -> bytes:
    return b"\x10" + struct.pack(">HB", address, value & 0xFF)


def wrn(address: int, values: Iterable[int]) -> bytes:
    payload = bytes(value & 0xFF for value in values)
    if len(payload) > 255:
        raise ValueError("WRN is limited to 255 bytes")
    return b"\x11" + struct.pack(">HB", address, len(payload)) + payload


def play_a(sample_id: int, level: int, pan: int) -> bytes:
    return b"\x20" + struct.pack(">HBB", sample_id, level, pan)


def stop_a() -> bytes:
    return b"\x21"


def play_b(sample_id: int, delta_n: int, level: int, pan: int, loop: bool = False) -> bytes:
    return b"\x22" + struct.pack(">HHBBB", sample_id, delta_n, level, pan, int(loop))


def stop_b() -> bytes:
    return b"\x23"


def midi_to_opz_keycode(note: int) -> int:
    if not 13 <= note <= 108:
        raise ValueError(f"MIDI note outside OPZ demo range: {note}")
    # OPM/OPZ keycodes are C#-first: C is code 14 of the previous block.
    # Keeping this identical to dms1_midi_protocol.hpp makes compiled ROMs
    # and the live VST agree exactly instead of being one semitone apart.
    codes = (14, 0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13)
    block = note // 12 - 1
    if note % 12 == 0:
        block -= 1
    return (block << 4) | codes[note % 12]


def midi_to_ssg_period(note: int) -> int:
    frequency = 440.0 * 2.0 ** ((note - 69) / 12.0)
    return max(1, min(0xFFF, round(2_000_000 / (16.0 * frequency))))


@dataclass(frozen=True)
class FmPatch:
    algorithm: int
    feedback: int
    multiples: tuple[int, int, int, int]
    fine: tuple[int, int, int, int]
    waves: tuple[int, int, int, int]
    levels: tuple[int, int, int, int]
    attacks: tuple[int, int, int, int]
    decays: tuple[int, int, int, int]
    sustains: tuple[int, int, int, int]
    sustain_levels: tuple[int, int, int, int]
    releases: tuple[int, int, int, int]
    reverb_rates: tuple[int, int, int, int]
    detunes: tuple[int, int, int, int] = (0, 0, 0, 0)
    key_scale_rates: tuple[int, int, int, int] = (0, 0, 0, 0)
    fixed_modes: tuple[int, int, int, int] = (0, 0, 0, 0)
    fixed_ranges: tuple[int, int, int, int] = (0, 0, 0, 0)
    fixed_frequencies: tuple[int, int, int, int] = (0, 0, 0, 0)
    am_enables: tuple[int, int, int, int] = (0, 0, 0, 0)
    detune2: tuple[int, int, int, int] = (0, 0, 0, 0)
    eg_shifts: tuple[int, int, int, int] = (0, 0, 0, 0)
    velocity_sensitivities: tuple[int, int, int, int] = (7, 7, 7, 7)
    transpose: int = 24
    lfo_speed: int = 0
    lfo_pitch_depth: int = 0
    lfo_amplitude_depth: int = 0
    lfo_pitch_sensitivity: int = 0
    lfo_amplitude_sensitivity: int = 0
    lfo_waveform: int = 0
    lfo_sync: int = 0


BASS_PATCH = FmPatch(
    algorithm=7,
    feedback=5,
    multiples=(1, 1, 2, 1),
    fine=(0, 3, 0, 8),
    waves=(0, 1, 4, 6),
    levels=(8, 22, 34, 42),
    attacks=(31, 30, 31, 28),
    decays=(12, 10, 16, 8),
    sustains=(4, 3, 6, 2),
    sustain_levels=(7, 8, 10, 6),
    releases=(7, 6, 8, 5),
    reverb_rates=(2, 2, 3, 1),
)

CHORD_PATCH = FmPatch(
    algorithm=7,
    feedback=2,
    multiples=(1, 2, 3, 1),
    fine=(0, 1, 7, 12),
    waves=(0, 2, 5, 1),
    levels=(20, 32, 39, 45),
    attacks=(25, 23, 28, 21),
    decays=(5, 6, 7, 4),
    sustains=(1, 2, 2, 1),
    sustain_levels=(5, 6, 8, 5),
    releases=(5, 5, 6, 4),
    reverb_rates=(2, 3, 4, 2),
)


def fm_patch_bytes(channel: int, patch: FmPatch) -> bytes:
    control = 0x80 | ((patch.feedback & 7) << 3) | (patch.algorithm & 7)
    data = bytearray()
    # DMS-1 deliberately exposes one shared OPZ LFO. Loading a patch therefore
    # updates its global rate/depth/waveform, while PMS/AMS remains per channel.
    data += wr8(0x0018, patch.lfo_speed & 0xFF)
    data += wr8(0x0019, patch.lfo_amplitude_depth & 0x7F)
    data += wr8(0x0019, 0x80 | (patch.lfo_pitch_depth & 0x7F))
    data += wr8(0x001B, ((patch.lfo_sync & 1) << 4) | (patch.lfo_waveform & 3))
    data += wr8(
        0x0038 + channel,
        ((patch.lfo_pitch_sensitivity & 7) << 4)
        | (patch.lfo_amplitude_sensitivity & 3),
    )
    data += wr8(0x0030 + channel, 1)
    for group in range(4):
        offset = channel + group * 8
        if patch.fixed_modes[group]:
            frequency = ((patch.fixed_ranges[group] & 7) << 4) | (patch.fixed_frequencies[group] & 0x0f)
        else:
            frequency = ((patch.detunes[group] & 7) << 4) | (patch.multiples[group] & 0x0f)
        data += wr8(0x0040 + offset, frequency)
        data += wr8(
            0x0040 + offset,
            0x80 | ((patch.waves[group] & 7) << 4) | (patch.fine[group] & 0x0F),
        )
        data += wr8(0x0060 + offset, patch.levels[group] & 0x7F)
        data += wr8(
            0x0080 + offset,
            ((patch.key_scale_rates[group] & 3) << 6)
            | ((patch.fixed_modes[group] & 1) << 5)
            | (patch.attacks[group] & 0x1f),
        )
        data += wr8(
            0x00A0 + offset,
            ((patch.am_enables[group] & 1) << 7) | (patch.decays[group] & 0x1f),
        )
        data += wr8(
            0x00C0 + offset,
            ((patch.detune2[group] & 3) << 6) | (patch.sustains[group] & 0x1f),
        )
        data += wr8(
            0x00C0 + offset,
            ((patch.eg_shifts[group] & 3) << 6) | 0x20 | (patch.reverb_rates[group] & 7),
        )
        data += wr8(
            0x00E0 + offset,
            ((patch.sustain_levels[group] & 0x0F) << 4) | (patch.releases[group] & 0x0F),
        )
    data += wr8(0x0020 + channel, control)
    return bytes(data)


def fm_note_on(channel: int, note: int, patch: FmPatch) -> bytes:
    control = 0x80 | ((patch.feedback & 7) << 3) | (patch.algorithm & 7)
    transposed_note = note + patch.transpose - 24
    return b"".join(
        (
            wr8(0x0020 + channel, control),
            wr8(0x0028 + channel, midi_to_opz_keycode(transposed_note)),
            wr8(0x0030 + channel, 1),
            wr8(0x0020 + channel, control | 0x40),
        )
    )


def fm_note_off(channel: int, patch: FmPatch) -> bytes:
    control = 0x80 | ((patch.feedback & 7) << 3) | (patch.algorithm & 7)
    return wr8(0x0020 + channel, control)


@dataclass(order=True)
class Event:
    cycle: int
    order: int
    payload: bytes


class Timeline:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self._order = 0

    def add_cycles(self, cycle: int, payload: bytes) -> None:
        if cycle < 0:
            raise ValueError("negative event time")
        self.events.append(Event(cycle, self._order, payload))
        self._order += 1

    def add_seconds(self, seconds: Fraction, payload: bytes) -> None:
        cycles = round(seconds * SYSTEM_CLOCK)
        self.add_cycles(cycles, payload)

    def compile(self, halt_cycle: int) -> bytes:
        output = bytearray()
        cursor = 0
        for event in sorted(self.events):
            if event.cycle > halt_cycle:
                raise ValueError("event occurs after HALT")
            if event.cycle < cursor:
                raise AssertionError("timeline sort failure")
            if event.cycle != cursor:
                output.append(0x01)
                output += uleb128(event.cycle - cursor)
                cursor = event.cycle
            output += event.payload
        if halt_cycle > cursor:
            output.append(0x01)
            output += uleb128(halt_cycle - cursor)
        output.append(0x00)
        return bytes(output)


def build_timeline() -> tuple[bytes, int]:
    timeline = Timeline()
    beat = SYSTEM_CLOCK // 2
    bar = beat * 4
    swing = round(0.012 * SYSTEM_CLOCK)
    hat_gate = round(0.020 * SYSTEM_CLOCK)

    initialization = bytearray()
    initialization += wrn(0x0188, (12, 20, 8, 12, 4))
    initialization += wr8(0x018D, 0xC0)
    initialization += fm_patch_bytes(0, BASS_PATCH)
    for channel in (1, 2, 3):
        initialization += fm_patch_bytes(channel, CHORD_PATCH)
    initialization += wrn(0x0100, (0, 0, 0, 0, 0, 0, 4, 0x1C, 0, 0, 0, 0x80, 0x00, 0x09))
    timeline.add_cycles(0, bytes(initialization))

    chord_progression = (
        (57, 60, 64),  # A minor
        (53, 57, 60),  # F major
        (60, 64, 67),  # C major
        (55, 59, 62),  # G major
    )
    bass_progression = (
        (45, 52, 45, 48),
        (41, 48, 41, 45),
        (48, 55, 48, 52),
        (43, 50, 43, 47),
    )

    for bar_index, chord in enumerate(chord_progression):
        bar_start = bar_index * bar
        for channel, note in zip((1, 2, 3), chord):
            timeline.add_cycles(bar_start, fm_note_on(channel, note, CHORD_PATCH))

        pedal_period = midi_to_ssg_period(chord[0] - 12)
        timeline.add_cycles(
            bar_start,
            wrn(0x0102, (pedal_period & 0xFF, pedal_period >> 8)) + wr8(0x0109, 8),
        )

        for beat_index, note in enumerate(bass_progression[bar_index]):
            start = bar_start + beat_index * beat
            human_offset = (7_200, -4_800, 12_000, -2_400)[beat_index]
            start += human_offset
            timeline.add_cycles(start, fm_note_on(0, note, BASS_PATCH))
            timeline.add_cycles(start + round(0.355 * SYSTEM_CLOCK), fm_note_off(0, BASS_PATCH))

        arp = (chord[0] + 12, chord[1] + 12, chord[2] + 12, chord[1] + 12)
        for step in range(8):
            start = bar_start + step * (beat // 2) + (swing if step & 1 else 0)
            note = arp[step % len(arp)]
            period = midi_to_ssg_period(note)
            timeline.add_cycles(
                start,
                wrn(0x0100, (period & 0xFF, period >> 8)) + wr8(0x0108, 11),
            )
            timeline.add_cycles(start + round(0.165 * SYSTEM_CLOCK), wr8(0x0108, 0))
            timeline.add_cycles(start, wr8(0x010A, 10 if (step % 2 == 0) else 7))
            timeline.add_cycles(start + hat_gate, wr8(0x010A, 0))

            drum_pattern = (
                (SAMPLE_ID_KICK, 0),
                (SAMPLE_ID_HAT, 5),
                (SAMPLE_ID_SNARE, 2),
                (SAMPLE_ID_HAT, 5),
                (SAMPLE_ID_KICK, 0),
                (SAMPLE_ID_HAT, 5),
                (SAMPLE_ID_SNARE, 2),
                (SAMPLE_ID_HAT, 5),
            )
            drum_id, drum_level = drum_pattern[step]
            timeline.add_cycles(start, play_a(drum_id, drum_level, 0xC0))

            # One deliberate final-bar overlap demonstrates the monophonic,
            # destructive ADPCM-A retrigger instead of hiding the constraint.
            if bar_index == 3 and step == 6:
                timeline.add_cycles(
                    start + round(0.080 * SYSTEM_CLOCK),
                    play_a(SAMPLE_ID_HAT, 5, 0xC0),
                )

    timeline.add_cycles(round(0.030 * SYSTEM_CLOCK), play_b(SAMPLE_ID_TEXTURE, ADPCM_B_DELTA, 224, 0xC0))
    timeline.add_cycles(2 * bar + round(0.030 * SYSTEM_CLOCK), play_b(SAMPLE_ID_TEXTURE, ADPCM_B_DELTA, 224, 0xC0))

    end = 4 * bar
    shutdown = bytearray()
    shutdown += fm_note_off(0, BASS_PATCH)
    for channel in (1, 2, 3):
        shutdown += fm_note_off(channel, CHORD_PATCH)
    shutdown += wrn(0x0108, (0, 0, 0))
    shutdown += stop_a()
    shutdown += stop_b()
    timeline.add_cycles(end, bytes(shutdown))
    halt_cycle = end + round(0.250 * SYSTEM_CLOCK)
    return timeline.compile(halt_cycle), halt_cycle


def make_texture_pcm() -> list[int]:
    sample_count = 52 * 256 * 2  # exactly 52 ADPCM pages
    phase = 0.0
    noise_state = 0x13579BDF
    output: list[int] = []
    for index in range(sample_count):
        time = index / ADPCM_B_SOURCE_RATE
        duration = sample_count / ADPCM_B_SOURCE_RATE
        vibrato = 1.0 + 0.012 * math.sin(2.0 * math.pi * 5.1 * time)
        f0 = (112.0 + 18.0 * time / duration) * vibrato
        phase += 2.0 * math.pi * f0 / ADPCM_B_SOURCE_RATE
        voiced = (
            0.58 * math.sin(phase)
            + 0.23 * math.sin(2.0 * phase + 0.4)
            + 0.14 * math.sin(3.0 * phase + 1.1)
            + 0.10 * math.sin(6.0 * phase)
            + 0.07 * math.sin(10.0 * phase + 0.8)
        )
        noise_state = (1664525 * noise_state + 1013904223) & 0xFFFFFFFF
        breath = (((noise_state >> 8) & 0xFFFF) / 32767.5 - 1.0) * 0.035
        attack = min(1.0, time / 0.035)
        release = min(1.0, max(0.0, (duration - time) / 0.120))
        pulse = 0.82 + 0.18 * math.sin(2.0 * math.pi * 2.3 * time)
        value = (voiced * pulse + breath) * attack * release
        output.append(max(-28_000, min(28_000, round(value * 22_000))))
    return output


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) & -boundary


@dataclass(frozen=True)
class SampleResource:
    sample_id: int
    codec: int
    name: str
    data: bytes
    source_rate: int
    level: int
    pan: int
    root_note: int
    fine_cents: int = 0


def pack_rom(
    code: bytes,
    meta: bytes,
    resources: tuple[SampleResource, ...] = (),
) -> bytes:
    """Pack already-compiled DSEQ and ADPCM resources into a DMR 0.1 image."""
    if not code or code[-1] != 0:
        raise ValueError("DSEQ CODE must end with HALT")
    if any(len(resource.data) == 0 or len(resource.data) % 256 for resource in resources):
        raise ValueError("all sample resources must occupy whole ADPCM pages")
    if len({resource.sample_id for resource in resources}) != len(resources):
        raise ValueError("duplicate sample ID")

    directory_offset = 64
    chunk_count = 4 if resources else 2
    code_offset = directory_offset + chunk_count * 16
    meta_offset = align(code_offset + len(code), 4)
    chunks: list[tuple[bytes, int, int, int]] = [
        (b"CODE", code_offset, len(code), 0),
        (b"META", meta_offset, len(meta), 0),
    ]
    sdir = bytearray()
    sample_data = b""
    sdir_offset = samp_offset = 0
    if resources:
        sample_data = b"".join(resource.data for resource in resources)
        sdir_offset = align(meta_offset + len(meta), 4)
        sdir_size = len(resources) * 16
        samp_offset = align(sdir_offset + sdir_size, 256)
        total_size = samp_offset + len(sample_data)
        chunks.extend((
            (b"SDIR", sdir_offset, sdir_size, 0),
            (b"SAMP", samp_offset, len(sample_data), 0),
        ))
    else:
        total_size = meta_offset + len(meta)
    if total_size > 0x01000000:
        raise ValueError("DMR exceeds the 16 MiB V0.1 limit")
    if resources and (total_size - 1) >> 8 > 0xFFFF:
        raise ValueError("sample pages exceed SDIR V0.1")

    sample_cursor = samp_offset
    for resource in resources:
        start_page = sample_cursor >> 8
        end_page = (sample_cursor + len(resource.data) - 1) >> 8
        sdir += struct.pack(
            ">HBBHHIBBBb",
            resource.sample_id,
            resource.codec,
            0,
            start_page,
            end_page,
            resource.source_rate,
            resource.level,
            resource.pan,
            resource.root_note,
            resource.fine_cents,
        )
        sample_cursor += len(resource.data)

    rom = bytearray(total_size)
    struct.pack_into(">4sHHHHI4sIIHHII", rom, 0,
                     b"DMR0", 0, 1, 64, 0, total_size, b"DMS1", 1,
                     directory_offset, chunk_count, 16, code_offset, SYSTEM_CLOCK)
    for index, chunk in enumerate(chunks):
        struct.pack_into(">4sIII", rom, directory_offset + index * 16, *chunk)
    rom[code_offset:code_offset + len(code)] = code
    rom[meta_offset:meta_offset + len(meta)] = meta
    if resources:
        rom[sdir_offset:sdir_offset + len(sdir)] = sdir
        rom[samp_offset:samp_offset + len(sample_data)] = sample_data
    return bytes(rom)


def build_rom(
    kick_path: Path = DEFAULT_KICK,
    snare_path: Path = DEFAULT_SNARE,
    hat_path: Path = DEFAULT_HAT,
) -> tuple[bytes, int, tuple[SampleResource, ...]]:
    code, halt_cycle = build_timeline()
    kick = prepare_adpcm_a(kick_path)
    snare = prepare_adpcm_a(snare_path)
    hat = prepare_adpcm_a(hat_path)
    resources = (
        SampleResource(
            SAMPLE_ID_TEXTURE, 2, "Generated 26 kHz texture",
            adpcm_b_encode(make_texture_pcm()), ADPCM_B_SOURCE_RATE, 224, 0xC0, 57,
        ),
        SampleResource(
            SAMPLE_ID_KICK, 1, kick_path.name,
            kick.encoded, round(ADPCM_A_RATE), 0, 0xC0, 36,
        ),
        SampleResource(
            SAMPLE_ID_SNARE, 1, snare_path.name,
            snare.encoded, round(ADPCM_A_RATE), 2, 0xC0, 38,
        ),
        SampleResource(
            SAMPLE_ID_HAT, 1, hat_path.name,
            hat.encoded, round(ADPCM_A_RATE), 5, 0xC0, 42,
        ),
    )
    meta = (
        "title=DMS-1 Nine Voices\n"
        "author=DAC MASTER\n"
        "driver=DSEQ/DHR timing philosophy\n"
        "adpcm_a_rate=8000000/432\n"
        "adpcm_a_sources=Streets of Rage 1 Kick,Snare,Hi Hat 1\n"
        "adpcm_b_source_rate=26000\n"
    ).encode("utf-8")

    rom = pack_rom(code, meta, resources)
    return bytes(rom), halt_cycle, resources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("roms/dms1_nine_voices.dmr"))
    parser.add_argument("--kick", type=Path, default=DEFAULT_KICK)
    parser.add_argument("--snare", type=Path, default=DEFAULT_SNARE)
    parser.add_argument("--hat", type=Path, default=DEFAULT_HAT)
    args = parser.parse_args()
    rom, halt_cycle, resources = build_rom(args.kick, args.snare, args.hat)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(rom)
    actual_rate = float(ADPCM_B_SERVICE_RATE * ADPCM_B_DELTA / 65_536)
    print(f"DMR: {args.out} | {len(rom)} bytes | DSEQ HALT {halt_cycle / SYSTEM_CLOCK:.3f} s")
    for resource in resources:
        codec = "A" if resource.codec == 1 else "B"
        print(
            f"ADPCM-{codec} id={resource.sample_id}: {resource.name} | "
            f"{resource.source_rate} Hz | {len(resource.data)} bytes | "
            f"{len(resource.data) // 256} pages"
        )
    print(
        f"ADPCM-B playback: Delta-N ${ADPCM_B_DELTA:04X} | hardware {actual_rate:.6f} Hz"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
