#!/usr/bin/env python3
"""Compile a DMSPROJ-0.1 project and Standard MIDI File into a native DMR ROM."""

from __future__ import annotations

import argparse
import bisect
import json
import math
import struct
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from dms_audio import ADPCM_A_RATE, ADPCM_B_SERVICE_RATE, prepare_adpcm_a, prepare_adpcm_b
from make_demo import (
    SYSTEM_CLOCK,
    FmPatch,
    SampleResource,
    Timeline,
    fm_note_off,
    fm_note_on,
    fm_patch_bytes,
    midi_to_ssg_period,
    pack_rom,
    play_a,
    play_b,
    stop_a,
    stop_b,
    wr8,
    wrn,
)


PROJECT_FORMAT = "DMSPROJ-0.1"
BANK_FORMAT = "DMS-OPZ-BANK-0.1"
ALLOWED_VOICES = {
    "FM1", "FM2", "FM3", "FM4",
    "SSG-A", "SSG-B", "SSG-C",
    "ADPCM-A", "ADPCM-B",
}


class CompileError(ValueError):
    pass


@dataclass(frozen=True)
class MidiNoteEvent:
    tick: int
    order: int
    track: str
    channel: int
    note: int
    velocity: int
    note_on: bool


@dataclass(frozen=True)
class TempoEvent:
    tick: int
    order: int
    microseconds_per_quarter: int


@dataclass(frozen=True)
class MidiSong:
    format_type: int
    ppqn: int
    track_names: tuple[str, ...]
    notes: tuple[MidiNoteEvent, ...]
    tempos: tuple[TempoEvent, ...]
    end_tick: int


@dataclass(frozen=True)
class CompileReport:
    rom_bytes: int
    midi_notes: int
    tempo_events: int
    halt_cycle: int
    resources_a: int
    resources_b: int
    used_voices: tuple[str, ...]
    warnings: tuple[str, ...]


def _require(data: bytes, cursor: int, size: int, label: str) -> None:
    if cursor < 0 or size < 0 or cursor + size > len(data):
        raise CompileError(f"{label}: donnees MIDI tronquees")


def _read_vlq(data: bytes, cursor: int, end: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if cursor >= end:
            raise CompileError("VLQ MIDI tronque")
        byte = data[cursor]
        cursor += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, cursor
    raise CompileError("VLQ MIDI superieur a quatre octets")


def _decode_midi_text(raw: bytes) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding).strip("\x00 ")
        except UnicodeDecodeError:
            pass
    return ""


def parse_midi(path: Path) -> MidiSong:
    data = path.read_bytes()
    if len(data) < 14 or data[:4] != b"MThd":
        raise CompileError(f"{path}: header MThd absent")
    header_size = struct.unpack_from(">I", data, 4)[0]
    if header_size < 6:
        raise CompileError("header MIDI trop court")
    _require(data, 8, header_size, "header MIDI")
    format_type, track_count, division = struct.unpack_from(">HHH", data, 8)
    if format_type not in (0, 1):
        raise CompileError(f"MIDI format {format_type} non pris en charge")
    if track_count == 0 or (format_type == 0 and track_count != 1):
        raise CompileError("nombre de pistes MIDI incoherent")
    if division & 0x8000:
        raise CompileError("timecode SMPTE non pris en charge; exporte en PPQN")
    if division == 0:
        raise CompileError("PPQN MIDI nul")

    cursor = 8 + header_size
    raw_notes: list[tuple[int, int, int, int, int, int, bool]] = []
    tempos: list[TempoEvent] = []
    track_names: list[str] = []
    global_order = 0
    end_tick = 0

    for track_index in range(track_count):
        _require(data, cursor, 8, f"piste MIDI {track_index + 1}")
        if data[cursor:cursor + 4] != b"MTrk":
            raise CompileError(f"piste {track_index + 1}: chunk MTrk absent")
        track_size = struct.unpack_from(">I", data, cursor + 4)[0]
        cursor += 8
        track_end = cursor + track_size
        _require(data, cursor, track_size, f"piste MIDI {track_index + 1}")
        tick = 0
        running_status: int | None = None
        track_name = f"Track {track_index + 1}"

        while cursor < track_end:
            delta, cursor = _read_vlq(data, cursor, track_end)
            tick += delta
            end_tick = max(end_tick, tick)
            if cursor >= track_end:
                raise CompileError("evenement MIDI sans statut")
            status = data[cursor]
            if status & 0x80:
                cursor += 1
                if status < 0xF0:
                    running_status = status
                else:
                    running_status = None
            elif running_status is not None:
                status = running_status
            else:
                raise CompileError("running status MIDI sans statut precedent")

            if status == 0xFF:
                if cursor >= track_end:
                    raise CompileError("meta-evenement MIDI tronque")
                meta_type = data[cursor]
                cursor += 1
                length, cursor = _read_vlq(data, cursor, track_end)
                if cursor + length > track_end:
                    raise CompileError("donnees de meta-evenement tronquees")
                payload = data[cursor:cursor + length]
                cursor += length
                if meta_type == 0x03:
                    decoded = _decode_midi_text(payload)
                    if decoded:
                        track_name = decoded
                elif meta_type == 0x51:
                    if length != 3:
                        raise CompileError("meta tempo MIDI de taille invalide")
                    tempo = int.from_bytes(payload, "big")
                    if tempo == 0:
                        raise CompileError("tempo MIDI nul")
                    tempos.append(TempoEvent(tick, global_order, tempo))
                global_order += 1
                if meta_type == 0x2F:
                    cursor = track_end
                continue

            if status in (0xF0, 0xF7):
                length, cursor = _read_vlq(data, cursor, track_end)
                if cursor + length > track_end:
                    raise CompileError("SysEx MIDI tronque")
                cursor += length
                global_order += 1
                continue

            command = status >> 4
            if not 0x8 <= command <= 0xE:
                raise CompileError(f"statut MIDI ${status:02X} non pris en charge")
            data_length = 1 if command in (0xC, 0xD) else 2
            if cursor + data_length > track_end:
                raise CompileError("message MIDI de canal tronque")
            first = data[cursor]
            second = data[cursor + 1] if data_length == 2 else 0
            if first & 0x80 or second & 0x80:
                raise CompileError("octet de donnees MIDI superieur a 127")
            cursor += data_length
            if command in (0x8, 0x9):
                note_on = command == 0x9 and second != 0
                raw_notes.append(
                    (tick, global_order, track_index, status & 0x0F,
                     first, second if note_on else 0, note_on)
                )
            global_order += 1

        track_names.append(track_name)

    if cursor != len(data):
        raise CompileError("octets surnumeraires apres les pistes MIDI")
    if len(set(track_names)) != len(track_names):
        raise CompileError("les noms de pistes MIDI doivent etre uniques")
    notes = tuple(
        MidiNoteEvent(tick, order, track_names[track_index], channel, note, velocity, on)
        for tick, order, track_index, channel, note, velocity, on in raw_notes
    )
    return MidiSong(
        format_type,
        division,
        tuple(track_names),
        tuple(sorted(notes, key=lambda event: (event.tick, event.order))),
        tuple(sorted(tempos, key=lambda event: (event.tick, event.order))),
        end_tick,
    )


class TickClock:
    """Exact MIDI PPQN/tempo integration into the 24 MHz DSEQ domain."""

    def __init__(self, ppqn: int, tempos: tuple[TempoEvent, ...]) -> None:
        self.ppqn = ppqn
        self.ticks: list[int] = [0]
        self.cycles: list[Fraction] = [Fraction(0)]
        self.rates: list[int] = [500_000]
        current_tick = 0
        current_cycles = Fraction(0)
        current_tempo = 500_000
        index = 0
        while index < len(tempos):
            tick = tempos[index].tick
            current_cycles += Fraction((tick - current_tick) * current_tempo * 24, ppqn)
            current_tick = tick
            while index < len(tempos) and tempos[index].tick == tick:
                current_tempo = tempos[index].microseconds_per_quarter
                index += 1
            if tick == self.ticks[-1]:
                self.cycles[-1] = current_cycles
                self.rates[-1] = current_tempo
            else:
                self.ticks.append(tick)
                self.cycles.append(current_cycles)
                self.rates.append(current_tempo)

    def to_cycles(self, tick: int) -> int:
        if tick < 0:
            raise CompileError("tick MIDI negatif")
        index = bisect.bisect_right(self.ticks, tick) - 1
        exact = self.cycles[index] + Fraction(
            (tick - self.ticks[index]) * self.rates[index] * 24,
            self.ppqn,
        )
        return round(exact)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CompileError(f"{path}: {error}") from error
    if not isinstance(value, dict):
        raise CompileError(f"{path}: objet JSON attendu")
    return value


def _integer(value: Any, minimum: int, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise CompileError(f"{label}: entier {minimum}..{maximum} attendu")
    return value


def _array4(source: dict[str, Any], key: str, maximum: int, patch_name: str) -> tuple[int, ...]:
    values = source.get(key)
    if not isinstance(values, list) or len(values) != 4:
        raise CompileError(f"patch {patch_name}: {key} doit contenir quatre valeurs")
    return tuple(_integer(value, 0, maximum, f"patch {patch_name}.{key}") for value in values)


def _optional_integer(
    source: dict[str, Any], key: str, default: int, maximum: int, patch_name: str
) -> int:
    return _integer(source.get(key, default), 0, maximum, f"patch {patch_name}.{key}")


def load_fm_bank(path: Path) -> dict[str, FmPatch]:
    bank = _read_json(path)
    if bank.get("format") != BANK_FORMAT:
        raise CompileError(f"{path}: format attendu {BANK_FORMAT}")
    raw_patches = bank.get("patches")
    if not isinstance(raw_patches, dict) or not raw_patches:
        raise CompileError(f"{path}: banque FM vide")
    patches: dict[str, FmPatch] = {}
    for name, raw in raw_patches.items():
        if not isinstance(name, str) or not isinstance(raw, dict):
            raise CompileError(f"{path}: patch FM invalide")
        patches[name] = FmPatch(
            algorithm=_integer(raw.get("algorithm"), 0, 7, f"patch {name}.algorithm"),
            feedback=_integer(raw.get("feedback"), 0, 7, f"patch {name}.feedback"),
            multiples=_array4(raw, "multiples", 15, name),
            fine=_array4(raw, "fine", 15, name),
            waves=_array4(raw, "waves", 7, name),
            levels=_array4(raw, "levels", 127, name),
            attacks=_array4(raw, "attacks", 31, name),
            decays=_array4(raw, "decays", 31, name),
            sustains=_array4(raw, "sustains", 31, name),
            sustain_levels=_array4(raw, "sustain_levels", 15, name),
            releases=_array4(raw, "releases", 15, name),
            reverb_rates=_array4(raw, "reverb_rates", 7, name),
            detunes=_array4(raw, "detunes", 7, name),
            key_scale_rates=_array4(raw, "key_scale_rates", 3, name),
            fixed_modes=_array4(raw, "fixed_modes", 1, name),
            fixed_ranges=_array4(raw, "fixed_ranges", 7, name),
            fixed_frequencies=_array4(raw, "fixed_frequencies", 15, name),
            am_enables=_array4(raw, "am_enables", 1, name),
            detune2=_array4(raw, "detune2", 3, name),
            eg_shifts=_array4(raw, "eg_shifts", 3, name),
            velocity_sensitivities=_array4(raw, "velocity_sensitivities", 7, name),
            transpose=_optional_integer(raw, "transpose", 24, 48, name),
            lfo_speed=_optional_integer(raw, "lfo_speed", 0, 255, name),
            lfo_pitch_depth=_optional_integer(raw, "lfo_pitch_depth", 0, 127, name),
            lfo_amplitude_depth=_optional_integer(raw, "lfo_amplitude_depth", 0, 127, name),
            lfo_pitch_sensitivity=_optional_integer(
                raw, "lfo_pitch_sensitivity", 0, 7, name
            ),
            lfo_amplitude_sensitivity=_optional_integer(
                raw, "lfo_amplitude_sensitivity", 0, 3, name
            ),
            lfo_waveform=_optional_integer(raw, "lfo_waveform", 0, 3, name),
            lfo_sync=_optional_integer(raw, "lfo_sync", 0, 1, name),
        )
    return patches


def _resolve(base: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise CompileError(f"{label}: chemin de fichier attendu")
    path = (base / value).resolve()
    if not path.is_file():
        raise CompileError(f"{label}: fichier absent: {path}")
    return path


def _pan(value: Any, label: str) -> int:
    if isinstance(value, str):
        pans = {"L": 0x80, "R": 0x40, "C": 0xC0}
        if value.upper() in pans:
            return pans[value.upper()]
    if isinstance(value, int) and value in (0x40, 0x80, 0xC0):
        return value
    raise CompileError(f"{label}: pan attendu L, R ou C")


def _fm_velocity(channel: int, patch: FmPatch, velocity: int) -> bytes:
    output = bytearray()
    for operator in range(4):
        attenuation = round(
            (127 - velocity) * 24 * patch.velocity_sensitivities[operator] / (127 * 7)
        )
        offset = channel + operator * 8
        output += wr8(0x0060 + offset, min(127, patch.levels[operator] + attenuation))
    return bytes(output)


def _meta_value(value: Any) -> str:
    return str(value).replace("\r", " ").replace("\n", " ")


def compile_project(project_path: Path) -> tuple[bytes, CompileReport]:
    project_path = project_path.resolve()
    project_dir = project_path.parent
    project = _read_json(project_path)
    if project.get("format") != PROJECT_FORMAT:
        raise CompileError(f"format attendu {PROJECT_FORMAT}")
    midi_path = _resolve(project_dir, project.get("midi"), "midi")
    bank_path = _resolve(project_dir, project.get("fm_bank"), "fm_bank")
    song = parse_midi(midi_path)
    patches = load_fm_bank(bank_path)
    raw_bindings = project.get("tracks")
    if not isinstance(raw_bindings, dict) or not raw_bindings:
        raise CompileError("tracks: objet non vide attendu")

    bindings: dict[str, dict[str, Any]] = {}
    used_voices: set[str] = set()
    for track_name, binding in raw_bindings.items():
        if track_name not in song.track_names:
            raise CompileError(f"piste MIDI introuvable: {track_name}")
        if not isinstance(binding, dict):
            raise CompileError(f"binding invalide pour {track_name}")
        voice = binding.get("voice")
        if voice not in ALLOWED_VOICES:
            raise CompileError(f"{track_name}: voix DMS-1 invalide: {voice}")
        if voice in used_voices:
            raise CompileError(f"voix materielle affectee deux fois: {voice}")
        used_voices.add(voice)
        bindings[track_name] = binding

    note_tracks = {event.track for event in song.notes}
    unmapped = sorted(note_tracks - bindings.keys())
    if unmapped:
        raise CompileError("pistes MIDI avec notes non affectees: " + ", ".join(unmapped))

    fm_bindings: dict[str, tuple[int, FmPatch]] = {}
    ssg_bindings: dict[str, tuple[int, bool, bool, int]] = {}
    for track_name, binding in bindings.items():
        voice = str(binding["voice"])
        if voice.startswith("FM"):
            patch_name = binding.get("patch")
            if patch_name not in patches:
                raise CompileError(f"{track_name}: patch FM absent: {patch_name}")
            fm_bindings[track_name] = (int(voice[-1]) - 1, patches[str(patch_name)])
        elif voice.startswith("SSG-"):
            channel = ord(voice[-1]) - ord("A")
            tone = bool(binding.get("tone", True))
            noise = bool(binding.get("noise", False))
            if not tone and not noise:
                raise CompileError(f"{track_name}: SSG sans tone ni noise")
            volume = _integer(binding.get("volume", 10), 1, 15, f"{track_name}.volume")
            ssg_bindings[track_name] = (channel, tone, noise, volume)

    resources: list[SampleResource] = []
    a_note_ids: dict[int, tuple[int, int, int]] = {}
    b_settings: tuple[int, int, int, int, int, bool] | None = None
    next_sample_id = 1

    b_tracks = [(name, binding) for name, binding in bindings.items()
                if binding["voice"] == "ADPCM-B"]
    if b_tracks:
        track_name, binding = b_tracks[0]
        wav = _resolve(project_dir, binding.get("wav"), f"{track_name}.wav")
        encode_rate = _integer(binding.get("encode_rate", 26_000), 1, 55_555,
                               f"{track_name}.encode_rate")
        root_note = _integer(binding.get("root_note", 60), 0, 127,
                             f"{track_name}.root_note")
        base_level = _integer(binding.get("level", 224), 0, 255, f"{track_name}.level")
        pan = _pan(binding.get("pan", "C"), f"{track_name}.pan")
        loop = bool(binding.get("loop", False))
        encoded = prepare_adpcm_b(wav, encode_rate)
        resources.append(SampleResource(next_sample_id, 2, wav.name, encoded.encoded,
                                        encode_rate, base_level, pan, root_note))
        b_settings = (next_sample_id, encode_rate, root_note, base_level, pan, loop)
        next_sample_id += 1

    a_tracks = [(name, binding) for name, binding in bindings.items()
                if binding["voice"] == "ADPCM-A"]
    if a_tracks:
        track_name, binding = a_tracks[0]
        note_map = binding.get("note_map")
        if not isinstance(note_map, dict) or not note_map:
            raise CompileError(f"{track_name}.note_map: objet non vide attendu")
        parsed_note_map: list[tuple[int, Any]] = []
        for note_text, entry in note_map.items():
            try:
                note = int(note_text)
            except (TypeError, ValueError) as error:
                raise CompileError(f"{track_name}: note ADPCM-A invalide: {note_text}") from error
            parsed_note_map.append((note, entry))
        for note, entry in sorted(parsed_note_map):
            _integer(note, 0, 127, f"{track_name}.note_map")
            if not isinstance(entry, dict):
                raise CompileError(f"{track_name}: entree ADPCM-A {note} invalide")
            wav = _resolve(project_dir, entry.get("wav"), f"ADPCM-A note {note}.wav")
            level = _integer(entry.get("level", 0), 0, 31, f"ADPCM-A note {note}.level")
            pan = _pan(entry.get("pan", "C"), f"ADPCM-A note {note}.pan")
            encoded = prepare_adpcm_a(wav)
            resources.append(SampleResource(next_sample_id, 1, wav.name, encoded.encoded,
                                            round(ADPCM_A_RATE), level, pan, note))
            a_note_ids[note] = (next_sample_id, level, pan)
            next_sample_id += 1

    mixer = project.get("mixer", {})
    if not isinstance(mixer, dict):
        raise CompileError("mixer: objet attendu")
    gains = tuple(
        _integer(mixer.get(name, default), 0, 0xBF, f"mixer.{name}")
        for name, default in (("fm", 12), ("ssg", 20), ("adpcm_a", 8),
                              ("adpcm_b", 12), ("master", 4))
    )
    noise_period = _integer(project.get("ssg_noise_period", 4), 1, 31,
                            "ssg_noise_period")
    timeline = Timeline()
    initialization = bytearray()
    initialization += wrn(0x0188, gains)
    initialization += wr8(0x018D, 0xC0)
    for channel, patch in sorted(fm_bindings.values(), key=lambda value: value[0]):
        initialization += fm_patch_bytes(channel, patch)
    ssg_mixer = 0x3F
    for channel, tone, noise, _volume in ssg_bindings.values():
        if tone:
            ssg_mixer &= ~(1 << channel)
        if noise:
            ssg_mixer &= ~(1 << (channel + 3))
    ssg_registers = [0, 0, 0, 0, 0, 0, noise_period, ssg_mixer, 0, 0, 0, 0, 0, 0]
    initialization += wrn(0x0100, ssg_registers)
    timeline.add_cycles(0, bytes(initialization))

    tick_clock = TickClock(song.ppqn, song.tempos)
    current_notes: dict[str, int] = {}
    warnings: list[str] = []
    retriggers: dict[str, int] = {}

    for event in song.notes:
        binding = bindings[event.track]
        voice = str(binding["voice"])
        channel_filter = binding.get("midi_channel")
        if channel_filter is not None:
            expected = _integer(channel_filter, 1, 16, f"{event.track}.midi_channel") - 1
            if event.channel != expected:
                continue
        cycle = tick_clock.to_cycles(event.tick)

        if event.note_on and voice in current_notes:
            retriggers[voice] = retriggers.get(voice, 0) + 1
        if voice.startswith("FM"):
            channel, patch = fm_bindings[event.track]
            if event.note_on:
                payload = _fm_velocity(channel, patch, event.velocity)
                payload += fm_note_on(channel, event.note, patch)
                timeline.add_cycles(cycle, payload)
                current_notes[voice] = event.note
            elif current_notes.get(voice) == event.note:
                timeline.add_cycles(cycle, fm_note_off(channel, patch))
                current_notes.pop(voice, None)
            continue

        if voice.startswith("SSG-"):
            channel, tone, _noise, base_volume = ssg_bindings[event.track]
            if event.note_on:
                payload = bytearray()
                if tone:
                    period = midi_to_ssg_period(event.note)
                    payload += wrn(0x0100 + channel * 2, (period & 0xFF, period >> 8))
                volume = max(1, round(base_volume * event.velocity / 127))
                payload += wr8(0x0108 + channel, volume)
                timeline.add_cycles(cycle, bytes(payload))
                current_notes[voice] = event.note
            elif current_notes.get(voice) == event.note:
                timeline.add_cycles(cycle, wr8(0x0108 + channel, 0))
                current_notes.pop(voice, None)
            continue

        if voice == "ADPCM-A":
            if event.note_on:
                setting = a_note_ids.get(event.note)
                if setting is None:
                    raise CompileError(f"ADPCM-A: note MIDI {event.note} absente du note_map")
                sample_id, base_level, pan = setting
                level = min(31, base_level + round((127 - event.velocity) * 12 / 127))
                timeline.add_cycles(cycle, play_a(sample_id, level, pan))
            continue

        if voice == "ADPCM-B":
            if b_settings is None:
                raise CompileError("ADPCM-B affectee sans ressource")
            sample_id, base_rate, root_note, base_level, pan, loop = b_settings
            if event.note_on:
                rate = base_rate * 2.0 ** ((event.note - root_note) / 12.0)
                delta = max(1, min(0xFFFF, round(rate * 65_536 / float(ADPCM_B_SERVICE_RATE))))
                level = max(1, round(base_level * event.velocity / 127))
                timeline.add_cycles(cycle, play_b(sample_id, delta, level, pan, loop))
                current_notes[voice] = event.note
            elif current_notes.get(voice) == event.note:
                timeline.add_cycles(cycle, stop_b())
                current_notes.pop(voice, None)

    for voice, count in sorted(retriggers.items()):
        warnings.append(f"{voice}: {count} retrigger(s) monophonique(s)")

    end_cycle = tick_clock.to_cycles(song.end_tick)
    shutdown = bytearray()
    for _track, (channel, patch) in sorted(fm_bindings.items()):
        shutdown += fm_note_off(channel, patch)
    shutdown += wrn(0x0108, (0, 0, 0))
    shutdown += stop_a()
    shutdown += stop_b()
    timeline.add_cycles(end_cycle, bytes(shutdown))
    halt_padding_ms = _integer(project.get("halt_padding_ms", 250), 0, 10_000,
                               "halt_padding_ms")
    halt_cycle = end_cycle + halt_padding_ms * SYSTEM_CLOCK // 1000
    code = timeline.compile(halt_cycle)

    metadata = {
        "title": project.get("title", project_path.stem),
        "author": project.get("author", "Auteur non renseigne"),
        "compiler": "DMS Compiler P0.4.1 / DMSPROJ-0.1",
        "source_midi": midi_path.name,
        "midi_format": song.format_type,
        "midi_ppqn": song.ppqn,
        "timing": "absolute MIDI ticks to 24 MHz; no added quantization",
        "adpcm_a_rate": "8000000/432",
        "adpcm_b_nominal": b_settings[1] if b_settings is not None else "none",
    }
    meta = "".join(f"{key}={_meta_value(value)}\n" for key, value in metadata.items()).encode("utf-8")
    rom = pack_rom(code, meta, tuple(resources))
    report = CompileReport(
        len(rom),
        sum(event.note_on for event in song.notes),
        len(song.tempos),
        halt_cycle,
        sum(resource.codec == 1 for resource in resources),
        sum(resource.codec == 2 for resource in resources),
        tuple(sorted(used_voices)),
        tuple(warnings),
    )
    return rom, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="projet DMSPROJ-0.1")
    parser.add_argument("--out", type=Path, help="ROM DMR de sortie")
    args = parser.parse_args()
    output = args.out or args.project.with_suffix(".dmr")
    try:
        rom, report = compile_project(args.project)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(rom)
    except (CompileError, OSError, ValueError) as error:
        print(f"DMS Compiler: {error}", file=sys.stderr)
        return 1
    print(f"DMS Compiler P0.4.1 | {PROJECT_FORMAT}")
    print(f"ROM: {output} | {report.rom_bytes} octets | HALT {report.halt_cycle / SYSTEM_CLOCK:.6f} s")
    print(f"MIDI: {report.midi_notes} note-on | {report.tempo_events} tempo | timing non quantifie")
    print(f"Voix: {', '.join(report.used_voices)}")
    print(f"Samples: ADPCM-A={report.resources_a} | ADPCM-B={report.resources_b}")
    for warning in report.warnings:
        print(f"ATTENTION: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
