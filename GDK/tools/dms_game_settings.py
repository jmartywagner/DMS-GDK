#!/usr/bin/env python3
"""Réglages centraux DMS-GDK et compilation C déterministe.

Le JSON reste la source éditable. Le C/H dans ``build/autogen`` est toujours
recréé avant une compilation; il ne doit jamais être modifié à la main.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FORMAT = "DMS-GAME-SETTINGS-1"


@dataclass(frozen=True)
class Setting:
    path: str
    section: str
    label: str
    kind: str
    default: int | float | bool
    minimum: int | float
    maximum: int | float
    macro: str
    help: str = ""


SETTINGS = (
    Setting("player.horizontal_speed", "Joueur", "Vitesse horizontale", "q8", 3.0, 0.0, 16.0, "DMS_GAME_PLAYER_SPEED_Q8", "pixels/image"),
    Setting("player.crouch_speed", "Joueur", "Vitesse accroupie", "q8", 2.0, 0.0, 16.0, "DMS_GAME_CROUCH_SPEED_Q8", "pixels/image"),
    Setting("player.acceleration", "Joueur", "Accélération", "q8", 0.75, 0.0, 8.0, "DMS_GAME_ACCELERATION_Q8", "pixels/image²"),
    Setting("player.instant_stop", "Joueur", "Arrêt instantané", "bool", True, 0, 1, "DMS_GAME_INSTANT_STOP", ""),
    Setting("player.gravity", "Joueur", "Gravité", "q8", 0.375, 0.0, 4.0, "DMS_GAME_GRAVITY_Q8", "pixels/image²"),
    Setting("player.jump_impulse", "Joueur", "Impulsion saut", "q8", -6.0, -24.0, 0.0, "DMS_GAME_JUMP_IMPULSE_Q8", "valeur négative = vers le haut"),
    Setting("player.double_jump_impulse", "Joueur", "Impulsion double saut", "q8", -5.5, -24.0, 0.0, "DMS_GAME_DOUBLE_JUMP_Q8", ""),
    Setting("player.short_jump_cutoff", "Joueur", "Coupure saut court", "q8", -3.5, -24.0, 0.0, "DMS_GAME_SHORT_JUMP_Q8", ""),
    Setting("player.long_jump_hold_frames", "Joueur", "Maintien saut long", "int", 12, 0, 120, "DMS_GAME_LONG_JUMP_FRAMES", "images"),
    Setting("player.max_fall_speed", "Joueur", "Vitesse de chute maximale", "q8", 8.0, 0.0, 32.0, "DMS_GAME_MAX_FALL_Q8", "pixels/image"),
    Setting("camera.offset_x", "Caméra", "Décalage horizontal", "int", 0, -320, 320, "DMS_GAME_CAMERA_OFFSET_X", "pixels"),
    Setting("camera.offset_y", "Caméra", "Décalage vertical", "int", -16, -224, 224, "DMS_GAME_CAMERA_OFFSET_Y", "pixels"),
    Setting("camera.deadzone_x", "Caméra", "Zone morte horizontale", "int", 56, 0, 320, "DMS_GAME_CAMERA_DEADZONE_X", "pixels"),
    Setting("camera.deadzone_y", "Caméra", "Zone morte verticale", "int", 36, 0, 224, "DMS_GAME_CAMERA_DEADZONE_Y", "pixels"),
    Setting("camera.scroll_speed", "Caméra", "Vitesse de suivi", "q8", 4.0, 0.0, 32.0, "DMS_GAME_CAMERA_SCROLL_Q8", "pixels/image"),
    Setting("camera.limit_left", "Caméra", "Limite gauche", "int", 0, -32768, 32767, "DMS_GAME_CAMERA_LIMIT_LEFT", "pixels"),
    Setting("camera.limit_top", "Caméra", "Limite haute", "int", 0, -32768, 32767, "DMS_GAME_CAMERA_LIMIT_TOP", "pixels"),
    Setting("camera.limit_right", "Caméra", "Limite droite", "int", 4096, -32768, 32767, "DMS_GAME_CAMERA_LIMIT_RIGHT", "pixels"),
    Setting("camera.limit_bottom", "Caméra", "Limite basse", "int", 2048, -32768, 32767, "DMS_GAME_CAMERA_LIMIT_BOTTOM", "pixels"),
    Setting("parallax.bg_a_x", "Parallaxe", "BG A - ratio X", "q8", 1.0, -4.0, 4.0, "DMS_GAME_PARALLAX_A_X_Q8", "1 = caméra, 0 = fixe"),
    Setting("parallax.bg_a_y", "Parallaxe", "BG A - ratio Y", "q8", 1.0, -4.0, 4.0, "DMS_GAME_PARALLAX_A_Y_Q8", ""),
    Setting("parallax.bg_b_x", "Parallaxe", "BG B - ratio X", "q8", 0.25, -4.0, 4.0, "DMS_GAME_PARALLAX_B_X_Q8", ""),
    Setting("parallax.bg_b_y", "Parallaxe", "BG B - ratio Y", "q8", 0.125, -4.0, 4.0, "DMS_GAME_PARALLAX_B_Y_Q8", ""),
    Setting("gameplay.difficulty", "Partie", "Difficulté", "int", 1, 0, 3, "DMS_GAME_DIFFICULTY", "0 facile, 1 normale, 2 difficile, 3 expert"),
    Setting("gameplay.starting_lives", "Partie", "Vies initiales", "int", 3, 1, 99, "DMS_GAME_STARTING_LIVES", ""),
    Setting("gameplay.continues", "Partie", "Continues", "int", 2, 0, 99, "DMS_GAME_CONTINUES", ""),
    Setting("timers.title_frames", "Partie", "Durée écran titre", "int", 600, 1, 36000, "DMS_GAME_TITLE_FRAMES", "images"),
    Setting("timers.transition_frames", "Partie", "Durée transition", "int", 30, 0, 600, "DMS_GAME_TRANSITION_FRAMES", "images"),
    Setting("timers.game_over_frames", "Partie", "Durée game over", "int", 360, 1, 36000, "DMS_GAME_OVER_FRAMES", "images"),
    Setting("ambience.cloud_0.enabled", "Ambiance", "Nuage 1 actif", "bool", True, 0, 1, "DMS_GAME_CLOUD0_ENABLED", ""),
    Setting("ambience.cloud_0.start_x", "Ambiance", "Nuage 1 - départ X", "int", 352, -1024, 8192, "DMS_GAME_CLOUD0_START_X", "pixels"),
    Setting("ambience.cloud_0.y", "Ambiance", "Nuage 1 - Y", "int", 42, -224, 448, "DMS_GAME_CLOUD0_Y", "pixels"),
    Setting("ambience.cloud_0.speed", "Ambiance", "Nuage 1 - vitesse", "q8", -1.0, -16.0, 16.0, "DMS_GAME_CLOUD0_SPEED_Q8", "pixels/image"),
    Setting("ambience.cloud_0.cadence", "Ambiance", "Nuage 1 - cadence", "int", 1, 1, 600, "DMS_GAME_CLOUD0_CADENCE", "images"),
    Setting("ambience.cloud_1.enabled", "Ambiance", "Nuage 2 actif", "bool", True, 0, 1, "DMS_GAME_CLOUD1_ENABLED", ""),
    Setting("ambience.cloud_1.start_x", "Ambiance", "Nuage 2 - départ X", "int", 520, -1024, 8192, "DMS_GAME_CLOUD1_START_X", "pixels"),
    Setting("ambience.cloud_1.y", "Ambiance", "Nuage 2 - Y", "int", 78, -224, 448, "DMS_GAME_CLOUD1_Y", "pixels"),
    Setting("ambience.cloud_1.speed", "Ambiance", "Nuage 2 - vitesse", "q8", -0.5, -16.0, 16.0, "DMS_GAME_CLOUD1_SPEED_Q8", "pixels/image"),
    Setting("ambience.cloud_1.cadence", "Ambiance", "Nuage 2 - cadence", "int", 2, 1, 600, "DMS_GAME_CLOUD1_CADENCE", "images"),
    Setting("ambience.despawn_x", "Ambiance", "Disparition à gauche", "int", -20, -1024, 320, "DMS_GAME_AMBIENCE_DESPAWN_X", "pixels"),
    Setting("ambience.foreground_enabled", "Ambiance", "Élément devant joueur actif", "bool", True, 0, 1, "DMS_GAME_FOREGROUND_ENABLED", ""),
)

BY_PATH = {s.path: s for s in SETTINGS}


class SettingsError(ValueError):
    pass


def _get(data: dict[str, Any], path: str, fallback: Any = None) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return fallback
        cur = cur[part]
    return cur


def _set(data: dict[str, Any], path: str, value: Any) -> None:
    cur = data
    parts = path.split(".")
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def default_document() -> dict[str, Any]:
    data: dict[str, Any] = {
        "format": FORMAT,
        "version": 1,
        "compatibility": {"macros": {}},
    }
    for setting in SETTINGS:
        _set(data, setting.path, setting.default)
    return data


def normalize_document(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(raw, dict):
        raise SettingsError("la racine JSON doit être un objet")
    source_format = raw.get("format")
    if source_format not in (None, FORMAT):
        raise SettingsError(f"format non pris en charge : {source_format!r}")
    data = default_document()
    warnings: list[str] = []
    for setting in SETTINGS:
        value = _get(raw, setting.path, setting.default)
        if _get(raw, setting.path, None) is None:
            warnings.append(f"{setting.path} absent : valeur par défaut ajoutée")
        _set(data, setting.path, value)
    compatibility = raw.get("compatibility", {})
    if isinstance(compatibility, dict) and isinstance(compatibility.get("macros", {}), dict):
        data["compatibility"] = {"macros": dict(compatibility.get("macros", {}))}
    return data, warnings


def validate_document(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("format") != FORMAT:
        errors.append(f"format doit valoir {FORMAT}")
    for setting in SETTINGS:
        value = _get(data, setting.path)
        if setting.kind == "bool":
            if not isinstance(value, bool):
                errors.append(f"{setting.path} doit être vrai ou faux")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            errors.append(f"{setting.path} doit être un nombre")
            continue
        if value < setting.minimum or value > setting.maximum:
            errors.append(f"{setting.path} hors limites [{setting.minimum}, {setting.maximum}]")
    macros = _get(data, "compatibility.macros", {})
    if not isinstance(macros, dict):
        errors.append("compatibility.macros doit être un objet")
    else:
        for macro, path in macros.items():
            if not isinstance(macro, str) or not macro.replace("_", "A").isalnum() or macro.upper() != macro:
                errors.append(f"macro de compatibilité invalide : {macro!r}")
            if path not in BY_PATH:
                errors.append(f"réglage inconnu pour {macro} : {path!r}")
    return errors


def load_document(path: Path) -> tuple[dict[str, Any], list[str]]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    data, warnings = normalize_document(raw)
    errors = validate_document(data)
    if errors:
        raise SettingsError("\n".join(errors))
    return data, warnings


def save_document(path: Path, data: dict[str, Any]) -> None:
    normalized, _ = normalize_document(data)
    errors = validate_document(normalized)
    if errors:
        raise SettingsError("\n".join(errors))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(normalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def encoded(setting: Setting, value: Any) -> int:
    if setting.kind == "bool":
        return 1 if value else 0
    if setting.kind == "q8":
        return int(round(float(value) * 256.0))
    return int(value)


def compile_document(source: Path, output_dir: Path) -> dict[str, Any]:
    data, warnings = load_document(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    values = {setting.path: encoded(setting, _get(data, setting.path)) for setting in SETTINGS}
    guard = "DMS_GAME_SETTINGS_GENERATED_H"
    h_lines = [
        "/* Généré depuis dms_game_settings.json - ne pas modifier. */",
        f"#ifndef {guard}",
        f"#define {guard}",
        "#include <stdint.h>",
        "#include \"dms_game_settings.h\"",
        "",
    ]
    for setting in SETTINGS:
        h_lines.append(f"#define {setting.macro} ({values[setting.path]})")
    h_lines += ["", "extern const DmsGameSettings dms_game_settings;", "", f"#endif /* {guard} */", ""]
    fields = ",\n    ".join(f"{values[s.path]}" for s in SETTINGS)
    c_text = (
        "/* Généré depuis dms_game_settings.json - ne pas modifier. */\n"
        "#include \"dms_game_settings_generated.h\"\n\n"
        "const DmsGameSettings dms_game_settings = {\n    " + fields + "\n};\n"
    )
    compat_lines = [
        "/* Alias projet générés - injectés avant les sources du jeu. */",
        "#ifndef DMS_GAME_SETTINGS_COMPAT_H",
        "#define DMS_GAME_SETTINGS_COMPAT_H",
        "#include \"dms_game_settings_generated.h\"",
        "",
    ]
    for macro, path in sorted(_get(data, "compatibility.macros", {}).items()):
        compat_lines += [f"#ifndef {macro}", f"#define {macro} ({values[path]})", "#endif"]
    compat_lines += ["", "#endif", ""]
    (output_dir / "dms_game_settings_generated.h").write_text("\n".join(h_lines), encoding="utf-8")
    (output_dir / "dms_game_settings_generated.c").write_text(c_text, encoding="utf-8")
    (output_dir / "dms_game_settings_compat.h").write_text("\n".join(compat_lines), encoding="utf-8")
    report = {
        "format": "DMS-GAME-SETTINGS-COMPILE-REPORT-1",
        "source": os.path.relpath(source.resolve(), output_dir.resolve()).replace("\\", "/"),
        "values": values,
        "warnings": warnings,
        "generated": ["dms_game_settings_generated.h", "dms_game_settings_generated.c", "dms_game_settings_compat.h"],
    }
    (output_dir / "dms_game_settings_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Valide ou compile les réglages centraux DMS-GDK")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.init:
            if args.source.exists() and not args.force:
                raise SettingsError(f"refus d’écraser le fichier existant : {args.source}")
            save_document(args.source, default_document())
            print("PASS : réglages créés :", args.source)
            return 0
        data, warnings = load_document(args.source)
        for warning in warnings:
            print("AVERTISSEMENT :", warning)
        if args.validate_only:
            print(f"PASS : {len(SETTINGS)} réglages valides : {args.source}")
            return 0
        output = args.output or args.source.parent / "build" / "autogen"
        report = compile_document(args.source, output)
        print(f"PASS : {len(report['values'])} réglages compilés -> {output}")
        return 0
    except Exception as exc:
        print("ERREUR RÉGLAGES :", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
