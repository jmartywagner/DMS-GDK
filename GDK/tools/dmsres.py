#!/usr/bin/env python3
"""DMS Resource Compiler P1.1.

Consumes the formats produced by the current DMS tools without redoing their
art preparation: DRES V3, DIMG V2, DMAP V2, DCOLL V1, DACTOR V1, DMR and the
Audio Asset Builder / Audio Integration Lab exports.

The project manifest is a tiny text file. Example:

    SPRITE PLAYER res/player.dres PALETTE_BASE=2
    IMAGE TILESET res/stage_tiles.dimg
    MAP STAGE01 res/stage01.dmap TILESET=TILESET
    COLLISION STAGE01_COLL res/stage01.dcoll MAP=STAGE01
    ACTOR HERO res/hero.dactor SPRITE=PLAYER COLLISION=STAGE01_COLL
    MUSIC LEVEL1 res/level1.dmr
    AUDIO GAME_AUDIO res/audio

Output is an internal GDK compilation product (resources.bin + generated C
metadata); it is not a replacement for any source resource format.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
GDK_ROOT = HERE.parents[1]
PACKAGE_ROOT = HERE.parents[2]
RUNTIME_ROOT = PACKAGE_ROOT / "RUNTIME"
sys.path.insert(0, str(RUNTIME_ROOT / "tools"))
try:
    from dms_cartridge import inspect_dmr_bytes
except Exception:
    inspect_dmr_bytes = None

TYPE_CODES = {
    "SPRITE": 1,
    "IMAGE": 2,
    "MAP": 3,
    "COLLISION": 4,
    "ACTOR": 5,
    "MUSIC": 6,
    "AUDIO": 7,
    "FLOW": 8,
    "SCENE": 9,
}

MODE_ALIASES = {
    "Mode 0 - STANDARD": 0,
    "Mode 1 - HIGH COLOR": 1,
    "Mode 2 - SCROLL": 2,
    "Mode 3 - SPRITE": 3,
    "Mode 4 - LOW RES": 4,
    "STANDARD": 0,
    "HIGH COLOR": 1,
    "SCROLL": 2,
    "SPRITE": 3,
    "LOW RES": 4,
}
MODE_INFO = {
    0: {"name": "STANDARD", "width": 320, "height": 224, "palettes": 4, "bg_b": True, "sprites": 80, "scanline": 20},
    1: {"name": "HIGH COLOR", "width": 320, "height": 224, "palettes": 8, "bg_b": False, "sprites": 80, "scanline": 20},
    2: {"name": "SCROLL", "width": 320, "height": 224, "palettes": 4, "bg_b": True, "sprites": 48, "scanline": 12},
    3: {"name": "SPRITE", "width": 320, "height": 224, "palettes": 4, "bg_b": False, "sprites": 128, "scanline": 32},
    4: {"name": "LOW RES", "width": 256, "height": 224, "palettes": 8, "bg_b": True, "sprites": 96, "scanline": 24},
}

class DmsResError(RuntimeError):
    pass

@dataclass
class Diagnostic:
    severity: str
    message: str
    resource: str = ""

@dataclass
class Entry:
    kind: str
    name: str
    path: Path
    options: dict[str, str] = field(default_factory=dict)
    manifest: dict[str, Any] | None = None
    payloads: dict[str, bytes] = field(default_factory=dict)
    compiled: dict[str, Any] = field(default_factory=dict)

@dataclass
class CompileResult:
    entries: list[Entry]
    diagnostics: list[Diagnostic]
    output_dir: Path


def symbol(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]+", "_", text.upper()).strip("_")
    if not s:
        s = "RESOURCE"
    if s[0].isdigit():
        s = "R_" + s
    return s

def stable_flow_event_id(value: str) -> int:
    """Same stable 16-bit Flow event contract used by Scene/Flow compilers."""
    s = re.sub(r"[^A-Za-z0-9_]+", "_", str(value).upper()).strip("_") or "ITEM"
    if s[0].isdigit(): s = "N_" + s
    return (zlib.crc32(s.encode("utf-8")) & 0xFFFF) or 1


def parse_project(path: Path) -> list[Entry]:
    if not path.is_file():
        raise DmsResError(f"manifeste projet introuvable : {path}")
    entries: list[Entry] = []
    names: set[str] = set()
    for lineno, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        parts = re.findall(r'"[^"]*"|\S+', line)
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts]
        if len(parts) < 3:
            raise DmsResError(f"ligne {lineno}: KIND NOM CHEMIN requis")
        kind = parts[0].upper()
        if kind not in TYPE_CODES:
            raise DmsResError(f"ligne {lineno}: type inconnu {kind}")
        name = symbol(parts[1])
        if name in names:
            raise DmsResError(f"ligne {lineno}: nom de ressource dupliqué {name}")
        names.add(name)
        p = Path(parts[2])
        if not p.is_absolute():
            p = (path.parent / p).resolve()
        opts: dict[str, str] = {}
        for token in parts[3:]:
            if "=" not in token:
                raise DmsResError(f"ligne {lineno}: option invalide {token} (clé=valeur attendu)")
            k, v = token.split("=", 1)
            opts[k.upper()] = v
        entries.append(Entry(kind, name, p, opts))
    if not entries:
        raise DmsResError("manifeste projet vide")
    return entries


def _zip_load(entry: Entry, expected_format: str, versions: set[int], required: tuple[str, ...]) -> None:
    if not entry.path.is_file():
        raise DmsResError(f"{entry.name}: fichier absent : {entry.path}")
    if not zipfile.is_zipfile(entry.path):
        raise DmsResError(f"{entry.name}: {entry.path.name} n'est pas un conteneur ZIP DMS valide")
    with zipfile.ZipFile(entry.path) as z:
        names = set(z.namelist())
        missing = [n for n in required if n not in names]
        if missing:
            raise DmsResError(f"{entry.name}: fichiers internes absents : {', '.join(missing)}")
        try:
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        except Exception as exc:
            raise DmsResError(f"{entry.name}: manifest.json invalide : {exc}") from exc
        if manifest.get("format") != expected_format:
            raise DmsResError(f"{entry.name}: format {manifest.get('format')!r}, attendu {expected_format}")
        version = int(manifest.get("format_version", -1))
        if version not in versions:
            raise DmsResError(f"{entry.name}: version {version} non supportée pour {expected_format}")
        entry.manifest = manifest
        for n in required:
            if n != "manifest.json":
                entry.payloads[n] = z.read(n)
        # Keep optional files useful to validation/runtime.
        for n in ("priority_a.bin", "priority_b.bin", "events.json", "objects.json", "actions.json", "actor.json", "palette_ids.bin", "tilemap.bin", "palette_map.bin"):
            if n in names and n not in entry.payloads:
                entry.payloads[n] = z.read(n)


def _mode_number(value: Any) -> int | None:
    if isinstance(value, int) and value in MODE_INFO:
        return value
    if isinstance(value, str):
        if value in MODE_ALIASES:
            return MODE_ALIASES[value]
        u = value.upper().replace("_", " ").strip()
        for k, v in MODE_ALIASES.items():
            if k.upper().replace("_", " ") == u:
                return v
        m = re.search(r"MODE\s*(\d)", u)
        if m and int(m.group(1)) in MODE_INFO:
            return int(m.group(1))
    return None


def load_entry(entry: Entry) -> None:
    if entry.kind == "SPRITE":
        _zip_load(entry, "DRES", {3}, ("manifest.json", "tiles.bin", "palettes.bin"))
    elif entry.kind == "IMAGE":
        _zip_load(entry, "DIMG", {2}, ("manifest.json", "tiles.bin", "palettes.bin", "palette_ids.bin", "tilemap.bin"))
    elif entry.kind == "MAP":
        _zip_load(entry, "DMAP", {2}, ("manifest.json", "bg_a.bin", "bg_b.bin", "priority_a.bin", "priority_b.bin"))
    elif entry.kind == "COLLISION":
        _zip_load(entry, "DCOLL", {1}, ("manifest.json", "zones.bin", "vertices.bin", "actions.json"))
    elif entry.kind == "ACTOR":
        _zip_load(entry, "DACTOR", {1}, ("manifest.json", "actor.json"))
    elif entry.kind == "FLOW":
        if not entry.path.is_file():
            raise DmsResError(f"{entry.name}: DFLOW absent : {entry.path}")
        try:
            from dmsflowc import load_flow, validate_flow
            flow = load_flow(entry.path)
            diags = validate_flow(flow, entry.path)
            errors = [x for x in diags if x.severity == "ERROR"]
            if errors:
                raise DmsResError("; ".join((f"{x.item}: " if x.item else "") + x.message for x in errors))
            entry.manifest = flow
            entry.payloads["flow.json"] = entry.path.read_bytes()
            entry.compiled["flow_diagnostics"] = [x.as_dict() for x in diags]
        except DmsResError:
            raise
        except Exception as exc:
            raise DmsResError(f"{entry.name}: DFLOW invalide : {exc}") from exc
    elif entry.kind == "SCENE":
        if not entry.path.is_file():
            raise DmsResError(f"{entry.name}: DSCENE absente : {entry.path}")
        try:
            from dmsscenec import load_scene
            scene, warnings = load_scene(entry.path)
            entry.manifest = scene
            entry.payloads["scene.json"] = entry.path.read_bytes()
            entry.compiled["scene_warnings"] = warnings
        except Exception as exc:
            raise DmsResError(f"{entry.name}: DSCENE invalide : {exc}") from exc
    elif entry.kind == "MUSIC":
        if not entry.path.is_file():
            raise DmsResError(f"{entry.name}: DMR absent : {entry.path}")
        data = entry.path.read_bytes()
        if inspect_dmr_bytes is None:
            if len(data) < 64 or data[:4] != b"DMR0":
                raise DmsResError(f"{entry.name}: DMR0 invalide")
            ident = {"title": entry.path.stem, "author": ""}
        else:
            identity = inspect_dmr_bytes(data, entry.path.name)
            ident = {"title": identity.title, "author": identity.author}
        entry.payloads["music.dmr"] = data
        entry.manifest = {"format": "DMR", "format_version": 1, **ident, "bytes": len(data)}
    elif entry.kind == "AUDIO":
        p = entry.path
        if not p.is_dir():
            raise DmsResError(f"{entry.name}: AUDIO doit pointer vers un dossier d'export")
        mf = p / "audio_manifest.json"
        bank = p / "audio_bank.bin"
        if not mf.is_file() or not bank.is_file():
            raise DmsResError(f"{entry.name}: audio_manifest.json et audio_bank.bin requis")
        manifest = json.loads(mf.read_text(encoding="utf-8"))
        fmt = manifest.get("format")
        if fmt not in ("DMS1-AUDIO-ASSET-BUILDER-0.1", "DMS1-AUDIO-LAB-EXPORT-0.2", "DMS1-AUDIO-LAB-EXPORT-0.3"):
            raise DmsResError(f"{entry.name}: export audio non reconnu : {fmt}")
        entry.manifest = manifest
        entry.payloads["audio_bank.bin"] = bank.read_bytes()
        for n in ("audio_sample_ids.h", "audio_samples.h", "audio_samples.c", "audio_ids.h", "audio_rules.h", "audio_rules.c", "audio_fm_programs.c", "audio_composite_members.c"):
            q = p / n
            if q.is_file():
                entry.payloads[n] = q.read_bytes()


def _dres_animations(e: Entry) -> set[str]:
    if not e.manifest:
        return set()
    desc = e.manifest.get("animation_descriptors") or e.manifest.get("animations") or {}
    return {str(k).upper() for k in desc.keys()}


def _max_map_tile(e: Entry, layer: str) -> int:
    m = e.manifest or {}
    grid = (((m.get("layers") or {}).get(layer)) or [])
    mx = -1
    for row in grid:
        for c in row:
            try:
                tid = int(c.get("tile_id", c.get("tile", -1)))
            except Exception:
                tid = -1
            mx = max(mx, tid)
    return mx


def _map_nonempty_bg_b(e: Entry) -> bool:
    m = e.manifest or {}
    grid = (((m.get("layers") or {}).get("BG_B")) or [])
    for row in grid:
        for c in row:
            try:
                if int(c.get("tile_id", c.get("tile", -1))) >= 0:
                    return True
            except Exception:
                pass
    return False


def _image_tilemap_dims(e: Entry) -> tuple[int, int]:
    """Return screen-cell dimensions when a DIMG tilemap is a full mode image."""
    raw = e.payloads.get("tilemap.bin", b"")
    if not raw or len(raw) % 2:
        return (0, 0)
    m = e.manifest or {}
    mode = m.get("mode") or {}
    ts = int((m.get("tiles") or {}).get("tile_size", 8) or 8)
    try:
        w = int(mode.get("w", 0)) // ts
        h = int(mode.get("h", 0)) // ts
    except Exception:
        return (0, 0)
    return (w, h) if w > 0 and h > 0 and w * h == len(raw) // 2 else (0, 0)


def _final_image_word(interim: int) -> int:
    # DIMG V2: 0..9 tile, 10..12 palette, 13 flipX, 14 flipY.
    # Frozen VDP: 0..9 tile, 10..12 palette, 13 priority, 14 H, 15 V.
    tile = interim & 0x03FF
    pal = (interim >> 10) & 0x07
    fx = (interim >> 13) & 1
    fy = (interim >> 14) & 1
    return tile | (pal << 10) | (fx << 14) | (fy << 15)


def _resolve_ref(entries_by_name: dict[str, Entry], owner: Entry, option: str, expected: str, diagnostics: list[Diagnostic]) -> Entry | None:
    ref = owner.options.get(option)
    if not ref:
        return None
    key = symbol(ref)
    target = entries_by_name.get(key)
    if target is None:
        diagnostics.append(Diagnostic("ERROR", f"référence {option}={ref} introuvable", owner.name))
        return None
    if target.kind != expected:
        diagnostics.append(Diagnostic("ERROR", f"{option}={ref} est {target.kind}, attendu {expected}", owner.name))
        return None
    return target


def _expand_audio_music_entries(entries: list[Entry]) -> None:
    """Materialize embedded P0.3 DMR songs as normal MUSIC resources.

    This keeps the game manifest simple: one AUDIO folder can carry the whole
    cartridge soundtrack. Explicit MUSIC entries still win and are not duplicated.
    """
    names = {e.name for e in entries}
    for audio in [e for e in entries if e.kind == "AUDIO"]:
        manifest = audio.manifest or {}
        if manifest.get("format") != "DMS1-AUDIO-LAB-EXPORT-0.3":
            continue
        for row in manifest.get("musics", []):
            source_name = str(row.get("source_music", "")).casefold()
            source_stem = str(row.get("source_stem", "")).casefold()
            profile_symbol = symbol(str(row.get("symbol") or row.get("name") or "MUSIC"))
            present = False
            for existing in entries:
                if existing.kind != "MUSIC":
                    continue
                if (source_name and existing.path.name.casefold() == source_name) or (source_stem and existing.path.stem.casefold() == source_stem) or existing.name == profile_symbol:
                    present = True
                    break
            if present:
                continue
            rel = str(row.get("export_music", "")).strip()
            if not rel:
                raise DmsResError(f"{audio.name}: musique P0.3 non embarquée pour {row.get('name', profile_symbol)}")
            path = (audio.path / rel).resolve()
            name = profile_symbol
            if name in names:
                base = symbol(f"{audio.name}_{profile_symbol}")
                name = base
                suffix = 2
                while name in names:
                    name = f"{base}_{suffix}"; suffix += 1
            embedded = Entry("MUSIC", name, path, {"AUDIO_OWNER": audio.name, "AUDIO_MUSIC_ID": str(row.get("id", 0))})
            load_entry(embedded)
            entries.append(embedded)
            names.add(name)


def validate(entries: list[Entry]) -> list[Diagnostic]:
    d: list[Diagnostic] = []
    by_name = {e.name: e for e in entries}

    for e in entries:
        m = e.manifest or {}
        if e.kind == "SPRITE":
            if m.get("format_version") != 3:
                d.append(Diagnostic("ERROR", "seul DRES V3 est accepté", e.name))
            if int(m.get("bpp", 4)) != 4 or m.get("rgb_format") != "RGB333":
                d.append(Diagnostic("ERROR", "DRES doit rester RGB333 / 4 bpp", e.name))
            palette_base = int(e.options.get("PALETTE_BASE", "0") or 0)
            palette_count = len(e.payloads.get("palettes.bin", b"")) // 32
            if palette_base < 0 or palette_base > 7 or palette_base + palette_count > 8:
                d.append(Diagnostic("ERROR", f"PALETTE_BASE={palette_base} + {palette_count} palette(s) dépasse P0..P7", e.name))
            frames = m.get("frames") or []
            max_cells = max((len(f.get("cells") or []) for f in frames), default=0)
            if max_cells > 128:
                d.append(Diagnostic("ERROR", f"frame DRES demande {max_cells} cellules sprite > 128 entrées matérielles", e.name))
            issues = m.get("issues") or []
            for issue in issues:
                if str(issue.get("severity", "")).upper() == "ERROR":
                    d.append(Diagnostic("ERROR", "DRES contient une erreur Asset Lab : " + str(issue.get("message", "")), e.name))
        elif e.kind == "IMAGE":
            mode = _mode_number((m.get("mode") or {}).get("name"))
            if mode is not None:
                lim = MODE_INFO[mode]["palettes"]
                for pid in m.get("selected_palette_ids", []):
                    if int(pid) >= lim:
                        d.append(Diagnostic("ERROR", f"palette physique P{pid} interdite en Mode {mode}", e.name))
            if int((m.get("tiles") or {}).get("tile_size", 8)) != 8:
                d.append(Diagnostic("WARN", "runtime P1.1 charge les DIMG comme tilesets seulement en cellules 8x8", e.name))
        elif e.kind == "MAP":
            mapinfo = m.get("map") or {}
            mode = _mode_number(mapinfo.get("mode"))
            if mode is None:
                d.append(Diagnostic("ERROR", "mode DMAP non reconnu", e.name))
            else:
                if not MODE_INFO[mode]["bg_b"] and _map_nonempty_bg_b(e):
                    d.append(Diagnostic("ERROR", f"Mode {mode} interdit BG B", e.name))
            w = int(mapinfo.get("width_cells", 0)); h = int(mapinfo.get("height_cells", 0)); ts = int(mapinfo.get("tile_size", 0))
            if w <= 0 or h <= 0:
                d.append(Diagnostic("ERROR", "dimensions DMAP invalides", e.name))
            if w > 64 or h > 32:
                d.append(Diagnostic("WARN", f"DMAP monde {w}x{h} > ring VDP 64x32 : streaming natif libdms activé", e.name))
            if ts != 8:
                d.append(Diagnostic("WARN", f"tile_size={ts}: chargement runtime P1.1 final limité à 8x8", e.name))
            tileset = _resolve_ref(by_name, e, "TILESET", "IMAGE", d)
            if tileset:
                unique = len(tileset.payloads.get("tiles.bin", b"")) // 32
                mx = max(_max_map_tile(e, "BG_A"), _max_map_tile(e, "BG_B"))
                if mx >= unique:
                    d.append(Diagnostic("ERROR", f"DMAP référence tile {mx}, mais {tileset.name} n'en fournit que {unique}", e.name))
            else:
                d.append(Diagnostic("WARN", "DMAP V2 ne contient pas les pixels du tileset ; associer TILESET=<IMAGE DIMG> pour une ROM graphique autonome", e.name))

            bg_b_image = _resolve_ref(by_name, e, "BG_B", "IMAGE", d) if e.options.get("BG_B") else None
            if bg_b_image:
                iw, ih = _image_tilemap_dims(bg_b_image)
                if not MODE_INFO.get(mode or 0, {}).get("bg_b", False):
                    d.append(Diagnostic("ERROR", f"Mode {mode} interdit un DIMG sur BG B", e.name))
                if not iw or not ih:
                    d.append(Diagnostic("ERROR", f"BG_B={bg_b_image.name} n'est pas une DIMG plein écran exploitable", e.name))
                if tileset:
                    map_tiles = len(tileset.payloads.get("tiles.bin", b"")) // 32
                    bg_tiles = len(bg_b_image.payloads.get("tiles.bin", b"")) // 32
                    sprite_tiles = sum(len(x.payloads.get("tiles.bin", b"")) // 32 for x in entries if x.kind == "SPRITE")
                    total = 1 + map_tiles + bg_tiles + sprite_tiles
                    if total > 1024:
                        d.append(Diagnostic("ERROR", f"budget patterns VRAM dépassé : blank 1 + map {map_tiles} + BG B {bg_tiles} + sprites {sprite_tiles} = {total} > 1024", e.name))
                    else:
                        d.append(Diagnostic("INFO", f"budget patterns VRAM : {total}/1024 (map {map_tiles}, BG B {bg_tiles}, sprites {sprite_tiles})", e.name))
        elif e.kind == "COLLISION":
            map_e = _resolve_ref(by_name, e, "MAP", "MAP", d)
            if map_e:
                s = m.get("scene") or {}
                mi = (map_e.manifest or {}).get("map") or {}
                sw = int(s.get("largeur_px", s.get("width_px", 0)) or 0)
                sh = int(s.get("hauteur_px", s.get("height_px", 0)) or 0)
                mw = int(mi.get("pixel_width", 0) or 0); mh = int(mi.get("pixel_height", 0) or 0)
                if sw and mw and (sw, sh) != (mw, mh):
                    d.append(Diagnostic("ERROR", f"DCOLL {sw}x{sh}px != DMAP {mw}x{mh}px", e.name))
        elif e.kind == "ACTOR":
            actor = m.get("actor") or {}
            sprite_id_raw = str(e.options.get("SPRITE_ID", "")).strip()
            spr = None if sprite_id_raw else _resolve_ref(by_name, e, "SPRITE", "SPRITE", d)
            if sprite_id_raw:
                try:
                    sprite_id = int(sprite_id_raw, 0)
                    if not 0 <= sprite_id <= 0xFFFF:
                        raise ValueError
                except ValueError:
                    d.append(Diagnostic("ERROR", f"SPRITE_ID={sprite_id_raw!r} doit être un entier 0..65535", e.name))
            if spr:
                animations = _dres_animations(spr)
                for st in actor.get("etats", []):
                    anim = str(st.get("animation", "")).upper()
                    if anim and animations and anim not in animations:
                        d.append(Diagnostic("ERROR", f"état {st.get('nom')} référence animation {anim} absente du DRES {spr.name}", e.name))
            _resolve_ref(by_name, e, "COLLISION", "COLLISION", d)
            supported_conditions = {"TOUJOURS","ENTREE_JOUEUR","BOUTON_PRESSE","BOUTON_RELACHE","VITESSE_X","VITESSE_Y","AU_SOL","PAS_AU_SOL","TOUCHE_MUR","TOUCHE_PLAFOND","TEMPS_ETAT","ANIMATION_TERMINEE","JOUEUR_DISTANCE","JOUEUR_DISTANCE_X","JOUEUR_DISTANCE_Y","PV_INFERIEUR_OU_EGAL","PV_SUPERIEUR"}
            supported_actions = {"AUCUNE","SAUTER"}
            supported_ops = {"==","!=","<","<=",">",">="}
            supported_buttons = {"A","B","C","X","+","×","START"}
            states = {str(st.get("nom", "")).upper() for st in actor.get("etats", [])}
            initial = str(actor.get("etat_initial", "")).upper()
            if states and initial and initial not in states:
                d.append(Diagnostic("ERROR", f"état initial {initial} absent du DACTOR", e.name))
            for t in actor.get("transitions", []):
                if not t.get("active", True):
                    continue
                src = str(t.get("source", "")).upper(); dst = str(t.get("destination", "")).upper()
                cond = str(t.get("condition", "TOUJOURS")).upper()
                action = str(t.get("action", "AUCUNE")).upper()
                op = str(t.get("operateur", "=="))
                if src not in states or dst not in states:
                    d.append(Diagnostic("ERROR", f"transition {src}->{dst} référence un état absent", e.name))
                if cond not in supported_conditions:
                    d.append(Diagnostic("ERROR", f"condition DACTOR {cond} pas encore exécutée par libdms P0.4", e.name))
                if action not in supported_actions:
                    d.append(Diagnostic("ERROR", f"action DACTOR {action} pas encore exécutée par libdms P0.4", e.name))
                if op not in supported_ops:
                    d.append(Diagnostic("ERROR", f"opérateur DACTOR {op} non reconnu", e.name))
                if cond == "BOUTON_PRESSE" and str(t.get("parametre_a", "")).upper() not in supported_buttons:
                    d.append(Diagnostic("ERROR", f"bouton {t.get('parametre_a')} non pris en charge par le pad runtime actuel", e.name))
        elif e.kind == "AUDIO":
            bank = e.payloads.get("audio_bank.bin", b"")
            if len(bank) % 256:
                d.append(Diagnostic("ERROR", "audio_bank.bin n'est pas alignée sur 256 octets", e.name))
            fmt = m.get("format")
            if fmt == "DMS1-AUDIO-ASSET-BUILDER-0.1":
                for s in m.get("samples", []):
                    sp = int(s.get("start_page", 0)); ep = int(s.get("end_page", 0))
                    if ep < sp or (ep + 1) * 256 > len(bank):
                        d.append(Diagnostic("ERROR", f"sample {s.get('symbol', s.get('id'))}: pages hors banque", e.name))
            else:
                if int(m.get("bank_bytes", len(bank))) != len(bank):
                    d.append(Diagnostic("ERROR", "bank_bytes du manifeste Audio Lab ne correspond pas au fichier", e.name))
        elif e.kind == "SCENE":
            try:
                from dmsscenec import compile_runtime, validate_scene
                for severity, message in validate_scene(m, e.path):
                    d.append(Diagnostic(severity, message, e.name))
                catalog = [{"id": i, "kind": x.kind, "name": x.name, "path": str(x.path)} for i, x in enumerate(entries)]
                rt = compile_runtime(m, e.path, catalog, entries.index(e))
                e.compiled["scene_runtime"] = rt
                # Recalculate the conservative sprite budget from the actual
                # DRES frames; an editable scene cannot under-declare it.
                cells_total = 0
                manual_cells = 0
                patterns: set[int] = set()
                source_objects = [o for o in (m.get("objects") or []) if str(o.get("kind", "")).upper() != "BACKGROUND"]
                for object_index, obj in enumerate(rt.get("objects", [])):
                    rid = int(obj.get("resource_id", 0xFFFF))
                    if rid == 0xFFFF or rid >= len(entries):
                        source_obj = source_objects[object_index] if object_index < len(source_objects) else {}
                        if str(source_obj.get("kind", "")).upper() not in ("TEXT", "UI") or not str(source_obj.get("text", "")):
                            declared = max(1, int(source_obj.get("sprite_cells", 1) or 1))
                            cells_total += declared; manual_cells += declared
                        continue
                    target = entries[rid]
                    if target.kind == "ACTOR":
                        ref = symbol(target.options.get("SPRITE", "")) if target.options.get("SPRITE") else ""
                        pair = by_name.get(ref)
                        target = pair if pair and pair.kind == "SPRITE" else target
                    if target.kind == "SPRITE":
                        counts: dict[int, int] = {}
                        for cell in (target.manifest or {}).get("cells", []):
                            if cell.get("empty"):
                                continue
                            fi = int(cell.get("frame", -1)); counts[fi] = counts.get(fi, 0) + 1
                        cells_total += max(counts.values(), default=1)
                        patterns.add(entries.index(target))
                info = MODE_INFO.get(int(m.get("video_mode", 0)), MODE_INFO[0])
                if cells_total > info["sprites"]:
                    d.append(Diagnostic("ERROR", f"budget DRES réel {cells_total} cellules > {info['sprites']} en mode {m.get('video_mode')}", e.name))
                else:
                    qualifier = "réel + contrat ressources C" if manual_cells else "réel"
                    d.append(Diagnostic("INFO", f"budget DRES {qualifier} : {cells_total}/{info['sprites']} cellules", e.name))
                tile_total = sum(len(entries[r].payloads.get("tiles.bin", b"")) // 32 for r in patterns)
                if tile_total > 1023:
                    d.append(Diagnostic("ERROR", f"budget VRAM sprites {tile_total} patterns > 1023", e.name))
                else:
                    d.append(Diagnostic("INFO", f"budget VRAM scène (sprites uniques) : {tile_total}/1023 patterns", e.name))
                if manual_cells:
                    d.append(Diagnostic("WARN", f"{manual_cells} cellule(s) de ressources C historiques : vérifier aussi leur budget patterns dans le rapport projet", e.name))
            except Exception as exc:
                d.append(Diagnostic("ERROR", str(exc), e.name))

    # Filename-level warning for DACTOR's own legacy references; project refs are authoritative.
    audio_entries = [e for e in entries if e.kind == "AUDIO"]
    if len(audio_entries) > 1:
        d.append(Diagnostic("ERROR", "un seul export AUDIO est autorisé par cartouche (IDs SFX globaux)", "AUDIO"))
    music_entries = [e for e in entries if e.kind == "MUSIC"]
    for ae in audio_entries:
        if (ae.manifest or {}).get("format") == "DMS1-AUDIO-LAB-EXPORT-0.3" and not music_entries:
            d.append(Diagnostic("ERROR", "Audio Lab P0.3 requiert au moins une ressource MUSIC .dmr dans le projet", ae.name))
    for e in audio_entries:
        for sfx in (e.manifest or {}).get("sfx", []):
            program = sfx.get("program", [])
            if len(program) > 48:
                d.append(Diagnostic("ERROR", f"{sfx.get('name')}: programme synthèse {len(program)} écritures > 48", e.name))

    for e in entries:
        if e.kind != "ACTOR":
            continue
        actor = (e.manifest or {}).get("actor") or {}
        legacy = str(actor.get("ressource_dres", "")).strip()
        if legacy and "SPRITE" not in e.options and "SPRITE_ID" not in e.options:
            d.append(Diagnostic("WARN", f"DACTOR mentionne {legacy}, mais aucune option SPRITE= ou SPRITE_ID= n'est donnée au manifeste GDK", e.name))

    return d


def _final_bg_word(interim: int, priority_code: int) -> int:
    # DMAP V2 interim: 0..9 tile, 10..12 palette, 13 flipX, 14 flipY, 15 front.
    # Frozen VDP:       0..9 tile, 10..12 palette, 13 priority, 14 H, 15 V.
    tile = interim & 0x03FF
    pal = (interim >> 10) & 0x07
    fx = (interim >> 13) & 1
    fy = (interim >> 14) & 1
    front = 1 if int(priority_code) in (1, 3) else 0
    return tile | (pal << 10) | (front << 13) | (fx << 14) | (fy << 15)


def compile_map(entry: Entry) -> None:
    m = entry.manifest or {}
    info = m.get("map") or {}
    w = int(info.get("width_cells", 0)); h = int(info.get("height_cells", 0))
    n = w * h
    world = {}
    preview = {}
    for layer, key, pkey in (("BG_A", "bg_a.bin", "priority_a.bin"), ("BG_B", "bg_b.bin", "priority_b.bin")):
        raw = entry.payloads[key]; pr = entry.payloads[pkey]
        if len(raw) != n * 2 or len(pr) != n:
            raise DmsResError(f"{entry.name}: taille binaire {layer} incohérente")
        words = []
        grid = (((m.get("layers") or {}).get(layer)) or [])
        for i in range(n):
            y, x = divmod(i, w)
            empty = False
            try:
                cell = grid[y][x]
                empty = int(cell.get("tile_id", cell.get("tile", -1))) < 0
            except Exception:
                empty = False
            interim = struct.unpack_from(">H", raw, i * 2)[0]
            # DMS-GDK BUILD 12: 0xFFFF est aussi le sentinel binaire canonique
            # produit par Map Builder / sync_editable_maps. Il doit rester
            # 0xFFFF même si le manifest ne contient pas la grille JSON des
            # cellules. Sans ce test, _final_bg_word(0xFFFF, 0) devient 0xDFFF
            # et le runtime affiche alors la tile 1023 partout sur un plan vide.
            if empty or interim == 0xFFFF:
                words.append(0xFFFF)
            else:
                words.append(_final_bg_word(interim, pr[i]))
        # Keep the COMPLETE world in ROM. The 64x32 table is only the hardware ring.
        world[layer] = b"".join(struct.pack(">H", x) for x in words)
        # Compatibility preview for the old bootstrap builder: top-left 64x32 window only.
        table = [0] * (64 * 32)
        for y in range(min(h, 32)):
            for x in range(min(w, 64)):
                table[y * 64 + x] = words[y * w + x]
        preview[layer] = b"".join(struct.pack(">H", x) for x in table)
    entry.compiled["world_bg_a"] = world["BG_A"]
    entry.compiled["world_bg_b"] = world["BG_B"]
    entry.compiled["vdp_bg_a"] = preview["BG_A"]
    entry.compiled["vdp_bg_b"] = preview["BG_B"]
    entry.compiled["mode"] = _mode_number(info.get("mode"))
    entry.compiled["width"] = w
    entry.compiled["height"] = h


def compile_audio_runtime(entry: Entry) -> None:
    m = entry.manifest or {}
    fmt = m.get("format")
    sfx: list[dict[str, Any]] = []
    if fmt == "DMS1-AUDIO-ASSET-BUILDER-0.1":
        for i, s in enumerate(m.get("samples", [])):
            codec = str(s.get("codec", "A")).upper()
            sfx.append({
                "id": i,
                "name": symbol(str(s.get("symbol") or s.get("name") or f"SAMPLE_{i}")),
                "kind": "SAMPLE",
                "codec": codec,
                "target": "ADPCM-A" if codec == "A" else "ADPCM-B",
                "start_page": int(s.get("start_page", 0)),
                "end_page": int(s.get("end_page", 0)),
                "rate_hz": int(s.get("rate_hz", 18519 if codec == "A" else 26000)),
                "level": int(s.get("level", 0 if codec == "A" else 224)),
                "pan": int(s.get("pan", 0xC0)),
                "priority": 50,
                "conflict": "STEAL",
            })
    elif fmt in ("DMS1-AUDIO-LAB-EXPORT-0.2", "DMS1-AUDIO-LAB-EXPORT-0.3"):
        for s in m.get("sfx", []):
            p = s.get("params") or {}
            kind = str(s.get("kind", ""))
            target = str(s.get("target", ""))
            kind_code = {"FM": 3, "SSG": 4, "COMPOSITE": 5}.get(kind, 0)
            if kind == "SAMPLE": kind_code = 1 if target == "ADPCM-A" else 2
            target_code = 0
            if target.startswith("FM") or target.startswith("SSG"):
                try: target_code = int(target[-1])
                except Exception: target_code = 0
            rec = {
                "id": int(s.get("id", len(sfx))),
                "name": symbol(str(s.get("name", f"SFX_{len(sfx)}"))),
                "kind": kind,
                "target": target,
                "priority": int(s.get("priority", 50)),
                "conflict": str(s.get("conflict", "STEAL")),
                "duck_db": float(s.get("duck_db", 0)),
                "duck_steps": max(0, min(24, int(round(float(s.get("duck_db", 0)) / 0.75)))),
                "kind_code": kind_code,
                "target_code": target_code,
                "duration_frames": max(1, int(s.get("duration_frames", 1))),
                "program": [[int(q[0]) & 0xFFFF, int(q[1]) & 0xFF] for q in s.get("program", []) if isinstance(q, (list, tuple)) and len(q) >= 2],
                "members": [int(x) for x in p.get("members", [])],
                "p0": int(p.get("p0", 0)), "p1": int(p.get("p1", 0)),
                "p2": int(p.get("p2", 0)), "p3": int(p.get("p3", 0)), "p4": int(p.get("p4", 0)),
            }
            if kind == "SAMPLE":
                rec.update({
                    "codec": "A" if target == "ADPCM-A" else "B",
                    "start_page": rec["p0"], "end_page": rec["p1"],
                    "level": rec["p2"], "pan": rec["p3"], "rate_hz": rec["p4"],
                })
            sfx.append(rec)
    entry.compiled["sfx"] = sfx
    channels = ("FM1","FM2","FM3","FM4","SSG1","SSG2","SSG3","ADPCM-A","ADPCM-B")
    profiles = []
    for row in m.get("musics", []) if fmt == "DMS1-AUDIO-LAB-EXPORT-0.3" else []:
        pr = row.get("music_priorities") or {}
        profiles.append({
            "name": str(row.get("name", "")),
            "symbol": symbol(str(row.get("symbol") or row.get("name") or "MUSIC")),
            "source_music": str(row.get("source_music", "")),
            "source_stem": str(row.get("source_stem", "")),
            "priorities": [max(0, min(100, int(pr.get(ch, 50)))) for ch in channels],
        })
    entry.compiled["music_profiles"] = profiles
    priorities = m.get("music_priorities") or {}
    legacy = [max(0, min(100, int(priorities.get(ch, 50)))) for ch in channels]
    entry.compiled["music_priorities"] = profiles[0]["priorities"][:] if profiles else legacy


def compile_audio_bus(entries: list[Entry]) -> bytes:
    bus = bytearray()
    for entry in entries:
        if entry.kind != "MUSIC":
            continue
        if len(bus) % 256:
            bus += bytes(256 - len(bus) % 256)
        base = len(bus) // 256
        entry.compiled["music_page_base"] = base
        entry.compiled["music_bus_offset"] = len(bus)
        bus += entry.payloads.get("music.dmr", b"")
        if len(bus) % 256:
            bus += bytes(256 - len(bus) % 256)
    for entry in entries:
        if entry.kind != "AUDIO":
            continue
        base = len(bus) // 256
        entry.compiled["audio_page_base"] = base
        for sfx in entry.compiled.get("sfx", []):
            if sfx.get("kind") == "SAMPLE":
                sfx["start_page"] = int(sfx.get("start_page", 0)) + base
                sfx["end_page"] = int(sfx.get("end_page", 0)) + base
        bus += entry.payloads.get("audio_bank.bin", b"")
        if len(bus) % 256:
            bus += bytes(256 - len(bus) % 256)
    if len(bus) // 256 > 0xFFFF:
        raise DmsResError("bus DMR multi-musiques + SFX dépasse 65535 pages")
    return bytes(bus)


def compile_music_native_catalog(entries: list[Entry]) -> bytes:
    musics = [e for e in entries if e.kind == "MUSIC"]
    if not musics:
        return b""
    try:
        from dms_z80_native import BANK_BYTES, build_native_commands, pack_banked_stream
    except Exception as exc:
        raise DmsResError(f"driver audio natif indisponible : {exc}") from exc
    stream = bytearray()
    bank_cursor = 0
    for entry in musics:
        commands, _, halt_cycle = build_native_commands(
            entry.payloads.get("music.dmr", b""),
            int(entry.compiled.get("music_page_base", 0)),
        )
        song = pack_banked_stream(commands, base_bank=bank_cursor, pad_final=True)
        bank_count = max(1, len(song) // BANK_BYTES)
        if bank_cursor + bank_count > 65536:
            raise DmsResError("catalogue musical natif dépasse 65536 banques Z80")
        entry.compiled["music_start_bank"] = bank_cursor
        entry.compiled["music_bank_count"] = bank_count
        entry.compiled["music_halt_cycle"] = halt_cycle
        stream += song
        bank_cursor += bank_count
    return bytes(stream)


def _music_priorities_for_entry(entry: Entry, audio_entry: Entry | None) -> list[int]:
    default = [50] * 9
    if audio_entry is None:
        return default
    profiles = audio_entry.compiled.get("music_profiles", [])
    stem = entry.path.stem.casefold()
    fname = entry.path.name.casefold()
    ename = symbol(entry.name)
    for profile in profiles:
        source = str(profile.get("source_music", "")).replace("\\", "/")
        source_name = Path(source).name.casefold() if source else ""
        source_stem = str(profile.get("source_stem", "")).casefold()
        psym = symbol(str(profile.get("symbol") or profile.get("name") or ""))
        if (source_name and source_name == fname) or (source_stem and source_stem == stem) or (psym and psym in (ename, symbol(entry.path.stem))):
            return [max(0, min(100, int(x))) for x in profile.get("priorities", default)[:9]]
    return list(audio_entry.compiled.get("music_priorities", default))[:9]



def _q8(value: Any, default: float = 0.0) -> int:
    try:
        n = int(round(float(value) * 256.0))
    except Exception:
        n = int(round(default * 256.0))
    return max(-32768, min(32767, n))


def compile_sprite_runtime(entry: Entry) -> None:
    m = entry.manifest or {}
    frames = m.get("frames") or []
    cells_src = m.get("cells") or []
    frame_cells: list[list[dict[str, int]]] = [[] for _ in frames]
    for c in cells_src:
        try:
            fi = int(c.get("frame", -1))
            tile = c.get("tile")
            if fi < 0 or fi >= len(frames) or c.get("empty") or tile is None:
                continue
            flags = (1 if c.get("flip_x") else 0) | (2 if c.get("flip_y") else 0)
            frame_cells[fi].append({
                "x": int(c.get("x", 0)), "y": int(c.get("y", 0)),
                "tile": int(tile), "palette": int(c.get("palette", 0)), "flags": flags,
            })
        except Exception:
            continue
    flat_cells: list[dict[str, int]] = []
    frame_meta: list[dict[str, int]] = []
    max_cells = 0
    for fi, f in enumerate(frames):
        cs = frame_cells[fi]
        first = len(flat_cells); flat_cells.extend(cs); max_cells = max(max_cells, len(cs))
        pivot = f.get("pivot") or [int(f.get("width", 0)) // 2, int(f.get("height", 0)) - 1]
        hb = f.get("hitbox") or {}
        if hb.get("enabled") and int(hb.get("w", 0) or 0) > 0 and int(hb.get("h", 0) or 0) > 0:
            bx, by, bw, bh = int(hb.get("x", 0)), int(hb.get("y", 0)), int(hb.get("w", 0)), int(hb.get("h", 0))
        else:
            ab = f.get("active_bounds")
            if ab and len(ab) >= 4:
                bx, by, x1, y1 = map(int, ab[:4]); bw, bh = max(0, x1-bx), max(0, y1-by)
            else:
                bx = by = 0; bw = int(f.get("width", 0)); bh = int(f.get("height", 0))
        duration_ms = max(1, int(f.get("duration_ms", 100) or 100))
        ticks = max(1, int(round(duration_ms * 60.0 / 1000.0)))
        frame_meta.append({"first": first, "count": len(cs), "px": int(pivot[0]), "py": int(pivot[1]),
                           "bx": bx, "by": by, "bw": bw, "bh": bh, "ticks": ticks})
    desc = m.get("animation_descriptors") or {}
    if not desc:
        desc = {k: {"frames": v} for k, v in (m.get("animations") or {}).items()}
    animation_ids: dict[str, int] = {}
    anim_frames: list[int] = []
    anim_meta: list[dict[str, int]] = []
    for name, d in desc.items():
        animation_ids[str(name).upper()] = len(anim_meta)
        ids = [int(x) for x in (d.get("frames") or []) if 0 <= int(x) < len(frames)]
        first = len(anim_frames); anim_frames.extend(ids); anim_meta.append({"first": first, "count": len(ids)})
    pal_count = len(entry.payloads.get("palettes.bin", b"")) // 32
    entry.compiled["sprite_runtime"] = {
        "cells": flat_cells, "frames": frame_meta, "anim_frames": anim_frames, "animations": anim_meta,
        "animation_ids": animation_ids, "max_cells": max_cells, "palette_count": pal_count,
        "palette_base": int(entry.options.get("PALETTE_BASE", "0") or 0),
        "priority": 1 if str(entry.options.get("PRIORITY", "1")).upper() not in ("0", "FALSE", "NO") else 0,
    }


def compile_collision_runtime(entry: Entry) -> None:
    m = entry.manifest or {}
    zones = []
    for z in m.get("zones") or []:
        b = z.get("bounds") or [0, 0, 0, 0]
        if len(b) < 4: b = [0, 0, 0, 0]
        tname = str(z.get("type_zone", "PERSONNALISE")).upper()
        tmap = {"SOLIDE":0, "PLATEFORME_1_SENS":1, "DANGER":2, "DECLENCHEUR":6, "SORTIE":7, "CHECKPOINT":8}
        action = z.get("action") or {}
        flags = (1 if z.get("active", True) else 0) | (2 if action.get("active") else 0) | (4 if action.get("une_fois") else 0)
        zones.append({"id": int(z.get("id", 0)) & 0xFFFF, "type": tmap.get(tname, 255),
                      "mask": int(z.get("cible_mask", 0x1F)) & 0xFF, "flags": flags,
                      "x0": int(b[0]), "y0": int(b[1]), "x1": int(b[2]), "y1": int(b[3])})
    scene = m.get("scene") or {}
    entry.compiled["collision_runtime"] = {"zones": zones,
        "width": int(scene.get("largeur_px", scene.get("width_px", 0)) or 0),
        "height": int(scene.get("hauteur_px", scene.get("height_px", 0)) or 0)}


def compile_actor_runtime(entry: Entry, by_name: dict[str, tuple[int, Entry]]) -> None:
    a = (entry.manifest or {}).get("actor") or {}
    spr_name = symbol(entry.options.get("SPRITE", "")) if entry.options.get("SPRITE") else ""
    coll_name = symbol(entry.options.get("COLLISION", "")) if entry.options.get("COLLISION") else ""
    spr_pair = by_name.get(spr_name); coll_pair = by_name.get(coll_name)
    if entry.options.get("SPRITE_ID", ""):
        sprite_id = int(entry.options["SPRITE_ID"], 0) & 0xFFFF
    else:
        sprite_id = spr_pair[0] if spr_pair and spr_pair[1].kind == "SPRITE" else 0xFFFF
    coll_id = coll_pair[0] if coll_pair and coll_pair[1].kind == "COLLISION" else 0xFFFF
    anim_ids = (spr_pair[1].compiled.get("sprite_runtime", {}).get("animation_ids", {}) if spr_pair else {})
    sfx_ids: dict[str, int] = {}
    for _, candidate in by_name.values():
        if candidate.kind == "AUDIO":
            for sound in candidate.compiled.get("sfx", []):
                sfx_ids[symbol(str(sound.get("name", "")))] = int(sound.get("id", 0)) & 0xFFFF

    def _ticks16(value: Any) -> int:
        try:
            return max(0, min(0xFFFF, int(round(float(value) * 60.0 / 1000.0))))
        except Exception:
            return 0

    def _sfx(value: Any) -> int:
        raw = str(value or "").strip()
        if not raw:
            return 0xFFFF
        try:
            return int(raw, 0) & 0xFFFF
        except ValueError:
            return sfx_ids.get(symbol(raw.removeprefix("SFX_")), 0xFFFF)

    group_ids = {"JOUEUR":1,"ENNEMI":2,"BOSS":2,"PROJECTILE_JOUEUR":4,"PROJECTILE_ENNEMI":8,
                 "OBJET":16,"OBJET_INTERACTIF":16,"PNJ":16,"TOUS":0x1F,"ANY":0x1F}
    states_src = a.get("etats") or []
    state_ids = {str(st.get("nom", "")).upper(): i for i, st in enumerate(states_src)}
    states = []
    for st in states_src:
        anim_name = str(st.get("animation", "")).upper()
        an = anim_ids.get(anim_name, 0xFFFF)
        frame_key = "FRAME_" + symbol(anim_name)
        manual_frame = int(entry.options.get(frame_key, "65535"), 0) & 0xFFFF
        manual_count = max(0, min(0xFFFF, int(entry.options.get("FRAME_COUNT_" + symbol(anim_name), "0"), 0)))
        flags = (1 if st.get("invulnerable") else 0) | (2 if st.get("intangible") else 0) | (4 if st.get("verrou_direction") else 0)
        states.append({"anim": an, "first_frame": manual_frame, "frame_count": manual_count,
                       "duration": _ticks16(st.get("duree_ms", 0)), "sfx_enter": _sfx(st.get("sfx_entree")),
                       "sfx_exit": _sfx(st.get("sfx_sortie")),
                       "loop": 1 if st.get("boucle", True) else 0,
                       "gravity": 1 if st.get("gravite", True) else 0,
                       "world": 1 if st.get("collision_monde", True) else 0,
                       "ctl": 1 if st.get("controlable", True) else 0,
                       "flags": flags, "speed": _q8(st.get("multiplicateur_vitesse", 1.0), 1.0),
                       "anim_speed": _q8(st.get("vitesse_animation", 1.0), 1.0)})
    cond_map = {"TOUJOURS":0, "ENTREE_JOUEUR":1, "BOUTON_PRESSE":2, "VITESSE_Y":3,
                "AU_SOL":4, "PAS_AU_SOL":5, "TEMPS_ETAT":6, "ANIMATION_TERMINEE":7,
                "JOUEUR_DISTANCE":8, "VITESSE_X":9, "TOUCHE_MUR":10, "TOUCHE_PLAFOND":11,
                "PV_INFERIEUR_OU_EGAL":12, "PV_SUPERIEUR":12, "BOUTON_RELACHE":13,
                "JOUEUR_DISTANCE_X":14, "JOUEUR_DISTANCE_Y":15}
    op_map = {"==":0, "!=":1, "<":2, "<=":3, ">":4, ">=":5}
    btn_map = {"A":0x10, "B":0x20, "C":0x40, "X":0x80, "+":0x40, "×":0x80, "START":0x80}
    act_map = {"AUCUNE":0, "SAUTER":1}
    transitions = []
    for t in a.get("transitions") or []:
        if not t.get("active", True): continue
        src = state_ids.get(str(t.get("source", "")).upper()); dst = state_ids.get(str(t.get("destination", "")).upper())
        if src is None or dst is None: continue
        cond_name = str(t.get("condition", "TOUJOURS")).upper(); cond = cond_map.get(cond_name, 255)
        raw_b = t.get("parametre_b", "0")
        if cond_name == "TEMPS_ETAT":
            try: ticks = max(0, int(round(float(raw_b) * 60.0 / 1000.0)))
            except Exception: ticks = 0
            value = max(-32768, min(32767, ticks * 256))
        elif cond_name in ("BOUTON_PRESSE", "BOUTON_RELACHE", "AU_SOL", "PAS_AU_SOL", "TOUCHE_MUR", "TOUCHE_PLAFOND", "ANIMATION_TERMINEE", "ENTREE_JOUEUR"):
            try: value = _q8(float(raw_b), 0.0)
            except Exception: value = 0
        else: value = _q8(raw_b, 0.0)
        transitions.append({"src":src, "dst":dst, "cond":cond, "op":op_map.get(str(t.get("operateur", "==")),0),
                            "action":act_map.get(str(t.get("action", "AUCUNE")).upper(),0),
                            "button":btn_map.get(str(t.get("parametre_a", "")).upper(),0), "value":value,
                            "priority":max(0,min(255,int(t.get("priorite",100) or 100)))})
    attacks = []
    for attack in a.get("attaques") or []:
        state = state_ids.get(str(attack.get("etat", "")).upper())
        if state is None:
            continue
        attacks.append({"state": state, "kx": _q8(attack.get("recul_x", 0)), "ky": _q8(attack.get("recul_y", 0)),
                        "stun": _ticks16(attack.get("stun_ms", 0)), "cooldown": _ticks16(attack.get("cooldown_ms", 0)),
                        "damage": max(0, min(255, int(attack.get("degats", 0) or 0))),
                        "target": group_ids.get(str(attack.get("groupe_cible", "ENNEMI")).upper(), 2),
                        "flags": 1 if attack.get("perce_armure") else 0})

    def _actor_ref(value: Any) -> int:
        raw = str(value or "").strip()
        if not raw:
            return 0xFFFF
        wanted = (entry.path.parent / raw).resolve()
        for rid, candidate in by_name.values():
            if candidate.kind == "ACTOR" and (candidate.path.resolve() == wanted or candidate.name == symbol(Path(raw).stem)):
                return rid
        projectile_candidates = [(rid, candidate) for rid, candidate in by_name.values()
                                 if candidate.kind == "ACTOR" and str(((candidate.manifest or {}).get("actor") or {}).get("type", "")).upper() == "PROJECTILE"]
        if len(projectile_candidates) == 1:
            return projectile_candidates[0][0]
        return 0xFFFF

    projectile_state = next((state_ids[name] for name in ("ATTACK", "THROW", "FIRE", "DIVE", "LEAP") if name in state_ids), 0xFFFF)
    projectiles = []
    for projectile in a.get("projectiles") or []:
        state = state_ids.get(str(projectile.get("etat", "")).upper(), projectile_state)
        direction = str(projectile.get("direction", "")).upper()
        projectiles.append({"actor": _actor_ref(projectile.get("acteur")), "state": state,
                            "cadence": _ticks16(projectile.get("cadence_ms", 0)), "life": _ticks16(projectile.get("duree_vie_ms", 0)),
                            "speed": _q8(projectile.get("vitesse", 0)), "ox": max(-32768, min(32767, int(projectile.get("offset_x", 0) or 0))),
                            "oy": max(-32768, min(32767, int(projectile.get("offset_y", 0) or 0))),
                            "max": max(1, min(255, int(projectile.get("max_simultane", 1) or 1))),
                            "damage": max(0, min(255, int(projectile.get("degats", 0) or 0))),
                            "group": group_ids.get(str(projectile.get("groupe", "PROJECTILE_ENNEMI")).upper(), 8),
                            "flags": (1 if direction in ("VERS_JOUEUR", "TOWARD_PLAYER") else 0) | (2 if projectile.get("detruire_sur_collision") else 0)})

    mv = a.get("mouvement") or {}; ctrl = a.get("controle") or {}; combat = a.get("combat") or {}; ai = a.get("ia") or {}
    group = str(combat.get("groupe_collision", a.get("type", ""))).upper()
    target = group_ids.get(group, 1 if ctrl.get("joueur") else 2)
    initial = state_ids.get(str(a.get("etat_initial", "")).upper(), 0)
    jump_button = btn_map.get(str(ctrl.get("bouton_saut", "C")).upper(), 0x40)
    def _ms_ticks(value: Any) -> int:
        try: return max(0, min(255, int(round(float(value) * 60.0 / 1000.0))))
        except Exception: return 0
    ai_modes = {"AUCUNE":0,"NONE":0,"PATROUILLE":1,"PATROL":1,"POURSUITE":2,"CHASE":2,"SHMUP_LIGNE":3,
                "SHMUP":3,"BOSS_PHASES":4,"BOSS":4,"NPC":5,"PNJ":5,"RPG":6}
    ai_flags = (1 if ai.get("retourner_mur") else 0) | (2 if ai.get("retourner_vide") else 0) | (4 if ai.get("suivre_x", True) else 0) | (8 if ai.get("suivre_y") else 0)
    hurt_state = next((state_ids[name] for name in ("HURT", "DAMAGE", "TOUCHE") if name in state_ids), 0xFF)
    death_state = next((state_ids[name] for name in ("DEFEAT", "DEAD", "DEATH", "MORT") if name in state_ids), 0xFF)
    offscreen = (1 if mv.get("actif_hors_ecran") else 0) | (2 if mv.get("detruire_hors_ecran") else 0)
    entry.compiled["actor_runtime"] = {"sprite_id":sprite_id,"coll_id":coll_id,"states":states,"transitions":transitions,
        "attacks": attacks, "projectiles": projectiles, "initial":initial,
        "max_vx":_q8(mv.get("vitesse_max_x",2.0),2.0), "accel":_q8(mv.get("acceleration_x",0.25),0.25),
        "brake":_q8(mv.get("freinage_x",0.2),0.2), "gravity":_q8(mv.get("gravite",0.25),0.25),
        "max_fall":_q8(mv.get("vitesse_chute_max",mv.get("vitesse_max_y",6.0)),6.0), "jump":_q8(mv.get("vitesse_saut",-5.0),-5.0),
        "received_kx":_q8(combat.get("recul_recu_x", 0)), "received_ky":_q8(combat.get("recul_recu_y", 0)),
        "ai_speed":_q8(ai.get("vitesse_patrouille", 0)), "hp_max":max(1,min(0xFFFF,int(combat.get("pv_max",1) or 1))),
        "hp_start":max(0,min(0xFFFF,int(combat.get("pv_depart",combat.get("pv_max",1)) or 0))),
        "invinc":_ticks16(combat.get("invincibilite_ms",0)), "death_delay":_ticks16(combat.get("delai_destruction_ms",0)),
        "ai_detection":max(0,min(0xFFFF,int(ai.get("distance_detection",0) or 0))),
        "ai_loss":max(0,min(0xFFFF,int(ai.get("distance_perte",0) or 0))),
        "ai_distance":max(0,min(0xFFFF,int(ai.get("distance_patrouille",0) or 0))), "ai_decision":_ticks16(ai.get("delai_decision_ms",0)),
        "player":1 if ctrl.get("joueur") else 0, "target":target, "contact":max(0,min(255,int(combat.get("degats_contact",0) or 0))),
        "death_destroys":1 if combat.get("mort_detruit",True) else 0, "ai_mode":ai_modes.get(str(ai.get("mode","AUCUNE")).upper(),0),
        "ai_flags":ai_flags, "activation_margin":max(0,min(255,int(mv.get("marge_activation",32) or 0))), "offscreen":offscreen,
        "hurt_state":hurt_state, "death_state":death_state,
        "coyote":_ms_ticks(mv.get("coyote_ms",0)), "jump_buffer":_ms_ticks(mv.get("buffer_saut_ms",0)),
        "jump_button":jump_button, "max_jumps":max(1,min(255,int(mv.get("nombre_sauts",1) or 1))),
        "respawn":1 if mv.get("respawn", False) else 0}

def compile_entries(entries: list[Entry]) -> None:
    for e in entries:
        if e.kind == "MAP": compile_map(e)
        elif e.kind == "AUDIO": compile_audio_runtime(e)
        elif e.kind == "SPRITE": compile_sprite_runtime(e)
        elif e.kind == "COLLISION": compile_collision_runtime(e)
    by_name = {e.name: (i, e) for i, e in enumerate(entries)}
    for e in entries:
        if e.kind == "ACTOR": compile_actor_runtime(e, by_name)
    catalog = [{"id": i, "kind": x.kind, "name": x.name, "path": str(x.path)} for i, x in enumerate(entries)]
    for i, e in enumerate(entries):
        if e.kind == "SCENE":
            from dmsscenec import compile_runtime
            e.compiled["scene_runtime"] = compile_runtime(e.manifest or {}, e.path, catalog, i)
    audio_bus = compile_audio_bus(entries)
    audio_native = compile_music_native_catalog(entries)
    audio_entry = next((e for e in entries if e.kind == "AUDIO"), None)
    for e in entries:
        if e.kind == "MUSIC":
            e.compiled["music_priorities"] = _music_priorities_for_entry(e, audio_entry)
        if e.kind in ("AUDIO", "MUSIC"):
            e.compiled["audio_bus_bytes"] = len(audio_bus)
            e.compiled["audio_bus"] = audio_bus
            e.compiled["audio_native"] = audio_native

def _pack_resources(entries: list[Entry], source_base: Path) -> tuple[bytes, list[dict[str, Any]]]:
    """Internal DMSR1 bundle: original source containers + selected compiled payloads."""
    records: list[tuple[int, int, str, bytes]] = []
    meta: list[dict[str, Any]] = []
    for rid, e in enumerate(entries):
        payload = e.path.read_bytes() if e.path.is_file() else e.payloads.get("audio_bank.bin", b"")
        records.append((rid, TYPE_CODES[e.kind], e.name, payload))
        source = os.path.relpath(e.path, source_base).replace("\\", "/")
        meta.append({"id": rid, "kind": e.kind, "name": e.name, "source": source, "bytes": len(payload), "options": e.options})

    header = bytearray(b"DMSR1\0\0\0")
    header += struct.pack(">HHI", 1, len(records), 0)
    dir_size = len(records) * 28
    data_offset = len(header) + dir_size
    names = bytearray(); datas = bytearray(); directory = bytearray()
    # name offsets are absolute into a compact string section placed before data.
    for _, _, name, _ in records:
        names += name.encode("utf-8") + b"\0"
    payload_base = data_offset + len(names)
    name_cursor = 0
    data_cursor = 0
    for rid, t, name, payload in records:
        nb = name.encode("utf-8") + b"\0"
        directory += struct.pack(">HBBIIIIII", rid, t, 0, len(header)+dir_size+name_cursor, len(nb)-1, payload_base+data_cursor, len(payload), 0, 0)
        name_cursor += len(nb); datas += payload; data_cursor += len(payload)
    return bytes(header + directory + names + datas), meta


def _generated_headers(entries: list[Entry]) -> tuple[str, str]:
    h = [
        "#ifndef DMS_GENERATED_RESOURCES_H", "#define DMS_GENERATED_RESOURCES_H", "", "#include <stdint.h>", "",
        "typedef enum { DMS_RES_SPRITE=1, DMS_RES_IMAGE=2, DMS_RES_MAP=3, DMS_RES_COLLISION=4, DMS_RES_ACTOR=5, DMS_RES_MUSIC=6, DMS_RES_AUDIO=7, DMS_RES_FLOW=8, DMS_RES_SCENE=9 } DmsResourceType;",
        "typedef struct { uint16_t id; uint8_t type; const char* name; } DmsResourceInfo;", "",
    ]
    for i, e in enumerate(entries):
        h.append(f"#define RES_{e.name} {i}")
    h.append("")
    for e in entries:
        if e.kind == "MUSIC":
            h.append(f"#define MUSIC_{e.name} RES_{e.name}")
    h += ["", f"#define DMS_RESOURCE_COUNT {len(entries)}", "extern const DmsResourceInfo dms_resources[DMS_RESOURCE_COUNT];", ""]
    # Stable SFX macros from every AUDIO export.
    for e in entries:
        if e.kind != "AUDIO":
            continue
        for s in e.compiled.get("sfx", []):
            h.append(f"#define SFX_{symbol(s['name'])} {int(s['id'])}")
        if e.compiled.get("sfx"):
            h.append("")
    h += ["#endif", ""]

    c = ['#include "resources.h"', "", "const DmsResourceInfo dms_resources[DMS_RESOURCE_COUNT] = {"]
    for i, e in enumerate(entries):
        c.append(f'    {{{i}, {TYPE_CODES[e.kind]}, "{e.name}"}},')
    c += ["};", ""]
    return "\n".join(h), "\n".join(c)



def _c_u8_array(name: str, data: bytes, cols: int = 16) -> str:
    lines = [f"static const uint8_t {name}[{max(1, len(data))}] = {{"]
    if data:
        for i in range(0, len(data), cols):
            lines.append("    " + ", ".join(f"0x{x:02X}" for x in data[i:i+cols]) + ",")
    else:
        lines.append("    0,")
    lines.append("};")
    return "\n".join(lines)


def _be_words(data: bytes) -> list[int]:
    if len(data) & 1:
        raise DmsResError("flux uint16 big-endian impair")
    return list(struct.unpack(">" + "H" * (len(data) // 2), data)) if data else []


def _c_u16_array(name: str, values: list[int], cols: int = 10) -> str:
    lines = [f"static const uint16_t {name}[{max(1, len(values))}] = {{"]
    if values:
        for i in range(0, len(values), cols):
            lines.append("    " + ", ".join(f"0x{x & 0xFFFF:04X}" for x in values[i:i+cols]) + ",")
    else:
        lines.append("    0,")
    lines.append("};")
    return "\n".join(lines)


def _generated_runtime_c(entries: list[Entry]) -> str:
    """Generate ROM-resident DRES/DMAP/DCOLL/DACTOR descriptors for libdms."""
    by_name = {e.name: (i, e) for i, e in enumerate(entries)}
    out = ['#include <stdint.h>', '#include "dms_resource_runtime.h"', '']

    sprite_rows = []
    for rid, e in enumerate(entries):
        if e.kind != "SPRITE": continue
        rt = e.compiled.get("sprite_runtime", {}); base=f"dms_spr_{e.name.lower()}"
        tiles=e.payloads.get("tiles.bin",b""); pals=_be_words(e.payloads.get("palettes.bin",b""))
        out += [_c_u8_array(base+"_tiles",tiles),"",_c_u16_array(base+"_palettes",pals),""]
        cells=rt.get("cells",[])
        if cells:
            out.append(f"static const DmsSpriteCellDesc {base}_cells[] = {{")
            for c in cells: out.append(f"    {{{c['x']}, {c['y']}, {c['tile']}u, {c['palette']}u, {c['flags']}u}},")
            out += ["};",""]
        else: out += [f"static const DmsSpriteCellDesc {base}_cells[1] = {{{{0}}}};",""]
        frames=rt.get("frames",[])
        if frames:
            out.append(f"static const DmsSpriteFrameDesc {base}_frames[] = {{")
            for f in frames: out.append(f"    {{{f['first']}u,{f['count']}u,{f['px']},{f['py']},{f['bx']},{f['by']},{f['bw']},{f['bh']},{f['ticks']}u}},")
            out += ["};",""]
        else: out += [f"static const DmsSpriteFrameDesc {base}_frames[1] = {{{{0}}}};",""]
        af=rt.get("anim_frames",[]); out += [_c_u16_array(base+"_anim_frames",af),""]
        am=rt.get("animations",[])
        if am:
            out.append(f"static const DmsSpriteAnimationDesc {base}_anims[] = {{")
            for a in am: out.append(f"    {{{a['first']}u,{a['count']}u}},")
            out += ["};",""]
        else: out += [f"static const DmsSpriteAnimationDesc {base}_anims[1] = {{{{0}}}};",""]
        sprite_rows.append((rid,base,len(tiles)//32,len(cells),len(frames),len(am),rt.get("max_cells",0),rt.get("palette_count",0),rt.get("palette_base",0),rt.get("priority",1)))
    if sprite_rows:
        out.append('const DmsDresResourceDesc dms_dres_resources[] = {')
        for rid,base,tc,cc,fc,ac,mc,pc,pb,pr in sprite_rows:
            out.append(f'    {{{rid}u,{base}_tiles,{base}_palettes,{base}_cells,{base}_frames,{base}_anim_frames,{base}_anims,{tc}u,{cc}u,{fc}u,{ac}u,{mc}u,{pc}u,{pb}u,{pr}u}},')
        out += ['};',f'const uint16_t dms_dres_resource_count = {len(sprite_rows)}u;','']
    else: out += ['__attribute__((weak)) const DmsDresResourceDesc dms_dres_resources[1] = {{0}};','__attribute__((weak)) const uint16_t dms_dres_resource_count = 0u;','']

    image_rows=[]
    for rid,e in enumerate(entries):
        if e.kind!="IMAGE": continue
        base=f"dms_img_{e.name.lower()}";tiles=e.payloads.get("tiles.bin",b"");pals=_be_words(e.payloads.get("palettes.bin",b""));pids=e.payloads.get("palette_ids.bin",b"")
        raw_tm=e.payloads.get("tilemap.bin",b"")
        tm=[]
        if raw_tm and len(raw_tm)%2==0:
            for off in range(0,len(raw_tm),2): tm.append(_final_image_word(struct.unpack_from(">H",raw_tm,off)[0]))
        iw,ih=_image_tilemap_dims(e)
        out += [_c_u8_array(base+"_tiles",tiles),"",_c_u16_array(base+"_palettes",pals),"",_c_u8_array(base+"_palette_ids",pids),"",_c_u16_array(base+"_tilemap",tm),""]
        image_rows.append((rid,base,len(tiles)//32,len(pids),len(tm),iw,ih))
    if image_rows:
        out.append('const DmsImageResourceDesc dms_image_resources[] = {')
        for rid,base,tc,pc,tmc,iw,ih in image_rows: out.append(f'    {{{rid}u,{base}_tiles,{base}_palettes,{base}_palette_ids,{tc}u,{pc}u,{base}_tilemap,{tmc}u,{iw}u,{ih}u}},')
        out += ['};',f'const uint16_t dms_image_resource_count = {len(image_rows)}u;','']
    else: out += ['__attribute__((weak)) const DmsImageResourceDesc dms_image_resources[1] = {{0}};','__attribute__((weak)) const uint16_t dms_image_resource_count = 0u;','']

    map_rows=[]
    for rid,e in enumerate(entries):
        if e.kind!="MAP":continue
        base=f"dms_map_{e.name.lower()}";a=_be_words(e.compiled.get("world_bg_a",b""));b=_be_words(e.compiled.get("world_bg_b",b""))
        out += [_c_u16_array(base+"_a",a),"",_c_u16_array(base+"_b",b),""]
        ts_name=symbol(e.options.get("TILESET","")) if e.options.get("TILESET") else "";tsid=0xFFFF
        if ts_name in by_name and by_name[ts_name][1].kind=="IMAGE":tsid=by_name[ts_name][0]
        bg_name=symbol(e.options.get("BG_B","")) if e.options.get("BG_B") else "";bgid=0xFFFF
        if bg_name in by_name and by_name[bg_name][1].kind=="IMAGE":bgid=by_name[bg_name][0]
        map_rows.append((rid,base,int(e.compiled.get("width",0)),int(e.compiled.get("height",0)),int(e.compiled.get("mode",0)),tsid,bgid))
    if map_rows:
        out.append('const DmsMapResourceDesc dms_map_resources[] = {')
        for rid,base,w,h,mode,tsid,bgid in map_rows:out.append(f'    {{{rid}u,{base}_a,{base}_b,{w}u,{h}u,{mode}u,{tsid}u,{bgid}u}},')
        out += ['};',f'const uint16_t dms_map_resource_count = {len(map_rows)}u;','']
    else:out += ['__attribute__((weak)) const DmsMapResourceDesc dms_map_resources[1] = {{0}};','__attribute__((weak)) const uint16_t dms_map_resource_count = 0u;','']

    coll_rows=[]
    for rid,e in enumerate(entries):
        if e.kind!="COLLISION":continue
        base=f"dms_coll_{e.name.lower()}";rt=e.compiled.get("collision_runtime",{});zs=rt.get("zones",[])
        if zs:
            out.append(f"static const DmsCollisionZoneDesc {base}_zones[] = {{")
            for z in zs:out.append(f"    {{{z['id']}u,{z['type']}u,{z['mask']}u,{z['flags']}u,{z['x0']},{z['y0']},{z['x1']},{z['y1']}}},")
            out += ["};",""]
        else:out += [f"static const DmsCollisionZoneDesc {base}_zones[1] = {{{{0}}}};",""]
        coll_rows.append((rid,base,len(zs),rt.get("width",0),rt.get("height",0)))
    if coll_rows:
        out.append('const DmsCollisionResourceDesc dms_collision_resources[] = {')
        for rid,base,n,w,h in coll_rows:out.append(f'    {{{rid}u,{base}_zones,{n}u,{w}u,{h}u}},')
        out += ['};',f'const uint16_t dms_collision_resource_count = {len(coll_rows)}u;','']
    else:out += ['__attribute__((weak)) const DmsCollisionResourceDesc dms_collision_resources[1] = {{0}};','__attribute__((weak)) const uint16_t dms_collision_resource_count = 0u;','']

    actor_rows=[]
    for rid,e in enumerate(entries):
        if e.kind!="ACTOR":continue
        base=f"dms_actor_{e.name.lower()}";rt=e.compiled.get("actor_runtime",{});sts=rt.get("states",[]);trs=rt.get("transitions",[]);ats=rt.get("attacks",[]);prs=rt.get("projectiles",[])
        if sts:
            out.append(f"static const DmsActorStateDesc {base}_states[] = {{")
            for st in sts:out.append(f"    {{{st['anim']}u,{st['first_frame']}u,{st['frame_count']}u,{st['duration']}u,{st['sfx_enter']}u,{st['sfx_exit']}u,{st['loop']}u,{st['gravity']}u,{st['world']}u,{st['ctl']}u,{st['flags']}u,{st['speed']},{st['anim_speed']}}},")
            out += ["};",""]
        else:out += [f"static const DmsActorStateDesc {base}_states[1] = {{{{0}}}};",""]
        if trs:
            out.append(f"static const DmsActorTransitionDesc {base}_transitions[] = {{")
            for t in trs:out.append(f"    {{{t['src']}u,{t['dst']}u,{t['cond']}u,{t['op']}u,{t['action']}u,{t['button']}u,{t['value']},{t['priority']}u}},")
            out += ["};",""]
        else:out += [f"static const DmsActorTransitionDesc {base}_transitions[1] = {{{{0}}}};",""]
        if ats:
            out.append(f"static const DmsActorAttackDesc {base}_attacks[] = {{")
            for at in ats:out.append(f"    {{{at['state']}u,{at['kx']},{at['ky']},{at['stun']}u,{at['cooldown']}u,{at['damage']}u,{at['target']}u,{at['flags']}u}},")
            out += ["};",""]
        else:out += [f"static const DmsActorAttackDesc {base}_attacks[1] = {{{{0}}}};",""]
        if prs:
            out.append(f"static const DmsActorProjectileDesc {base}_projectiles[] = {{")
            for pr in prs:out.append(f"    {{{pr['actor']}u,{pr['state']}u,{pr['cadence']}u,{pr['life']}u,{pr['speed']},{pr['ox']},{pr['oy']},{pr['max']}u,{pr['damage']}u,{pr['group']}u,{pr['flags']}u}},")
            out += ["};",""]
        else:out += [f"static const DmsActorProjectileDesc {base}_projectiles[1] = {{{{0}}}};",""]
        actor_rows.append((rid,base,rt))
    if actor_rows:
        out.append('const DmsActorResourceDesc dms_actor_resources[] = {')
        for rid,base,rt in actor_rows:
            out.append(f"    {{{rid}u,{rt.get('sprite_id',0xFFFF)}u,{rt.get('coll_id',0xFFFF)}u,{base}_states,{base}_transitions,{base}_attacks,{base}_projectiles,{len(rt.get('states',[]))}u,{len(rt.get('transitions',[]))}u,{len(rt.get('attacks',[]))}u,{len(rt.get('projectiles',[]))}u,{rt.get('initial',0)}u,{rt.get('max_vx',0)},{rt.get('accel',0)},{rt.get('brake',0)},{rt.get('gravity',0)},{rt.get('max_fall',0)},{rt.get('jump',0)},{rt.get('received_kx',0)},{rt.get('received_ky',0)},{rt.get('ai_speed',0)},{rt.get('hp_max',1)}u,{rt.get('hp_start',1)}u,{rt.get('invinc',0)}u,{rt.get('death_delay',0)}u,{rt.get('ai_detection',0)}u,{rt.get('ai_loss',0)}u,{rt.get('ai_distance',0)}u,{rt.get('ai_decision',0)}u,{rt.get('player',0)}u,{rt.get('target',1)}u,{rt.get('contact',0)}u,{rt.get('death_destroys',1)}u,{rt.get('ai_mode',0)}u,{rt.get('ai_flags',0)}u,{rt.get('activation_margin',32)}u,{rt.get('offscreen',0)}u,{rt.get('hurt_state',255)}u,{rt.get('death_state',255)}u,{rt.get('coyote',0)}u,{rt.get('jump_buffer',0)}u,{rt.get('jump_button',0x40)}u,{rt.get('max_jumps',1)}u,{rt.get('respawn',0)}u}},")
        out += ['};',f'const uint16_t dms_actor_resource_count = {len(actor_rows)}u;','']
    else:out += ['__attribute__((weak)) const DmsActorResourceDesc dms_actor_resources[1] = {{0}};','__attribute__((weak)) const uint16_t dms_actor_resource_count = 0u;','']

    scene_rows=[]
    for rid,e in enumerate(entries):
        if e.kind!="SCENE":continue
        base=f"dms_scene_{e.name.lower()}";rt=e.compiled.get("scene_runtime",{});objs=rt.get("objects",[])
        if objs:
            out.append(f"static const DmsSceneObjectResourceDesc {base}_objects[] = {{")
            for o in objs:
                text=json.dumps(str(o.get('text','')),ensure_ascii=True)
                out.append("    {%du,%s,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%du,%du,%du,%du,%du,%du,%du,%du,%du,%du,%du,%du,%du,%du,%du,%du,%du}," % (
                    o['resource_id'],text,o['x'],o['y'],o['vx'],o['vy'],o['px'],o['py'],o['spawn_x'],o['spawn_y'],o['left'],o['right'],o['top'],o['bottom'],o['animation'],o['cadence'],o['start'],o['end'],o['start_trigger'],o['end_trigger'],o['kind'],o['layer'],o['priority'],o['palette'],o['palette_animation'],o['palette_span'],o['palette_cadence'],o['visible'],o['loop'],o['screen'],o['direction']))
            out += ["};",""]
        else:out += [f"static const DmsSceneObjectResourceDesc {base}_objects[1] = {{{{0}}}};",""]
        scene_rows.append((rid,base,rt))
    if scene_rows:
        out.append('const DmsSceneResourceDesc dms_scene_resources[] = {')
        for rid,base,rt in scene_rows:
            name=json.dumps(str(rt.get('name','SCENE')),ensure_ascii=True)
            out.append(f"    {{{rid}u,{name},{base}_objects,{len(rt.get('objects',[]))}u,{rt.get('map_resource_id',0xFFFF)}u,{rt.get('scroll_a_x',0)},{rt.get('scroll_a_y',0)},{rt.get('scroll_b_x',0)},{rt.get('scroll_b_y',0)},{rt.get('parallax_a_x',256)},{rt.get('parallax_a_y',256)},{rt.get('parallax_b_x',64)},{rt.get('parallax_b_y',32)},{rt.get('video_mode',0)}u,{rt.get('flags',0)}u}},")
        out += ['};',f'const uint16_t dms_scene_resource_count = {len(scene_rows)}u;','']
    else:out += ['__attribute__((weak)) const DmsSceneResourceDesc dms_scene_resources[1] = {{0}};','__attribute__((weak)) const uint16_t dms_scene_resource_count = 0u;','']

    music_rows=[]
    for rid,e in enumerate(entries):
        if e.kind != "MUSIC":
            continue
        pr=list(e.compiled.get("music_priorities",[50]*9))
        if len(pr) < 9:
            pr += [50] * (9-len(pr))
        music_rows.append((rid,int(e.compiled.get("music_start_bank",0)),int(e.compiled.get("music_bank_count",1)),pr[:9]))
    if music_rows:
        out.append('const DmsMusicResourceDesc dms_music_resources[] = {')
        for rid,start,count,pr in music_rows:
            vals=','.join(str(max(0,min(100,int(x))))+'u' for x in pr)
            out.append(f'    {{{rid}u,{start}u,{count}u,{{{vals}}}}},')
        out += ['};',f'const uint16_t dms_music_resource_count = {len(music_rows)}u;','']
    else:
        out += ['__attribute__((weak)) const DmsMusicResourceDesc dms_music_resources[1] = {{0}};','__attribute__((weak)) const uint16_t dms_music_resource_count = 0u;','']

    audio_entries=[e for e in entries if e.kind=="AUDIO"]
    sfx_rows=[];program_flat=[];composite_flat=[];music_priorities=list(music_rows[0][3]) if music_rows else [50]*9
    if audio_entries:
        ae=audio_entries[0]
        if not music_rows:
            music_priorities=list(ae.compiled.get("music_priorities",music_priorities))
        for s in ae.compiled.get("sfx",[]):
            kind=str(s.get("kind","SAMPLE"));target=str(s.get("target","ADPCM-A"));kind_code=int(s.get("kind_code",1 if kind=="SAMPLE" and target=="ADPCM-A" else 2 if kind=="SAMPLE" else 0))
            program=s.get("program",[]);pf=len(program_flat);program_flat.extend((int(q[0]),int(q[1])) for q in program);members=[int(x) for x in s.get("members",[])];cf=len(composite_flat);composite_flat.extend(members)
            conflict={"STEAL":0,"IGNORE_IF_BUSY":1,"IGNORE":1,"QUEUE":2,"FORCE":3}.get(str(s.get("conflict","STEAL")).upper(),0)
            duration=int(s.get("duration_frames",0))
            rate=int(s.get("rate_hz",s.get("p4",18519)) or 18519);start=int(s.get("start_page",0));end=int(s.get("end_page",0))
            if duration<=0 and kind=="SAMPLE":duration=max(1,int(round(max(1,end-start+1)*512*60/max(1,rate))))
            duration=max(1,duration)
            sfx_rows.append({"id":int(s.get("id",len(sfx_rows))),"duration":duration,"start":start,"end":end,"rate":rate,"pf":pf,"pc":len(program),"cf":cf,"cc":len(members),"kind":kind_code,"target":int(s.get("target_code",0)),"priority":int(s.get("priority",50)),"conflict":conflict,"duck":int(s.get("duck_steps",round(float(s.get("duck_db",0))/0.75))),"level":int(s.get("level",s.get("p2",0))),"pan":int(s.get("pan",s.get("p3",0xC0))),"flags":int(s.get("flags",0))})
    if program_flat:
        out.append('const DmsAudioRegWrite dms_sfx_program[] = {')
        for address,value in program_flat:out.append(f'    {{0x{address&0xFFFF:04X}u,0x{value&0xFF:02X}u}},')
        out += ['};',f'const uint16_t dms_sfx_program_count = {len(program_flat)}u;','']
    else:out += ['__attribute__((weak)) const DmsAudioRegWrite dms_sfx_program[1] = {{0}};','__attribute__((weak)) const uint16_t dms_sfx_program_count = 0u;','']
    if composite_flat:
        out.append('const uint16_t dms_sfx_composite_members[] = {')
        for value in composite_flat:out.append(f'    {value}u,')
        out += ['};',f'const uint16_t dms_sfx_composite_member_count = {len(composite_flat)}u;','']
    else:out += ['__attribute__((weak)) const uint16_t dms_sfx_composite_members[1] = {0};','__attribute__((weak)) const uint16_t dms_sfx_composite_member_count = 0u;','']
    if sfx_rows:
        out.append('const DmsSfxResourceDesc dms_sfx_resources[] = {')
        for s in sfx_rows:out.append(f"    {{{s['id']}u,{s['duration']}u,{s['start']}u,{s['end']}u,{s['rate']}u,{s['pf']}u,{s['pc']}u,{s['cf']}u,{s['cc']}u,{s['kind']}u,{s['target']}u,{s['priority']}u,{s['conflict']}u,{s['duck']}u,{s['level']}u,{s['pan']}u,{s['flags']}u}},")
        out += ['};',f'const uint16_t dms_sfx_resource_count = {len(sfx_rows)}u;','']
    else:out += ['__attribute__((weak)) const DmsSfxResourceDesc dms_sfx_resources[1] = {{0}};','__attribute__((weak)) const uint16_t dms_sfx_resource_count = 0u;','']
    out.append('const uint8_t dms_music_channel_priorities[9] = {'+','.join(str(max(0,min(100,int(x))))+'u' for x in music_priorities[:9])+'};')
    out.append('')
    return "\n".join(out)
def compile_project(project: Path, out_dir: Path) -> CompileResult:
    entries = parse_project(project)
    diagnostics: list[Diagnostic] = []
    for e in entries:
        try:
            load_entry(e)
        except Exception as exc:
            diagnostics.append(Diagnostic("ERROR", str(exc), e.name))
    if not any(x.severity == "ERROR" for x in diagnostics):
        try:
            _expand_audio_music_entries(entries)
        except Exception as exc:
            diagnostics.append(Diagnostic("ERROR", str(exc), "AUDIO"))
    if not any(x.severity == "ERROR" for x in diagnostics):
        diagnostics += validate(entries)
    if any(x.severity == "ERROR" for x in diagnostics):
        return CompileResult(entries, diagnostics, out_dir)

    compile_entries(entries)
    out_dir.mkdir(parents=True, exist_ok=True)
    for e in entries:
        if e.kind == "FLOW":
            from dmsflowc import compile_flow
            flow_doc = dict(e.manifest or {})
            flow_doc["resource_manifest"] = os.path.relpath(project, e.path.parent).replace("\\", "/")
            paths = compile_flow(flow_doc, out_dir, e.path, e.name.lower())
            e.compiled["flow_outputs"] = {k: str(v.name) for k, v in paths.items()}
    blob, meta = _pack_resources(entries, project.parent)
    (out_dir / "resources.bin").write_bytes(blob)
    h, c = _generated_headers(entries)
    (out_dir / "resources.h").write_text(h, encoding="utf-8")
    (out_dir / "resources.c").write_text(c, encoding="utf-8")
    (out_dir / "dms_runtime_resources.c").write_text(_generated_runtime_c(entries), encoding="utf-8")
    audio_entry = next((e for e in entries if e.kind == "AUDIO"), None)
    carrier = next((e for e in entries if e.kind == "MUSIC"), audio_entry)
    if carrier is not None:
        audio_bus = carrier.compiled.get("audio_bus", b"")
        audio_native = carrier.compiled.get("audio_native", b"")
        if audio_bus:
            (out_dir / "audio_bus.dmr").write_bytes(audio_bus)
        if audio_native:
            (out_dir / "audio_native.ndrv").write_bytes(audio_native)

    runtime = {
        "format": "DMS-GDK-RESOURCE-MANIFEST-1.1",
        "project": os.path.relpath(project, out_dir).replace("\\", "/"),
        "resources": meta,
        "compiled": {},
    }
    for e in entries:
        item: dict[str, Any] = {"kind": e.kind, "options": e.options, "manifest": e.manifest}
        if e.kind == "MAP":
            item["mode"] = e.compiled.get("mode")
            item["width"] = e.compiled.get("width"); item["height"] = e.compiled.get("height")
            (out_dir / f"{e.name.lower()}_bg_a.vdp.bin").write_bytes(e.compiled["vdp_bg_a"])
            (out_dir / f"{e.name.lower()}_bg_b.vdp.bin").write_bytes(e.compiled["vdp_bg_b"])
            (out_dir / f"{e.name.lower()}_bg_a.world.bin").write_bytes(e.compiled["world_bg_a"])
            (out_dir / f"{e.name.lower()}_bg_b.world.bin").write_bytes(e.compiled["world_bg_b"])
            item["compiled_bg_a"] = f"{e.name.lower()}_bg_a.world.bin"
            item["compiled_bg_b"] = f"{e.name.lower()}_bg_b.world.bin"
            item["ring_preview_bg_a"] = f"{e.name.lower()}_bg_a.vdp.bin"
            item["ring_preview_bg_b"] = f"{e.name.lower()}_bg_b.vdp.bin"
        if e.kind == "AUDIO":
            item["sfx_runtime"] = e.compiled.get("sfx", [])
            (out_dir / f"{e.name.lower()}_audio_bank.bin").write_bytes(e.payloads["audio_bank.bin"])
            item["compiled_bank"] = f"{e.name.lower()}_audio_bank.bin"
        if e.kind == "FLOW":
            item["flow_outputs"] = e.compiled.get("flow_outputs", {})
            item["flow_diagnostics"] = e.compiled.get("flow_diagnostics", [])
        if e.kind == "SCENE":
            item["scene_runtime"] = e.compiled.get("scene_runtime", {})
            item["scene_warnings"] = e.compiled.get("scene_warnings", [])
        runtime["compiled"][e.name] = item
    (out_dir / "resources_manifest.json").write_text(json.dumps(runtime, indent=2, ensure_ascii=False), encoding="utf-8")

    report_project = os.path.relpath(project, out_dir).replace("\\", "/")
    report_lines = ["DMS RESOURCE COMPILER P1.1", "=========================", "", f"Projet : {report_project}", f"Ressources : {len(entries)}", ""]
    for i, e in enumerate(entries):
        report_lines.append(f"[{i:02d}] {e.kind:9s} {e.name:24s} {e.path.name}")
    report_lines += ["", "DIAGNOSTICS", "-----------"]
    if diagnostics:
        report_lines += [f"[{x.severity}] {x.resource}: {x.message}" for x in diagnostics]
    else:
        report_lines.append("PASS - aucun problème détecté.")
    (out_dir / "DMSRES_REPORT.txt").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return CompileResult(entries, diagnostics, out_dir)


def print_diagnostics(result: CompileResult) -> None:
    if not result.diagnostics:
        print("DMSRES: PASS")
        return
    for x in result.diagnostics:
        prefix = "ERREUR" if x.severity == "ERROR" else ("INFO" if x.severity == "INFO" else "AVERTISSEMENT")
        where = f" [{x.resource}]" if x.resource else ""
        print(f"{prefix}{where}: {x.message}")


def main() -> int:
    ap = argparse.ArgumentParser(description="DMS-1 resource compiler")
    ap.add_argument("project", type=Path, help="resources.dmsres")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--validate-only", action="store_true")
    args = ap.parse_args()
    out = args.out or (args.project.parent / "build" / "generated")
    try:
        result = compile_project(args.project.resolve(), out.resolve())
        print_diagnostics(result)
        if any(x.severity == "ERROR" for x in result.diagnostics):
            return 2
        if args.validate_only:
            return 0
        print(f"DMSRES: {len(result.entries)} ressources -> {out}")
        return 0
    except Exception as exc:
        print(f"ERREUR DMSRES: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
