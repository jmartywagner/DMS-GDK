#!/usr/bin/env python3
"""Détection et préparation automatique des sources éditables DMS-GDK."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
from dms_game_settings import compile_document as compile_settings  # noqa: E402
from dms_game_settings import default_document, save_document  # noqa: E402
from dmsres import parse_project, symbol  # noqa: E402


class AutoBuildError(RuntimeError):
    pass


@dataclass
class AutoEntry:
    kind: str
    name: str
    path: Path
    options: dict[str, str] = field(default_factory=dict)
    origin: str = "auto"


def _project_config(project: Path) -> dict[str, Any]:
    path = project / "dms_project.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    auto = data.get("autobuild", {})
    return auto if isinstance(auto, dict) else {}


def _discover(project: Path, suffix: str) -> list[Path]:
    ignored = {"build", "archive", "archives", "backup", "backups", ".git"}
    out = []
    for path in project.rglob("*" + suffix):
        try:
            rel = path.relative_to(project)
        except ValueError:
            continue
        if any(part.lower() in ignored for part in rel.parts):
            continue
        if path.is_file():
            out.append(path.resolve())
    return sorted(out, key=lambda p: str(p).lower())


def _read_zip_json(path: Path, member: str) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            return json.loads(archive.read(member).decode("utf-8"))
    except Exception:
        return {}


def _read_numeric_defines(path: Path) -> dict[str, int]:
    """Read the simple numeric resource/frame contract emitted by GDK tools."""
    if not path.is_file():
        return {}
    values: dict[str, int] = {}
    pattern = re.compile(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+((?:0[xX][0-9A-Fa-f]+)|(?:\d+))[uUlL]*\s*(?:/\*.*\*/)?$")
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        match = pattern.match(line)
        if match:
            values[match.group(1).upper()] = int(match.group(2), 0)
    return values


def _manual_sprite_options(actor: dict[str, Any], header: Path, actor_path: Path) -> dict[str, str]:
    """Bridge an editable DACTOR to a hand-generated DRES C resource table."""
    defines = _read_numeric_defines(header)
    prefixes = [name[4:] for name in defines if name.startswith("RES_")]
    animations = {str(st.get("animation", "")).upper() for st in actor.get("etats", []) if str(st.get("animation", "")).strip()}

    def score(prefix: str) -> int:
        return sum(1 for anim in animations if f"{prefix}_FRAME_{anim}" in defines or f"{prefix}_FRAME_{anim}_0" in defines)

    hinted = symbol(actor_path.stem)
    actor_type = symbol(str(actor.get("type", "")))
    if actor_type == "PROJECTILE" and "PROJECTILE" in prefixes:
        hinted = "PROJECTILE"
    ranked = sorted(prefixes, key=lambda p: (score(p), p == hinted, p in hinted or hinted in p), reverse=True)
    if not ranked or f"RES_{ranked[0]}" not in defines:
        return {}
    prefix = ranked[0]
    options = {"SPRITE_ID": str(defines[f"RES_{prefix}"])}
    for anim in sorted(animations):
        aliases = [anim]
        if "ATTACK" in anim:
            aliases.append(anim.replace("ATTACK", "FIRE"))
        macro = next((f"{prefix}_FRAME_{alias}" for alias in aliases if f"{prefix}_FRAME_{alias}" in defines), "")
        if not macro:
            macro = next((f"{prefix}_FRAME_{alias}_0" for alias in aliases if f"{prefix}_FRAME_{alias}_0" in defines), "")
        if macro not in defines:
            continue
        first = defines[macro]
        count = 1
        zero_match = re.match(r"^(.*)_0$", macro)
        if zero_match:
            count = 0
            while f"{zero_match.group(1)}_{count}" in defines:
                count += 1
            count = max(1, count)
        key = symbol(anim)
        options[f"FRAME_{key}"] = str(first)
        options[f"FRAME_COUNT_{key}"] = str(count)
    return options


def _safe_name(path: Path, fallback: str) -> str:
    return symbol(path.stem or fallback)


def _unique_name(base: str, used: set[str]) -> str:
    name = symbol(base); candidate = name; index = 2
    while candidate in used:
        candidate = f"{name}_{index}"; index += 1
    used.add(candidate)
    return candidate


def _selected_file(project: Path, config: dict[str, Any], key: str, suffix: str, warnings: list[str]) -> Path | None:
    explicit = str(config.get(key, "")).strip()
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = project / path
        if not path.is_file():
            raise AutoBuildError(f"autobuild.{key} introuvable : {path}")
        return path.resolve()
    files = _discover(project, suffix)
    if len(files) > 1:
        raise AutoBuildError(f"plusieurs {suffix} détectés; préciser autobuild.{key} dans dms_project.json : " + ", ".join(str(x.relative_to(project)) for x in files))
    if files:
        return files[0]
    return None


def _relative_token(path: Path, manifest_dir: Path) -> str:
    value = os.path.relpath(path, manifest_dir).replace("\\", "/")
    return f'"{value}"' if any(ch.isspace() for ch in value) else value


def _project_relative(path: Path, project: Path) -> str:
    """Portable display path for generated reports (never an absolute host path)."""
    return os.path.relpath(path, project).replace("\\", "/")


def _write_manifest(path: Path, entries: list[AutoEntry]) -> None:
    lines = ["# Généré par dmsautobuild.py - sources originales inchangées."]
    for entry in entries:
        opts = "".join(f" {key}={value}" for key, value in sorted(entry.options.items()))
        lines.append(f"{entry.kind} {entry.name} {_relative_token(entry.path, path.parent)}{opts}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _catalog_original(manifest: Path | None) -> list[AutoEntry]:
    if manifest is None or not manifest.is_file():
        return []
    return [AutoEntry(e.kind, e.name, e.path.resolve(), dict(e.options), "manifest") for e in parse_project(manifest)]


def prepare_project(project: Path, validate_only: bool = False) -> dict[str, Any]:
    project = project.resolve()
    if not (project / "src" / "main.c").is_file():
        raise AutoBuildError(f"projet invalide, src/main.c absent : {project}")
    config = _project_config(project)
    if config.get("enabled", True) is False:
        return {"enabled": False, "resource_manifest": project / "resources.dmsres" if (project / "resources.dmsres").is_file() else None, "generated_sources": []}
    out = project / "build" / "autogen"
    out.mkdir(parents=True, exist_ok=True)
    messages: list[str] = []
    warnings: list[str] = []
    generated_sources: list[Path] = []

    settings_path = project / "dms_game_settings.json"
    if settings_path.is_file():
        report = compile_settings(settings_path, out)
        generated_sources.append(out / "dms_game_settings_generated.c")
        messages.append(f"RÉGLAGES : {len(report['values'])} valeurs compilées")
    elif config.get("create_default_settings", False):
        save_document(settings_path, default_document())
        compile_settings(settings_path, out)
        generated_sources.append(out / "dms_game_settings_generated.c")
        messages.append("RÉGLAGES : fichier par défaut créé et compilé")
    else:
        messages.append("RÉGLAGES : aucun dms_game_settings.json (valeurs libdms de secours)")

    manifest_cfg = str(config.get("resources", "resources.dmsres"))
    original_manifest = Path(manifest_cfg)
    if not original_manifest.is_absolute():
        original_manifest = project / original_manifest
    if not original_manifest.is_file():
        original_manifest = None
    entries = _catalog_original(original_manifest)
    used = {entry.name for entry in entries}
    covered = {(entry.kind, entry.path.resolve()) for entry in entries}

    def add(kind: str, base: str, path: Path, options: dict[str, str] | None = None) -> AutoEntry | None:
        key = (kind, path.resolve())
        if key in covered:
            return None
        entry = AutoEntry(kind, _unique_name(base, used), path.resolve(), dict(options or {}))
        entries.append(entry); covered.add(key)
        return entry

    scan_assets = config.get("scan_assets", True) is not False
    if scan_assets:
        dres_entries: dict[Path, AutoEntry] = {}
        for path in _discover(project, ".dres"):
            item = add("SPRITE", _safe_name(path, "SPRITE"), path)
            if item: dres_entries[path] = item
        image_entries: dict[Path, AutoEntry] = {}
        for path in _discover(project, ".dimg"):
            item = add("IMAGE", _safe_name(path, "IMAGE"), path)
            if item: image_entries[path] = item
            else:
                image_entries[path] = next((e for e in entries if e.kind == "IMAGE" and e.path == path), None)  # type: ignore[assignment]
        map_entries: dict[Path, AutoEntry] = {}
        for path in _discover(project, ".dmap"):
            manifest = _read_zip_json(path, "manifest.json")
            source = str((manifest.get("tileset") or {}).get("source", "")).strip()
            tileset_path = (path.parent / source).resolve() if source else None
            image = image_entries.get(tileset_path) if tileset_path else None
            if image is None:
                image = next((e for e in entries if e.kind == "IMAGE" and tileset_path and e.path == tileset_path), None)
            if image is None:
                warnings.append(f"MAP ignorée (tileset DIMG non résolu) : {path.relative_to(project)}")
                continue
            item = add("MAP", _safe_name(path, "MAP"), path, {"TILESET": image.name})
            if item: map_entries[path] = item
            else:
                found = next((e for e in entries if e.kind == "MAP" and e.path == path), None)
                if found: map_entries[path] = found
        for path in _discover(project, ".dcoll"):
            map_path = path.with_suffix(".dmap").resolve(); map_entry = map_entries.get(map_path) or next((e for e in entries if e.kind == "MAP" and e.path == map_path), None)
            opts = {"MAP": map_entry.name} if map_entry else {}
            if not map_entry: warnings.append(f"COLLISION sans DMAP associé : {path.relative_to(project)}")
            add("COLLISION", _safe_name(path, "COLLISION") + "_COLL", path, opts)
        for path in _discover(project, ".dactor"):
            if ("ACTOR", path.resolve()) in covered:
                continue
            actor_json = _read_zip_json(path, "actor.json")
            actor = actor_json.get("actor") or actor_json
            sprite_ref = str(actor.get("ressource_dres", "")).strip(); coll_ref = str(actor.get("ressource_dcoll", "")).strip()
            sprite_path = (path.parent / sprite_ref).resolve() if sprite_ref.lower().endswith(".dres") else None
            sprite_header = (path.parent / sprite_ref).resolve() if sprite_ref.lower().endswith(".h") else None
            coll_path = (path.parent / coll_ref).resolve() if coll_ref.lower().endswith(".dcoll") else None
            sprite = next((e for e in entries if e.kind == "SPRITE" and sprite_path and e.path == sprite_path), None)
            collision = next((e for e in entries if e.kind == "COLLISION" and coll_path and e.path == coll_path), None)
            opts = {"SPRITE": sprite.name} if sprite else (_manual_sprite_options(actor, sprite_header, path) if sprite_header else {})
            if not opts:
                warnings.append(f"ACTOR détecté mais non ajouté (référence DRES non résolue) : {path.relative_to(project)}")
                continue
            if collision: opts["COLLISION"] = collision.name
            add("ACTOR", _safe_name(path, "ACTOR"), path, opts)

    for path in _discover(project, ".dmr"):
        add("MUSIC", _safe_name(path, "MUSIC"), path)

    flow = _selected_file(project, config, "flow", ".dflow", warnings)
    if flow:
        add("FLOW", _safe_name(flow, "GAME_FLOW"), flow)

    for path in _discover(project, ".dscene"):
        add("SCENE", _safe_name(path, "SCENE"), path)

    audio_source = _selected_file(project, config, "audio", ".dmsaudio.json", warnings)
    if audio_source:
        try:
            from dmsaudio_compile import compile_audio_project
            audio_dir = out / "audio"
            compile_audio_project(audio_source, audio_dir, ROOT)
            add("AUDIO", _safe_name(audio_source, "GAME_AUDIO"), audio_dir)
            messages.append(f"AUDIO : {audio_source.relative_to(project)} compilé")
        except ImportError:
            warnings.append("compilateur headless audio absent; projet audio détecté mais non compilé")

    merged = out / "resources.merged.dmsres"
    resource_manifest: Path | None = None
    if entries:
        _write_manifest(merged, entries)
        resource_manifest = merged
        auto_count = sum(1 for entry in entries if entry.origin == "auto")
        messages.append(f"RESSOURCES : {len(entries)} entrées ({auto_count} ajout(s) automatiques)")
    elif original_manifest:
        resource_manifest = original_manifest

    report = {
        "format": "DMS-AUTOBUILD-REPORT-1",
        "project": ".",
        "validate_only": validate_only,
        "resource_manifest": _project_relative(resource_manifest, project) if resource_manifest else None,
        "generated_sources": [_project_relative(path, project) for path in generated_sources],
        "entries": [{"kind": e.kind, "name": e.name, "source": _project_relative(e.path, project), "origin": e.origin, "options": e.options} for e in entries],
        "messages": messages,
        "warnings": warnings,
    }
    (out / "dms_autobuild_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for message in messages: print("[AUTO]", message)
    for warning in warnings: print("[AUTO] AVERTISSEMENT :", warning)
    return {**report, "resource_manifest": resource_manifest, "generated_sources": generated_sources, "output_dir": out}


def main() -> int:
    parser = argparse.ArgumentParser(description="Préparation automatique d’un projet DMS-GDK")
    parser.add_argument("project", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        prepare_project(args.project, args.validate_only)
        print("PASS AUTOBUILD : détection et préparation terminées")
        return 0
    except Exception as exc:
        print("ERREUR AUTOBUILD :", exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
