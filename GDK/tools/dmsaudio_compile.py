#!/usr/bin/env python3
"""Compilation headless des projets DMS-1 Audio Integration Lab P0.2/P0.3.

P0.3 is cartridge-oriented: many DMR songs, one global SFX bank, and one
9-channel music-priority profile per song. The actual DMR files remain MUSIC
resources; this compiler exports the global SFX contract and music profiles.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "RUNTIME" / "tools"))
from dms_audio import PAGE_BYTES, prepare_adpcm_a, prepare_adpcm_b  # noqa: E402
try:
    from dms_cartridge import inspect_dmr_bytes
except Exception:  # pragma: no cover - validation fallback
    inspect_dmr_bytes = None

FORMAT_V2 = "DMS1-AUDIO-LAB-PROJECT-0.2"
FORMAT_V3 = "DMS1-AUDIO-LAB-PROJECT-0.3"
EXPORT_V3 = "DMS1-AUDIO-LAB-EXPORT-0.3"
PAN = {"L": 0x80, "C": 0xC0, "R": 0x40}
CHANNELS = ("FM1", "FM2", "FM3", "FM4", "SSG1", "SSG2", "SSG3", "ADPCM-A", "ADPCM-B")
DEFAULT_PATCH = {
    "algorithm": 7, "feedback": 2, "multiples": [1, 2, 3, 1], "fine": [0, 0, 0, 0],
    "waves": [0, 0, 0, 0], "levels": [16, 32, 40, 45], "attacks": [31] * 4,
    "decays": [10] * 4, "sustains": [4] * 4, "sustain_levels": [8] * 4, "releases": [6] * 4,
}


class AudioCompileError(RuntimeError):
    pass


def symbol(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]+", "_", name.upper()).strip("_") or "SFX"
    return "S_" + value if value[0].isdigit() else value


def clamp(value: Any, low: int, high: int) -> int:
    try:
        number = int(round(float(value)))
    except Exception:
        number = low
    return max(low, min(high, number))


def midi_to_opz_keycode(note: int) -> int:
    note = clamp(note, 13, 108)
    codes = (14, 0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13)
    block = note // 12 - 1
    if note % 12 == 0:
        block -= 1
    return (block << 4) | codes[note % 12]


def midi_to_ssg_period(note: float) -> int:
    frequency = 440.0 * 2.0 ** ((float(note) - 69.0) / 12.0)
    return max(1, min(0xFFF, round(2_000_000 / (16.0 * frequency))))


def _arr(patch: dict[str, Any], key: str, default: int = 0) -> list[int]:
    raw = patch.get(key, [default] * 4)
    return [int(raw[i]) if isinstance(raw, list) and i < len(raw) else default for i in range(4)]


def fm_patch_pairs(channel: int, patch: dict[str, Any]) -> list[tuple[int, int]]:
    alg = int(patch.get("algorithm", 7)) & 7
    fb = int(patch.get("feedback", 0)) & 7
    mult = _arr(patch, "multiples", 1); fine = _arr(patch, "fine"); waves = _arr(patch, "waves")
    levels = _arr(patch, "levels", 32); attacks = _arr(patch, "attacks", 31); decays = _arr(patch, "decays", 8)
    sustains = _arr(patch, "sustains", 4); sl = _arr(patch, "sustain_levels", 8); rel = _arr(patch, "releases", 6)
    rr = _arr(patch, "reverb_rates"); detunes = _arr(patch, "detunes"); ksr = _arr(patch, "key_scale_rates")
    fixed = _arr(patch, "fixed_modes"); frange = _arr(patch, "fixed_ranges"); ffreq = _arr(patch, "fixed_frequencies")
    amen = _arr(patch, "am_enables"); dt2 = _arr(patch, "detune2"); egshift = _arr(patch, "eg_shifts")
    pms = int(patch.get("lfo_pitch_sensitivity", 0)) & 7; ams = int(patch.get("lfo_amplitude_sensitivity", 0)) & 3
    pairs = [(0x0038 + channel, (pms << 4) | ams), (0x0030 + channel, 1)]
    for group in range(4):
        off = channel + group * 8
        freq = ((frange[group] & 7) << 4) | (ffreq[group] & 15) if fixed[group] else ((detunes[group] & 7) << 4) | (mult[group] & 15)
        pairs += [
            (0x0040 + off, freq), (0x0040 + off, 0x80 | ((waves[group] & 7) << 4) | (fine[group] & 15)),
            (0x0060 + off, levels[group] & 0x7F), (0x0080 + off, ((ksr[group] & 3) << 6) | ((fixed[group] & 1) << 5) | (attacks[group] & 31)),
            (0x00A0 + off, ((amen[group] & 1) << 7) | (decays[group] & 31)), (0x00C0 + off, ((dt2[group] & 3) << 6) | (sustains[group] & 31)),
            (0x00C0 + off, ((egshift[group] & 3) << 6) | 0x20 | (rr[group] & 7)), (0x00E0 + off, ((sl[group] & 15) << 4) | (rel[group] & 15)),
        ]
    pairs.append((0x0020 + channel, 0x80 | (fb << 3) | alg))
    return pairs


def _resolve(project: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (project.parent / path).resolve()


def _bank_identity(path: Path) -> str:
    try:
        return str(path.resolve()).casefold()
    except Exception:
        return str(path).casefold()

def _load_patch_banks(project: Path, package_root: Path, rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Load OPZ patches without collapsing identical keys from different banks."""
    banks: dict[tuple[str, str], dict[str, Any]] = {}
    paths = [package_root / "TOOLS" / "AUDIO" / "DMS1_AUDIO_LAB_VST3" / "instruments" / "factory_opz.json"]
    for row in rows:
        value = str(row.get("patch_bank", "")).strip()
        if value:
            paths.append(_resolve(project, value))
    seen: set[str] = set()
    for path in paths:
        ident = _bank_identity(path)
        if ident in seen:
            continue
        seen.add(ident)
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if data.get("format") != "DMS-OPZ-BANK-0.1":
            continue
        for key, patch in (data.get("patches") or {}).items():
            if isinstance(patch, dict):
                banks[(ident, str(key))] = patch
    return banks


def _music_rows(project: Path, data: dict[str, Any]) -> list[dict[str, Any]]:
    fmt = data.get("format")
    if fmt == FORMAT_V3:
        raw = data.get("musics") or []
    else:
        raw = [{"uid": 1, "name": "", "source_path": data.get("dmr", ""), "music_priorities": data.get("music_priorities", {})}]
    result = []
    seen: set[str] = set()
    seen_symbols: set[str] = set()
    for index, row in enumerate(raw):
        ref = str(row.get("source_path", row.get("dmr", ""))).strip()
        if not ref:
            continue
        path = _resolve(project, ref)
        if not path.is_file():
            raise AudioCompileError(f"musique DMR absente : {ref}")
        blob = path.read_bytes()
        if len(blob) < 64 or blob[:4] != b"DMR0":
            raise AudioCompileError(f"DMR invalide : {path.name}")
        title = path.stem
        if inspect_dmr_bytes is not None:
            try:
                title = inspect_dmr_bytes(blob, path.name).title
            except Exception as exc:
                raise AudioCompileError(str(exc)) from exc
        name = str(row.get("name", "")).strip() or title or path.stem
        key = path.name.casefold()
        if key in seen:
            raise AudioCompileError(f"musique dupliquée dans Audio Lab : {path.name}")
        seen.add(key)
        msym = symbol(name)
        if msym in seen_symbols:
            raise AudioCompileError(f"nom/symbole musique dupliqué : {msym}")
        seen_symbols.add(msym)
        src_prio = row.get("music_priorities") or row.get("priorities") or {}
        priorities = {ch: clamp(src_prio.get(ch, 50), 0, 100) for ch in CHANNELS}
        result.append({
            "id": index,
            "uid": int(row.get("uid", index + 1)),
            "name": name,
            "symbol": msym,
            "source_music": path.name,
            "source_stem": path.stem,
            "source_path_abs": str(path),
            "music_priorities": priorities,
        })
    if not result:
        raise AudioCompileError("aucune musique DMR dans le projet Audio Lab")
    return result


def _compile_audio_project_into(project: Path, out: Path, package_root: Path = ROOT) -> dict[str, Any]:
    project = project.resolve(); out = out.resolve()
    data = json.loads(project.read_text(encoding="utf-8-sig"))
    if data.get("format") not in (FORMAT_V2, FORMAT_V3):
        raise AudioCompileError(f"format {FORMAT_V3} (ou ancien P0.2) requis")
    rows = data.get("sfx") or []
    if not isinstance(rows, list):
        raise AudioCompileError("sfx doit être une liste")
    uids = [int(row.get("uid", i + 1)) for i, row in enumerate(rows)]
    if len(set(uids)) != len(uids):
        dup = next(uid for uid in uids if uids.count(uid) > 1)
        raise AudioCompileError(f"UID SFX dupliqué : {dup}")
    musics = _music_rows(project, data)
    out.mkdir(parents=True, exist_ok=True)
    music_out = out / "musics"
    music_out.mkdir(parents=True, exist_ok=True)
    for stale in music_out.glob("*.dmr"):
        stale.unlink()
    for music in musics:
        src = Path(str(music.pop("source_path_abs")))
        filename = f"{int(music['id']):03d}_{symbol(str(music['name']))}.dmr"
        dst = music_out / filename
        shutil.copyfile(src, dst)
        music["export_music"] = f"musics/{filename}"
    bank = bytearray()
    sample_pages: dict[int, tuple[int, int, int]] = {}
    banks = _load_patch_banks(project, package_root, rows)

    for row in rows:
        if str(row.get("kind", "")).upper() != "SAMPLE":
            continue
        ref = str(row.get("source_path", "")).strip()
        path = _resolve(project, ref)
        if not path.is_file():
            raise AudioCompileError(f"sample absent : {ref}")
        target = str(row.get("target", "ADPCM-A")).upper()
        if target not in ("ADPCM-A", "ADPCM-B"):
            raise AudioCompileError(f"{row.get('name', path.stem)} : cible sample invalide {target}")
        encoded = prepare_adpcm_a(path) if target == "ADPCM-A" else prepare_adpcm_b(path, clamp(row.get("rate", 26000), 8000, 55556))
        if len(bank) % PAGE_BYTES:
            bank += bytes(PAGE_BYTES - len(bank) % PAGE_BYTES)
        start = len(bank) // PAGE_BYTES
        bank += encoded.encoded
        end = len(bank) // PAGE_BYTES - 1
        sample_pages[int(row.get("uid", len(sample_pages) + 1))] = (start, end, int(round(float(encoded.exact_rate))))
    (out / "audio_bank.bin").write_bytes(bank)

    uid_to_id = {int(row.get("uid", i + 1)): i for i, row in enumerate(rows)}
    manifest_rows = []
    ids = ["#ifndef DMS_AUDIO_IDS_H", "#define DMS_AUDIO_IDS_H", ""]
    used_symbols: set[str] = set()
    for index, row in enumerate(rows):
        name = str(row.get("name", f"SFX_{index}")); sym = symbol(name)
        if sym in used_symbols:
            raise AudioCompileError(f"symbole SFX dupliqué : {sym}")
        used_symbols.add(sym)
        ids.append(f"#define DMS_SFX_{sym} {index}u")
        kind = str(row.get("kind", "SAMPLE")).upper(); target = str(row.get("target", "AUTO")).upper()
        duration_ms = clamp(row.get("duration_ms", 180), 1, 60000)
        program: list[list[int]] = []
        params: dict[str, Any] = {"p0": 0, "p1": 0, "p2": 0, "p3": 0, "p4": 0}
        if kind == "SAMPLE":
            uid = int(row.get("uid", index + 1))
            if uid not in sample_pages:
                raise AudioCompileError(f"{name}: sample non encodé")
            start, end, exact_rate = sample_pages[uid]
            params = {"p0": start, "p1": end, "p2": clamp(row.get("level", 0), 0, 255), "p3": PAN.get(str(row.get("pan", "C")).upper(), 0xC0), "p4": exact_rate}
        elif kind == "FM":
            key = str(row.get("patch_key", "")).strip()
            bank_ref = str(row.get("patch_bank", "")).strip()
            if not key:
                raise AudioCompileError(f"{name}: patch FM non sélectionné")
            bank_path = _resolve(project, bank_ref) if bank_ref else (package_root / "TOOLS" / "AUDIO" / "DMS1_AUDIO_LAB_VST3" / "instruments" / "factory_opz.json")
            patch = banks.get((_bank_identity(bank_path), key))
            if patch is None:
                raise AudioCompileError(f"{name}: patch FM introuvable dans {bank_path.name} : {key}")
            control = 0x80 | ((int(patch.get("feedback", 0)) & 7) << 3) | (int(patch.get("algorithm", 7)) & 7)
            program = [[a, d] for a, d in fm_patch_pairs(0, patch)]
            program += [[0x0028, midi_to_opz_keycode(clamp(row.get("note", 60), 13, 108))], [0x0030, 1], [0x0020, control | 0x40]]
        elif kind == "SSG":
            period = midi_to_ssg_period(float(row.get("note", 60)))
            tone = bool(row.get("ssg_tone", True)); noise = bool(row.get("ssg_noise", False)); mixer = 0x3F
            if tone: mixer &= ~1
            if noise: mixer &= ~(1 << 3)
            program = [[0x0100, period & 0xFF], [0x0101, period >> 8], [0x0107, mixer], [0x0108, clamp(row.get("ssg_volume", 15), 1, 31)]]
            if noise:
                program.insert(2, [0x0106, clamp(row.get("noise_period", 4), 1, 31)])
        elif kind == "COMPOSITE":
            member_uids = [int(uid) for uid in row.get("members", [])]
            missing = [uid for uid in member_uids if uid not in uid_to_id]
            if missing:
                raise AudioCompileError(f"{name}: membre(s) composite introuvable(s) : {', '.join(map(str, missing))}")
            members = [uid_to_id[uid] for uid in member_uids if uid_to_id[uid] != index]
            params["members"] = members
        else:
            raise AudioCompileError(f"type SFX inconnu : {kind}")
        if len(program) > 48:
            raise AudioCompileError(f"{name}: programme {len(program)} écritures > limite 48")
        manifest_rows.append({
            "id": index, "uid": int(row.get("uid", index + 1)), "name": name, "kind": kind, "target": target,
            "priority": clamp(row.get("priority", 50), 0, 100), "conflict": str(row.get("behaviour", "STEAL")).upper(),
            "duck_db": max(0.0, min(18.0, float(row.get("duck_db", 0)))),
            "duration_frames": max(1, int(round(duration_ms * 60 / 1000))), "params": params, "program": program,
        })
    ids += ["", f"#define DMS_SFX_COUNT {len(rows)}u", "", "#endif", ""]
    (out / "audio_ids.h").write_text("\n".join(ids), encoding="utf-8")

    manifest = {
        "format": EXPORT_V3,
        "runtime_contract": "DMS-GDK-AUDIO-RUNTIME-2-MULTIMUSIC",
        "source_project": project.name,
        "bank_bytes": len(bank),
        "musics": musics,
        "sfx": manifest_rows,
    }
    (out / "audio_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "README_AUDIO_EXPORT.txt").write_text(
        "DMS-1 GAME AUDIO P0.3\n"
        "Une banque SFX globale : ADPCM-A/B, FM, SSG et composites.\n"
        "Les DMR sont embarqués dans le sous-dossier musics/ de cet export.\n"
        "Le manifeste contient un profil de priorités indépendant pour chaque DMR.\n"
        "Une seule ressource AUDIO P0.3 suffit : le GDK découvre automatiquement les MUSIC embarquées.\n"
        "MUS_play(RES_...) sélectionne réellement la piste.\n",
        encoding="utf-8",
    )
    return manifest


def compile_audio_project(project: Path, out: Path, package_root: Path = ROOT) -> dict[str, Any]:
    """Compile completely in staging, then replace only Audio Lab-owned outputs."""
    project = project.resolve(); out = out.resolve(); out.parent.mkdir(parents=True, exist_ok=True)
    owned = ("audio_bank.bin", "audio_ids.h", "audio_manifest.json", "README_AUDIO_EXPORT.txt", "musics")
    stage = Path(tempfile.mkdtemp(prefix=".dms_audio_stage_", dir=str(out.parent)))
    backup = Path(tempfile.mkdtemp(prefix=".dms_audio_backup_", dir=str(out.parent)))
    try:
        report = _compile_audio_project_into(project, stage, package_root)
        out.mkdir(parents=True, exist_ok=True)
        moved: list[str] = []
        try:
            for name in owned:
                dest = out / name
                if dest.exists():
                    shutil.move(str(dest), str(backup / name))
                src = stage / name
                if src.exists():
                    shutil.move(str(src), str(dest))
                moved.append(name)
        except Exception:
            for name in reversed(moved):
                dest = out / name
                if dest.is_dir(): shutil.rmtree(dest, ignore_errors=True)
                elif dest.exists(): dest.unlink()
                old = backup / name
                if old.exists(): shutil.move(str(old), str(dest))
            raise
        return report
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compilation headless DMS Audio Lab P0.3 GAME AUDIO")
    parser.add_argument("project", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = compile_audio_project(args.project, args.out)
        print(f"PASS GAME AUDIO : {len(report['musics'])} musiques, {len(report['sfx'])} SFX, {report['bank_bytes']} octets ADPCM -> {args.out}")
        return 0
    except Exception as exc:
        print("ERREUR AUDIO :", exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
