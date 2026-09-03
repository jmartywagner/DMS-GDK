#!/usr/bin/env python3
"""DMS-1 Audio Asset Builder engine.

Small standalone batch converter for DMS-1 ADPCM resources.
Designed to be called by the Windows drag-and-drop PowerShell UI.
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
import json
import math
import re
import struct
import sys
import wave
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Sequence

ADPCM_A_RATE = Fraction(8_000_000, 432)          # ~18518.5185 Hz
ADPCM_B_SERVICE_RATE = Fraction(8_000_000, 144)  # ~55555.5556 Hz
DEFAULT_ADPCM_B_RATE = 26_000
PAGE_BYTES = 256
PAGE_DECODED_SAMPLES = PAGE_BYTES * 2
AUTO_A_MAX_SECONDS = 1.5

@dataclass(frozen=True)
class WavData:
    samples: tuple[int, ...]
    sample_rate: int
    channels: int
    bits_per_sample: int

@dataclass(frozen=True)
class EncodedSample:
    encoded: bytes
    pcm: tuple[int, ...]
    nominal_rate: int
    exact_rate: Fraction
    source: WavData

    @property
    def pages(self) -> int:
        return len(self.encoded) // PAGE_BYTES


def _signed_24(data: bytes, offset: int) -> int:
    value = data[offset] | (data[offset + 1] << 8) | (data[offset + 2] << 16)
    if value & 0x800000:
        value -= 1 << 24
    return value


def read_pcm_wav(path: Path) -> WavData:
    with wave.open(str(path), "rb") as stream:
        if stream.getcomptype() != "NONE":
            raise ValueError("WAV compressé non accepté (PCM uniquement)")
        channels = stream.getnchannels()
        width = stream.getsampwidth()
        sample_rate = stream.getframerate()
        frame_count = stream.getnframes()
        raw = stream.readframes(frame_count)
    if channels < 1 or channels > 8:
        raise ValueError(f"nombre de canaux non supporté: {channels}")
    if width not in (1, 2, 3, 4):
        raise ValueError(f"résolution PCM non supportée: {width * 8} bits")
    expected = frame_count * channels * width
    if len(raw) != expected:
        raise ValueError("WAV tronqué")
    mono: list[int] = []
    cursor = 0
    for _ in range(frame_count):
        total = 0
        for _ch in range(channels):
            if width == 1:
                value = (raw[cursor] - 128) << 8
            elif width == 2:
                value = struct.unpack_from("<h", raw, cursor)[0]
            elif width == 3:
                value = _signed_24(raw, cursor) >> 8
            else:
                value = struct.unpack_from("<i", raw, cursor)[0] >> 16
            total += value
            cursor += width
        mono.append(max(-32768, min(32767, int(total / channels))))
    if not mono:
        raise ValueError("WAV vide")
    return WavData(tuple(mono), sample_rate, channels, width * 8)


def resample_windowed_sinc(samples: Sequence[int], source_rate: int, target_rate: Fraction, radius: int = 18) -> tuple[int, ...]:
    if not samples:
        return ()
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("fréquence invalide")
    target_count = max(1, round(Fraction(len(samples), source_rate) * target_rate))
    ratio = float(target_rate) / source_rate
    cutoff = 0.5 * min(1.0, ratio) * 0.94
    source_step = source_rate / float(target_rate)
    output: list[int] = []
    for output_index in range(target_count):
        position = output_index * source_step
        center = math.floor(position)
        first = max(0, center - radius + 1)
        last = min(len(samples) - 1, center + radius)
        weighted = 0.0
        weight_sum = 0.0
        for source_index in range(first, last + 1):
            distance = source_index - position
            if abs(distance) >= radius:
                continue
            argument = 2.0 * cutoff * distance
            sinc = 1.0 if argument == 0.0 else math.sin(math.pi * argument) / (math.pi * argument)
            window = 0.5 + 0.5 * math.cos(math.pi * distance / radius)
            weight = 2.0 * cutoff * sinc * window
            weighted += samples[source_index] * weight
            weight_sum += weight
        value = 0 if weight_sum == 0.0 else round(weighted / weight_sum)
        output.append(max(-32768, min(32767, value)))
    return tuple(output)


def pad_pcm_to_page(samples: Sequence[int]) -> tuple[int, ...]:
    padding = (-len(samples)) % PAGE_DECODED_SAMPLES
    return tuple(samples) + (0,) * padding

ADPCM_A_STEPS = (
    16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,73,80,88,97,107,118,130,143,157,173,
    190,209,230,253,279,307,337,371,408,449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552,
)
ADPCM_A_INDEX = (-1,-1,-1,-1,2,5,7,9)

def _signed_12(value: int) -> int:
    value &= 0xFFF
    return value - 0x1000 if value & 0x800 else value


def adpcm_a_encode(pcm: Sequence[int]) -> bytes:
    accumulator = 0
    step_index = 0
    nibbles: list[int] = []
    for pcm_value in pad_pcm_to_page(pcm):
        target = max(-2048, min(2047, round(pcm_value / 16)))
        step = ADPCM_A_STEPS[step_index]
        best_nibble = 0
        best_accumulator = accumulator
        best_error = 1 << 60
        for nibble in range(16):
            delta = (2 * (nibble & 7) + 1) * step // 8
            if nibble & 8:
                delta = -delta
            candidate = (accumulator + delta) & 0xFFF
            error = abs(target - _signed_12(candidate))
            if error < best_error:
                best_error = error
                best_nibble = nibble
                best_accumulator = candidate
        nibbles.append(best_nibble)
        accumulator = best_accumulator
        step_index = max(0, min(48, step_index + ADPCM_A_INDEX[best_nibble & 7]))
    encoded = bytearray()
    for i in range(0, len(nibbles), 2):
        encoded.append((nibbles[i] << 4) | nibbles[i + 1])
    if len(encoded) % PAGE_BYTES:
        raise AssertionError("ADPCM-A hors pages 256")
    return bytes(encoded)

ADPCM_B_SCALES = (57,57,57,57,77,102,128,153)

def adpcm_b_encode(pcm: Sequence[int]) -> bytes:
    accumulator = 0
    step = 127
    nibbles: list[int] = []
    for target in pad_pcm_to_page(pcm):
        best_nibble = 0
        best_value = accumulator
        best_error = 1 << 60
        for nibble in range(16):
            delta = (2 * (nibble & 7) + 1) * step // 8
            if nibble & 8:
                delta = -delta
            candidate = max(-32768, min(32767, accumulator + delta))
            error = abs(target - candidate)
            if error < best_error:
                best_error = error
                best_nibble = nibble
                best_value = candidate
        nibbles.append(best_nibble)
        accumulator = best_value
        step = max(127, min(24576, step * ADPCM_B_SCALES[best_nibble & 7] // 64))
    encoded = bytearray()
    for i in range(0, len(nibbles), 2):
        encoded.append((nibbles[i] << 4) | nibbles[i + 1])
    if len(encoded) % PAGE_BYTES:
        raise AssertionError("ADPCM-B hors pages 256")
    return bytes(encoded)


def prepare_a(path: Path) -> EncodedSample:
    source = read_pcm_wav(path)
    converted = resample_windowed_sinc(source.samples, source.sample_rate, ADPCM_A_RATE)
    return EncodedSample(adpcm_a_encode(converted), converted, round(ADPCM_A_RATE), ADPCM_A_RATE, source)


def prepare_b(path: Path, rate: int) -> EncodedSample:
    if rate <= 0 or rate > round(ADPCM_B_SERVICE_RATE):
        raise ValueError(f"ADPCM-B: fréquence 1..{round(ADPCM_B_SERVICE_RATE)} Hz")
    source = read_pcm_wav(path)
    exact = Fraction(rate, 1)
    converted = resample_windowed_sinc(source.samples, source.sample_rate, exact)
    return EncodedSample(adpcm_b_encode(converted), converted, rate, exact, source)


def c_symbol(name: str) -> str:
    s = name.upper()
    s = re.sub(r"[^A-Z0-9]+", "_", s).strip("_")
    if not s:
        s = "SAMPLE"
    if s[0].isdigit():
        s = "S_" + s
    return s


def unique_symbols(paths: list[Path]) -> list[str]:
    used: dict[str, int] = {}
    out: list[str] = []
    for p in paths:
        base = c_symbol(p.stem)
        n = used.get(base, 0) + 1
        used[base] = n
        out.append(base if n == 1 else f"{base}_{n}")
    return out


def analyze_path(path: Path) -> dict:
    try:
        wav = read_pcm_wav(path)
        duration = len(wav.samples) / wav.sample_rate if wav.sample_rate else 0.0
        auto = "A" if duration <= AUTO_A_MAX_SECONDS else "B"
        a_samples = max(1, round(Fraction(len(wav.samples), wav.sample_rate) * ADPCM_A_RATE))
        b_samples = max(1, round(Fraction(len(wav.samples), wav.sample_rate) * DEFAULT_ADPCM_B_RATE))
        a_bytes = ((a_samples + PAGE_DECODED_SAMPLES - 1) // PAGE_DECODED_SAMPLES) * PAGE_BYTES
        b_bytes = ((b_samples + PAGE_DECODED_SAMPLES - 1) // PAGE_DECODED_SAMPLES) * PAGE_BYTES
        return {
            "path": str(path), "name": path.name, "ok": True,
            "duration": duration, "source_rate": wav.sample_rate, "channels": wav.channels,
            "bits": wav.bits_per_sample, "auto": auto,
            "estimated_a_bytes": a_bytes, "estimated_b_bytes": b_bytes,
            "warning": "stéréo -> mono" if wav.channels > 1 else "",
        }
    except Exception as exc:
        return {"path": str(path), "name": path.name, "ok": False, "error": str(exc), "auto": "A"}


def collect_paths(entries: list[str]) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for raw in entries:
        p = Path(raw.strip().strip('"'))
        candidates = sorted(p.rglob("*.wav")) if p.is_dir() else [p]
        for q in candidates:
            if q.suffix.lower() != ".wav" or not q.is_file():
                continue
            key = str(q.resolve()).lower()
            if key not in seen:
                seen.add(key)
                found.append(q.resolve())
    return found


def analyze_list(list_path: Path) -> int:
    entries = list_path.read_text(encoding="utf-8-sig").splitlines()
    paths = collect_paths(entries)
    json.dump([analyze_path(p) for p in paths], sys.stdout, ensure_ascii=True)
    return 0


def _build_into(plan_path: Path, out_dir: Path, b_rate: int) -> int:
    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    rows = plan.get("samples", [])
    if not rows:
        raise ValueError("aucun sample")
    if b_rate <= 0 or b_rate > round(ADPCM_B_SERVICE_RATE):
        raise ValueError(f"fréquence B invalide: {b_rate}")

    paths = [Path(r["path"]).resolve() for r in rows]
    symbols = unique_symbols(paths)
    out_dir.mkdir(parents=True, exist_ok=True)
    encoded_dir = out_dir / "encoded"
    encoded_dir.mkdir(exist_ok=True)

    bank = bytearray()
    defs: list[dict] = []
    errors: list[dict] = []

    for index, (row, path, sym) in enumerate(zip(rows, paths, symbols)):
        try:
            target = str(row.get("target", "AUTO")).upper()
            meta = analyze_path(path)
            if not meta.get("ok"):
                raise ValueError(meta.get("error", "WAV invalide"))
            if target == "AUTO":
                target = meta["auto"]
            if target not in ("A", "B"):
                raise ValueError(f"cible inconnue {target}")
            encoded = prepare_a(path) if target == "A" else prepare_b(path, b_rate)
            # Explicit page alignment. Current codecs already return whole pages.
            if len(bank) % PAGE_BYTES:
                bank += bytes(PAGE_BYTES - len(bank) % PAGE_BYTES)
            offset = len(bank)
            start_page = offset // PAGE_BYTES
            bank += encoded.encoded
            end_page = len(bank) // PAGE_BYTES - 1
            if end_page > 0xFFFF:
                raise ValueError("banque audio > 65536 pages (16 Mio), adressage ADPCM DMS-1 dépassé")
            rate = encoded.nominal_rate
            individual = encoded_dir / f"{index:03d}_{sym}.adpcm{target.lower()}"
            individual.write_bytes(encoded.encoded)
            defs.append({
                "id": index, "symbol": sym, "name": path.stem, "source": str(path),
                "codec": target, "rate_hz": rate, "offset_bytes": offset,
                "start_page": start_page, "end_page": end_page,
                "encoded_bytes": len(encoded.encoded), "pages": encoded.pages,
                "decoded_samples": len(encoded.pcm),
                "duration_seconds": len(encoded.pcm) / rate if rate else 0.0,
                "source_rate": encoded.source.sample_rate,
                "source_channels": encoded.source.channels,
                "source_bits": encoded.source.bits_per_sample,
            })
        except Exception as exc:
            errors.append({"source": str(path), "error": str(exc)})

    if errors:
        raise ValueError("Conversion interrompue: " + "; ".join(f"{Path(e['source']).name}: {e['error']}" for e in errors))

    (out_dir / "audio_bank.bin").write_bytes(bank)

    ids_h = [
        "#ifndef DMS_AUDIO_SAMPLE_IDS_H",
        "#define DMS_AUDIO_SAMPLE_IDS_H",
        "",
    ]
    for d in defs:
        ids_h.append(f"#define DMS_SAMPLE_{d['symbol']} {d['id']}")
    ids_h += ["", f"#define DMS_SAMPLE_COUNT {len(defs)}", "", "#endif", ""]
    (out_dir / "audio_sample_ids.h").write_text("\n".join(ids_h), encoding="utf-8")

    header = [
        "#ifndef DMS_AUDIO_SAMPLES_H",
        "#define DMS_AUDIO_SAMPLES_H",
        "",
        "#include <stdint.h>",
        "#include \"audio_sample_ids.h\"",
        "",
        "#define DMS_AUDIO_CODEC_ADPCM_A 1",
        "#define DMS_AUDIO_CODEC_ADPCM_B 2",
        "#define DMS_AUDIO_BANK_PAGE_BYTES 256",
        "",
        "typedef struct DmsAudioSampleDef {",
        "    uint32_t offset_bytes;",
        "    uint32_t encoded_bytes;",
        "    uint16_t start_page;",
        "    uint16_t end_page;",
        "    uint16_t rate_hz;",
        "    uint8_t codec;",
        "    uint8_t reserved;",
        "} DmsAudioSampleDef;",
        "",
        "extern const DmsAudioSampleDef dms_audio_samples[DMS_SAMPLE_COUNT];",
        "extern const uint16_t dms_audio_sample_count;",
        "",
    ]
    # Also emit macro-only metadata for the current minimal GDK bootstrap.
    for d in defs:
        prefix = f"DMS_SAMPLE_{d['symbol']}"
        codec_num = 1 if d["codec"] == "A" else 2
        header += [
            f"#define {prefix}_CODEC {codec_num}",
            f"#define {prefix}_START_PAGE {d['start_page']}",
            f"#define {prefix}_END_PAGE {d['end_page']}",
            f"#define {prefix}_RATE_HZ {d['rate_hz']}",
            f"#define {prefix}_OFFSET_BYTES {d['offset_bytes']}u",
            f"#define {prefix}_SIZE_BYTES {d['encoded_bytes']}u",
            "",
        ]
    header += ["#endif", ""]
    (out_dir / "audio_samples.h").write_text("\n".join(header), encoding="utf-8")

    source = [
        '#include "audio_samples.h"',
        "",
        "const DmsAudioSampleDef dms_audio_samples[DMS_SAMPLE_COUNT] = {",
    ]
    for d in defs:
        codec = "DMS_AUDIO_CODEC_ADPCM_A" if d["codec"] == "A" else "DMS_AUDIO_CODEC_ADPCM_B"
        source.append(
            f"    {{{d['offset_bytes']}u, {d['encoded_bytes']}u, {d['start_page']}, {d['end_page']}, {d['rate_hz']}, {codec}, 0}}, /* {d['symbol']} */"
        )
    source += ["};", f"const uint16_t dms_audio_sample_count = {len(defs)};", ""]
    (out_dir / "audio_samples.c").write_text("\n".join(source), encoding="utf-8")

    manifest = {
        "format": "DMS1-AUDIO-ASSET-BUILDER-0.1",
        "page_bytes": PAGE_BYTES,
        "adpcm_a_exact_rate_hz": float(ADPCM_A_RATE),
        "adpcm_b_rate_hz": b_rate,
        "auto_rule": f"A si durée <= {AUTO_A_MAX_SECONDS:.1f}s, sinon B",
        "bank_bytes": len(bank),
        "bank_pages": len(bank) // PAGE_BYTES,
        "samples": defs,
    }
    (out_dir / "audio_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    readme = f"""DMS-1 AUDIO ASSET BUILDER P0.1\n\nFichiers générés\n-----------------\naudio_bank.bin       Banque ADPCM, alignée sur pages de {PAGE_BYTES} octets\naudio_sample_ids.h   IDs C stables des samples\naudio_samples.h      Déclarations + métadonnées par sample\naudio_samples.c      Table de métadonnées\naudio_manifest.json  Rapport complet de conversion\nencoded/             Fichiers ADPCM individuels de contrôle\n\nRègle AUTO\n----------\nA si durée <= {AUTO_A_MAX_SECONDS:.1f} s ; B au-delà. Cette règle est seulement une proposition : la fenêtre permet de forcer A ou B.\n\nDMS-1\n-----\nADPCM-A : {float(ADPCM_A_RATE):.6f} Hz, fixe.\nADPCM-B : {b_rate} Hz pour cet export (service matériel {float(ADPCM_B_SERVICE_RATE):.6f} Hz).\nLes WAV stéréo sont sommés en mono avant conversion. Les données sont paddées sur pages de 256 octets.\n\nIntégration GDK\n---------------\nInclure audio_sample_ids.h / audio_samples.h et ajouter audio_samples.c + audio_bank.bin aux ressources du projet.\nLe bootstrap GDK P1.0.7 actuellement fourni dans la console ne possède pas encore l'appel de lecture SFX individuel côté Z80 ; le Builder prépare donc les ressources et leurs déclarations exactes sans inventer une API de lecture qui n'existe pas encore.\n"""
    (out_dir / "LISEZ_MOI_GDK.txt").write_text(readme, encoding="utf-8")

    summary = {"ok": True, "count": len(defs), "bank_bytes": len(bank), "out": str(out_dir)}
    json.dump(summary, sys.stdout, ensure_ascii=True)
    return 0


def build(plan_path: Path, out_dir: Path, b_rate: int) -> int:
    """Build in staging and commit only after the whole bank validates."""
    out_dir = out_dir.resolve(); out_dir.parent.mkdir(parents=True, exist_ok=True)
    owned = ("audio_bank.bin", "audio_sample_ids.h", "audio_samples.h", "audio_samples.c", "audio_manifest.json", "LISEZ_MOI_GDK.txt", "encoded")
    stage = Path(tempfile.mkdtemp(prefix=".dms_audio_asset_stage_", dir=str(out_dir.parent)))
    backup = Path(tempfile.mkdtemp(prefix=".dms_audio_asset_backup_", dir=str(out_dir.parent)))
    try:
        rc = _build_into(plan_path, stage, b_rate)
        out_dir.mkdir(parents=True, exist_ok=True)
        moved: list[str] = []
        try:
            for name in owned:
                dest = out_dir / name
                if dest.exists(): shutil.move(str(dest), str(backup / name))
                src = stage / name
                if src.exists(): shutil.move(str(src), str(dest))
                moved.append(name)
        except Exception:
            for name in reversed(moved):
                dest = out_dir / name
                if dest.is_dir(): shutil.rmtree(dest, ignore_errors=True)
                elif dest.exists(): dest.unlink()
                old = backup / name
                if old.exists(): shutil.move(str(old), str(dest))
            raise
        return rc
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--analyze-list", type=Path)
    g.add_argument("--build", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--b-rate", type=int, default=DEFAULT_ADPCM_B_RATE)
    args = ap.parse_args()
    try:
        if args.analyze_list:
            return analyze_list(args.analyze_list)
        if not args.out:
            raise ValueError("--out requis avec --build")
        return build(args.build, args.out, args.b_rate)
    except Exception as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, ensure_ascii=True)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
