#!/usr/bin/env python3
"""Validateur/compilateur headless des scènes DMS-1 DSCENE V2."""
from __future__ import annotations

import argparse
import json
import re
import zlib
from pathlib import Path
from typing import Any, Iterable

FORMAT = "DSCENE"
VERSION = 2
MODE_INFO = {
    0: {"name": "STANDARD", "width": 320, "height": 224, "palettes": 4, "bg_b": True, "sprites": 80, "scanline": 20},
    1: {"name": "HIGH COLOR", "width": 320, "height": 224, "palettes": 8, "bg_b": False, "sprites": 80, "scanline": 20},
    2: {"name": "SCROLL", "width": 320, "height": 224, "palettes": 4, "bg_b": True, "sprites": 48, "scanline": 12},
    3: {"name": "SPRITE", "width": 320, "height": 224, "palettes": 4, "bg_b": False, "sprites": 128, "scanline": 32},
    4: {"name": "LOW RES", "width": 256, "height": 224, "palettes": 8, "bg_b": True, "sprites": 96, "scanline": 24},
}
KINDS = {
    "SPRITE": 1,
    "ACTOR": 2,
    "BOSS": 2,
    "ATMOSPHERE": 3,
    "TEXT": 4,
    "UI": 5,
    "TITLE": 5,
    "CINEMATIC": 5,
    "TRANSITION": 6,
}
LAYERS = {
    "BG_B": 0,
    "BG_A_BEHIND": 1,
    "ACTORS": 2,
    "BG_A_FRONT": 3,
    "ATMOSPHERE": 4,
    "UI": 5,
    "TRANSITION": 6,
}

OPS = {"SHOW":1,"HIDE":2,"TYPEWRITER":3,"SLIDE_IN":4,"FX_START":5,"MUSIC_PLAY":6,"MUSIC_STOP":7,
       "SFX_PLAY":8,"MENU_ENABLE":9,"WAIT_INPUT":10,"END":11,"CAMERA_SET":12,"CAMERA_SPEED":13,
       "SCROLL_SET":14,"VIDEO_MODE":15,"TRIGGER":16,"SPAWN_FORMATION":17,"CHECKPOINT":18,"FLOW_EMIT":19}
FX_ORDER = ["NONE","SHAKE","KICK","FLASH","FADE_OUT","FADE_IN","PULSE","COLOR_CYCLE","WATER_WAVE","RIPPLE",
            "HEAT_HAZE","SHEAR_WOBBLE","RASTER_SPLIT","SCAN_SWEEP","SPEED_BANDS","BG_PARALLAX_OSC",
            "PALETTE_INVERT","PALETTE_TINT","PALETTE_DESATURATE","PALETTE_STROBE","HIT_FREEZE_VISUAL",
            "EARTHQUAKE_RASTER","PERSPECTIVE_WARP","UNDERWATER_DRIFT","PARALLAX_KICK","BG_DEPTH_SWAY"]
FX_ID = {name:i for i,name in enumerate(FX_ORDER)}
MODE2_FX = {"WATER_WAVE","RIPPLE","HEAT_HAZE","SHEAR_WOBBLE","RASTER_SPLIT","SCAN_SWEEP","SPEED_BANDS","EARTHQUAKE_RASTER","PERSPECTIVE_WARP","UNDERWATER_DRIFT"}
OPTION_TYPES = {"NONE":0,"LIVES":1,"MUSIC_TEST":2,"SFX_TEST":3}
WAIT_MASKS = {"A":0x10,"B":0x20,"+":0x40,"PLUS":0x40,"C":0x40,"×":0x80,"X":0x80,"MULTIPLY":0x80,"START":0x80,"ANY":0xF0}


class SceneCompileError(RuntimeError):
    pass


def symbol(value: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_]+", "_", value.upper()).strip("_") or "SCENE"
    return "S_" + out if out[0].isdigit() else out


def stable_trigger(value: Any) -> int:
    if value in (None, "", 0, "0"):
        return 0
    if isinstance(value, int):
        return value & 0xFFFF
    text = str(value).strip()
    try:
        return int(text, 0) & 0xFFFF
    except ValueError:
        return (zlib.crc32(text.upper().encode("utf-8")) & 0xFFFF) or 1


def stable_flow_event(value: Any) -> int:
    """Stable FLOW event id, intentionally identical to dmsflowc.stable_event_id."""
    if value in (None, "", 0, "0"):
        return 0
    if isinstance(value, int):
        return value & 0xFFFF
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value).upper()).strip("_") or "ITEM"
    if text[0].isdigit():
        text = "N_" + text
    return (zlib.crc32(text.encode("utf-8")) & 0xFFFF) or 1


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _q8(value: Any, default: float = 0.0) -> int:
    return max(-32768, min(32767, int(round(_float(value, default) * 256.0))))


def migrate_v1(raw: dict[str, Any]) -> dict[str, Any]:
    data = json.loads(json.dumps(raw))
    data["format"] = FORMAT
    data["format_version"] = VERSION
    data.setdefault("type", "TITLE")
    data.setdefault("parallax", {"a_x": 1.0, "a_y": 1.0, "b_x": 0.25, "b_y": 0.125})
    data.setdefault("camera", {"x":0,"y":0,"speed_x":0.0,"speed_y":0.0})
    data.setdefault("menu_move_sfx", 0)
    data.setdefault("menu_validate_sfx", 0)
    data.setdefault("map", "")
    data.setdefault("budgets", {})
    for obj in data.get("objects", []):
        if obj.get("kind") in ("TEXT", "MENU_ITEM"):
            obj.setdefault("layer", "UI")
            obj.setdefault("priority", True)
            obj["x"] = _int(obj.get("x")) * 8
            obj["y"] = _int(obj.get("y")) * 8
            if obj.get("kind") == "MENU_ITEM":
                obj["kind"] = "UI"
        elif obj.get("kind") == "BACKGROUND":
            obj.setdefault("layer", "BG_B" if str(obj.get("plane", "B")).upper() == "B" else "BG_A_BEHIND")
        _object_defaults(obj)
    data["migration"] = {"from": "DSCENE-1", "automatic": True}
    return data


def _object_defaults(obj: dict[str, Any]) -> None:
    kind = str(obj.get("kind", "SPRITE")).upper()
    obj["kind"] = kind
    obj.setdefault("layer", "ACTORS" if kind in ("ACTOR", "BOSS", "SPRITE") else ("ATMOSPHERE" if kind == "ATMOSPHERE" else "UI"))
    obj.setdefault("resource", "")
    obj.setdefault("text", "")
    obj.setdefault("x", 0)
    obj.setdefault("y", 0)
    obj.setdefault("velocity_x", 0.0)
    obj.setdefault("velocity_y", 0.0)
    obj.setdefault("parallax_x", 1.0)
    obj.setdefault("parallax_y", 1.0)
    obj.setdefault("spawn_x", obj.get("x", 0))
    obj.setdefault("spawn_y", obj.get("y", 0))
    obj.setdefault("despawn_left", -64)
    obj.setdefault("despawn_right", 384)
    obj.setdefault("despawn_top", -64)
    obj.setdefault("despawn_bottom", 288)
    obj.setdefault("direction", "LEFT" if _float(obj.get("velocity_x")) < 0 else "RIGHT")
    obj.setdefault("loop", False)
    obj.setdefault("animation", 0)
    obj.setdefault("cadence", 0)
    obj.setdefault("palette", 0)
    obj.setdefault("palette_animation", "NONE")
    obj.setdefault("palette_cadence", 8)
    obj.setdefault("palette_span", 1)
    obj.setdefault("priority", str(obj.get("layer")).upper() in ("BG_A_FRONT", "UI", "TRANSITION"))
    obj.setdefault("visible", True)
    obj.setdefault("screen_space", str(obj.get("layer")).upper() in ("UI", "TRANSITION"))
    obj.setdefault("start_frame", 0)
    obj.setdefault("end_frame", 0)
    obj.setdefault("start_trigger", "")
    obj.setdefault("end_trigger", "")
    obj.setdefault("sprite_cells", 4 if kind not in ("TEXT", "BACKGROUND") else 0)
    obj.setdefault("selected_palette", obj.get("palette", 0))
    obj.setdefault("action", 0)
    obj.setdefault("destination", "")
    obj.setdefault("option_type", "NONE")
    obj.setdefault("option_min", 0)
    obj.setdefault("option_max", 0)
    obj.setdefault("option_step", 1)
    obj.setdefault("option_value", 0)


def normalize_scene(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(raw, dict):
        raise SceneCompileError("la racine DSCENE doit être un objet JSON")
    if raw.get("format") != FORMAT:
        raise SceneCompileError("format DSCENE requis")
    version = _int(raw.get("format_version"), 0)
    warnings: list[str] = []
    if version == 1:
        data = migrate_v1(raw)
        warnings.append("DSCENE V1 migrée en mémoire vers V2; sauvegarder avec Scene Builder pour figer la migration")
    elif version == VERSION:
        data = json.loads(json.dumps(raw))
    else:
        raise SceneCompileError(f"version DSCENE {version} non prise en charge")
    data.setdefault("name", "SCENE")
    data.setdefault("type", "SCREEN")
    data.setdefault("video_mode", 0)
    data.setdefault("map", "")
    data.setdefault("scroll", {"a_x": 0, "a_y": 0, "b_x": 0, "b_y": 0})
    data.setdefault("parallax", {"a_x": 1.0, "a_y": 1.0, "b_x": 0.25, "b_y": 0.125})
    data.setdefault("camera", {"x":0,"y":0,"speed_x":0.0,"speed_y":0.0})
    data.setdefault("menu_move_sfx", 0)
    data.setdefault("menu_validate_sfx", 0)
    data.setdefault("budgets", {})
    data.setdefault("objects", [])
    data.setdefault("events", [])
    for obj in data["objects"]:
        if isinstance(obj, dict):
            _object_defaults(obj)
    return data, warnings


def load_scene(path: Path) -> tuple[dict[str, Any], list[str]]:
    return normalize_scene(json.loads(path.read_text(encoding="utf-8-sig")))


def validate_scene(scene: dict[str, Any], source: Path | None = None) -> list[tuple[str, str]]:
    diagnostics: list[tuple[str, str]] = []
    mode = _int(scene.get("video_mode"), -1)
    info = MODE_INFO.get(mode)
    if info is None:
        return [("ERROR", f"mode vidéo inconnu : {mode}")]
    objects = scene.get("objects")
    if not isinstance(objects, list):
        return [("ERROR", "objects doit être une liste")]
    if len(objects) > 128:
        diagnostics.append(("ERROR", f"{len(objects)} objets > limite Scene runtime 128"))
    ids: set[str] = set()
    sprite_cells = 0
    sprite_intervals: list[tuple[int,int,int]] = []
    trigger_ids: dict[int, str] = {}
    atmospheric = 0
    front = 0
    for index, obj in enumerate(objects):
        if not isinstance(obj, dict):
            diagnostics.append(("ERROR", f"objet {index} n’est pas un objet JSON"))
            continue
        oid = str(obj.get("id", "")).strip()
        if not oid or oid in ids:
            diagnostics.append(("ERROR", f"ID objet vide ou dupliqué : {oid!r}"))
        ids.add(oid)
        kind = str(obj.get("kind", "")).upper()
        layer = str(obj.get("layer", "")).upper()
        if kind == "BACKGROUND":
            diagnostics.append(("WARN", f"{oid}: BACKGROUND DIMG V1 reste exportable, mais une scène de jeu V2 doit référencer un DMAP dans map"))
            continue
        if kind not in KINDS:
            diagnostics.append(("ERROR", f"{oid}: kind {kind!r} inconnu"))
        if layer not in LAYERS:
            diagnostics.append(("ERROR", f"{oid}: layer {layer!r} inconnu"))
        if layer == "BG_B" and not info["bg_b"]:
            diagnostics.append(("ERROR", f"{oid}: BG B interdit en mode {mode}"))
        pal = _int(obj.get("palette"), 0)
        if pal < 0 or pal >= info["palettes"]:
            diagnostics.append(("ERROR", f"{oid}: palette P{pal} interdite en mode {mode}"))
        palette_animation = str(obj.get("palette_animation", "NONE")).upper()
        if palette_animation not in ("NONE", "CYCLE", "CYCLE_PALETTES"):
            diagnostics.append(("ERROR", f"{oid}: animation de palette {palette_animation!r} inconnue"))
        palette_span = max(1, _int(obj.get("palette_span"), 1))
        if pal + palette_span > info["palettes"]:
            diagnostics.append(("ERROR", f"{oid}: cycle P{pal} + {palette_span} palette(s) dépasse les {info['palettes']} palettes du mode"))
        needs_resource = kind in ("SPRITE", "ACTOR", "BOSS", "ATMOSPHERE", "TITLE", "CINEMATIC", "TRANSITION") or (kind == "UI" and not str(obj.get("text", "")))
        if needs_resource:
            ref = str(obj.get("resource", "")).strip()
            numeric_id = obj.get("resource_id")
            if not ref and numeric_id in (None, ""):
                diagnostics.append(("ERROR", f"{oid}: ressource DRES/DACTOR requise"))
            elif Path(ref).is_absolute():
                diagnostics.append(("ERROR", f"{oid}: chemin absolu interdit; utiliser une référence relative ou un symbole resources.dmsres"))
            cells=max(1, _int(obj.get("sprite_cells"), 4))
            # Hidden objects are templates / deferred SHOW targets. They are not
            # resident in the live sprite table until an event enables them.
            # dmsres performs the authoritative event-aware budget check below,
            # including SHOW and SPAWN_FORMATION at the video mode active then.
            if bool(obj.get("visible", True)):
                sprite_cells += cells
                st=max(0,_int(obj.get("start_frame"),0)); en=max(0,_int(obj.get("end_frame"),0)) or 65535
                sprite_intervals.append((st,en,cells))
        if kind == "TEXT" and len(str(obj.get("text", ""))) > 63:
            diagnostics.append(("ERROR", f"{oid}: texte > 63 caractères"))
        if kind == "ATMOSPHERE":
            atmospheric += 1
        if bool(obj.get("priority")) or layer == "BG_A_FRONT":
            front += 1
        start = _int(obj.get("start_frame"), 0)
        end = _int(obj.get("end_frame"), 0)
        if start < 0 or end < 0 or (end and end <= start):
            diagnostics.append(("ERROR", f"{oid}: intervalle start/end invalide"))
        for key in ("start_trigger", "end_trigger"):
            raw_trigger = obj.get(key)
            tid = stable_trigger(raw_trigger)
            if tid:
                label = str(raw_trigger)
                if tid in trigger_ids and trigger_ids[tid] != label:
                    diagnostics.append(("ERROR", f"collision d’ID trigger 0x{tid:04X}: {trigger_ids[tid]!r} / {label!r}"))
                trigger_ids[tid] = label
    peak_cells=0
    if sprite_intervals:
        for frame in sorted({x for st,en,_ in sprite_intervals for x in (st,en)}):
            peak_cells=max(peak_cells,sum(c for st,en,c in sprite_intervals if st<=frame<en))
    if peak_cells > info["sprites"]:
        diagnostics.append(("ERROR", f"budget sprites simultanés : {peak_cells} cellules > {info['sprites']} en mode {mode}"))
    elif peak_cells:
        diagnostics.append(("INFO", f"budget sprites simultanés estimé : {peak_cells}/{info['sprites']} cellules"))
    # Worst-case libdms scene bookkeeping only (game callbacks are measured by
    # the runtime profiler). 60 000 cycles leaves ample room in a 166 667-cycle
    # frame at 10 MHz/60 Hz for gameplay, VDP streaming and interrupts.
    cpu_cycles = min(len(objects),32) * 320 + peak_cells * 140
    if cpu_cycles > 60000:
        diagnostics.append(("ERROR", f"budget CPU scène estimé : {cpu_cycles} cycles/image > garde 60000"))
    else:
        diagnostics.append(("INFO", f"budget CPU scène estimé : {cpu_cycles}/60000 cycles/image (hors callbacks jeu)"))
    declared_scanline = _int((scene.get("budgets") or {}).get("scanline_cells"), peak_cells)
    if declared_scanline > info["scanline"]:
        diagnostics.append(("ERROR", f"budget scanline : {declared_scanline} > {info['scanline']} en mode {mode}"))
    scene_type = str(scene.get("type", "")).upper()
    if scene_type in ("GAME", "GAMEPLAY", "LEVEL"):
        if atmospheric < 2:
            diagnostics.append(("ERROR", "une scène GAMEPLAY doit contenir deux objets ATMOSPHERE indépendants"))
        if front < 1:
            diagnostics.append(("ERROR", "une scène GAMEPLAY doit contenir au moins un élément devant le joueur"))
    map_ref = str(scene.get("map", "")).strip()
    if map_ref and Path(map_ref).is_absolute():
        diagnostics.append(("ERROR", "scene.map doit être relatif ou être un symbole resources.dmsres"))
    if source and map_ref and any(sep in map_ref for sep in ("/", "\\")) and not (source.parent / map_ref).resolve().is_file():
        diagnostics.append(("ERROR", f"DMAP relatif absent : {map_ref}"))
    event_mode=mode
    for idx,event in enumerate(sorted(scene.get("events") or [],key=lambda q:_int(q.get("frame"),0))):
        op=str(event.get("op","")).upper()
        if op not in OPS:
            diagnostics.append(("ERROR",f"event {idx}: opération {op!r} inconnue")); continue
        target=str(event.get("target","")).strip()
        if op in ("SHOW","HIDE","TYPEWRITER","SLIDE_IN","SPAWN_FORMATION") and target not in ids:
            diagnostics.append(("ERROR",f"event {idx} {op}: target absent {target!r}"))
        if op=="VIDEO_MODE":
            new_mode=_int(event.get("mode",event.get("ref",-1)),-1)
            if new_mode not in MODE_INFO: diagnostics.append(("ERROR",f"event {idx}: mode vidéo invalide"))
            else: event_mode=new_mode
        if op=="FX_START":
            fx=str(event.get("fx","NONE")).upper()
            if fx not in FX_ID: diagnostics.append(("ERROR",f"event {idx}: FX {fx!r} inconnu"))
            elif fx in MODE2_FX and event_mode!=2: diagnostics.append(("ERROR",f"event {idx}: FX {fx} requiert MODE 2 à cette frame"))
    return diagnostics


def _resolve_resource(ref: str, expected: set[str], source: Path, resources: Iterable[dict[str, Any]]) -> int:
    if not ref:
        return 0xFFFF
    wanted_symbol = symbol(ref)
    wanted_path = (source.parent / ref).resolve() if any(c in ref for c in ("/", "\\", ".")) else None
    matches = []
    for item in resources:
        if str(item.get("kind", "")).upper() not in expected:
            continue
        name = symbol(str(item.get("name", "")))
        path = Path(item.get("path", "")).resolve() if item.get("path") else None
        if name == wanted_symbol or (wanted_path is not None and path == wanted_path):
            matches.append(int(item["id"]))
    if len(matches) != 1:
        label = ", ".join(sorted(expected))
        raise SceneCompileError(f"référence {ref!r}: attendu une unique ressource {label}, trouvé {len(matches)}")
    return matches[0]


def compile_runtime(scene: dict[str, Any], source: Path, resources: Iterable[dict[str, Any]], resource_id: int) -> dict[str, Any]:
    errors = [message for severity, message in validate_scene(scene, source) if severity == "ERROR"]
    if errors:
        raise SceneCompileError("; ".join(errors))
    rows = []
    object_index: dict[str,int] = {}
    for obj in scene.get("objects", []):
        kind_name = str(obj.get("kind", "")).upper()
        if kind_name == "BACKGROUND":
            continue
        text_object = kind_name == "TEXT" or (kind_name == "UI" and str(obj.get("text", "")) and not str(obj.get("resource", "")))
        expected = {"ACTOR"} if kind_name in ("ACTOR", "BOSS") else {"SPRITE"}
        if text_object:
            rid = 0xFFFF
        elif obj.get("resource_id") not in (None, ""):
            rid = max(0, min(0xFFFE, _int(obj.get("resource_id"))))
        else:
            rid = _resolve_resource(str(obj.get("resource", "")), expected, source, resources)
        layer = str(obj.get("layer", "UI")).upper()
        oid=str(obj.get("id","OBJECT")); object_index[oid]=len(rows)
        opt_name=str(obj.get("option_type","NONE")).upper()
        destination=str(obj.get("destination","")).strip()
        action_event=stable_flow_event(destination) if destination else max(0,min(0xFFFF,_int(obj.get("action"),0)))
        rows.append({
            "id": oid, "resource_id": rid, "text": str(obj.get("text", "")),
            "x": _int(obj.get("x")), "y": _int(obj.get("y")),
            "vx": _q8(obj.get("velocity_x")), "vy": _q8(obj.get("velocity_y")),
            "px": _q8(obj.get("parallax_x"), 1.0), "py": _q8(obj.get("parallax_y"), 1.0),
            "spawn_x": _int(obj.get("spawn_x"), _int(obj.get("x"))), "spawn_y": _int(obj.get("spawn_y"), _int(obj.get("y"))),
            "left": _int(obj.get("despawn_left"), -64), "right": _int(obj.get("despawn_right"), 384),
            "top": _int(obj.get("despawn_top"), -64), "bottom": _int(obj.get("despawn_bottom"), 288),
            "animation": _int(obj.get("animation")), "cadence": max(0, _int(obj.get("cadence"))),
            "start": max(0, _int(obj.get("start_frame"))), "end": max(0, _int(obj.get("end_frame"))),
            "start_trigger": stable_trigger(obj.get("start_trigger")), "end_trigger": stable_trigger(obj.get("end_trigger")),
            "kind": KINDS[kind_name] if text_object else KINDS[kind_name], "layer": LAYERS[layer], "priority": 1 if obj.get("priority") else 0,
            "palette": max(0, _int(obj.get("palette"))), "selected_palette":max(0,_int(obj.get("selected_palette"),_int(obj.get("palette")))),
            "palette_animation": 1 if str(obj.get("palette_animation", "NONE")).upper() in ("CYCLE", "CYCLE_PALETTES") else 0,
            "palette_cadence": max(1, _int(obj.get("palette_cadence"), 8)), "palette_span": max(1, _int(obj.get("palette_span"), 1)),
            "visible": 1 if obj.get("visible", True) else 0,
            "loop": 1 if obj.get("loop") else 0, "screen": 1 if obj.get("screen_space") else 0,
            "direction": 1 if str(obj.get("direction", "RIGHT")).upper() in ("LEFT", "GAUCHE", "-1") else 0,
            "action_event":action_event, "option_type":OPTION_TYPES.get(opt_name,0),
            "option_min":_int(obj.get("option_min"),0), "option_max":_int(obj.get("option_max"),0),
            "option_step":_int(obj.get("option_step"),1) or 1, "option_value":_int(obj.get("option_value"),0),
        })
    evrows=[]
    for ev in sorted(scene.get("events") or [],key=lambda q:_int(q.get("frame"),0)):
        opname=str(ev.get("op","")).upper(); op=OPS[opname]
        target=object_index.get(str(ev.get("target","")),255); a=b=c=d=0; ref=max(0,min(0xFFFF,_int(ev.get("ref"),0)))
        if opname=="TYPEWRITER": a=_int(ev.get("speed"),2)
        elif opname=="SLIDE_IN": a=_int(ev.get("offset"),20); b=_int(ev.get("duration"),24)
        elif opname=="FX_START": ref=FX_ID[str(ev.get("fx","NONE")).upper()]; a=_int(ev.get("intensity"),15); b=_int(ev.get("duration"),0); c=_int(ev.get("secondary"),0); d=_int(ev.get("palette_mask"),15)
        elif opname in ("MUSIC_PLAY","SFX_PLAY"): ref=max(0,min(0xFFFF,_int(ev.get("ref"),0)))
        elif opname=="CAMERA_SET": a=_int(ev.get("x"),_int(ev.get("a"),0)); b=_int(ev.get("y"),_int(ev.get("b"),0))
        elif opname=="CAMERA_SPEED": a=_q8(ev.get("speed_x",ev.get("x",0))); b=_q8(ev.get("speed_y",ev.get("y",0)))
        elif opname=="SCROLL_SET": a=_int(ev.get("a_x"),0); b=_int(ev.get("a_y"),0); c=_int(ev.get("b_x"),0); d=_int(ev.get("b_y"),0)
        elif opname=="VIDEO_MODE": a=_int(ev.get("mode"),_int(ev.get("ref"),0))
        elif opname == "TRIGGER": ref=stable_trigger(ev.get("trigger",ev.get("event",ev.get("ref",0))))
        elif opname == "FLOW_EMIT": ref=stable_flow_event(ev.get("event",ev.get("trigger",ev.get("ref",0))))
        elif opname=="SPAWN_FORMATION": a=max(1,min(64,_int(ev.get("count"),1))); b=_int(ev.get("spacing_x"),0); c=_int(ev.get("spacing_y"),0); d=_int(ev.get("velocity_scale",256),256)
        elif opname=="CHECKPOINT": a=_int(ev.get("x"),0); b=_int(ev.get("y"),0)
        elif opname=="WAIT_INPUT": a=WAIT_MASKS.get(str(ev.get("wait","ANY")).upper(),0xF0)
        evrows.append({"frame":max(0,_int(ev.get("frame"),0)),"op":op,"target":target,"a":a,"b":b,"c":c,"d":d,"ref":ref})
    scroll = scene.get("scroll") or {}; parallax = scene.get("parallax") or {}; camera=scene.get("camera") or {}
    return {
        "resource_id": resource_id, "name": str(scene.get("name", "SCENE")), "objects": rows, "events":evrows,
        "map_resource_id": _resolve_resource(str(scene.get("map", "")), {"MAP"}, source, resources) if str(scene.get("map", "")).strip() else 0xFFFF,
        "scroll_a_x": _int(scroll.get("a_x")), "scroll_a_y": _int(scroll.get("a_y")), "scroll_b_x": _int(scroll.get("b_x")), "scroll_b_y": _int(scroll.get("b_y")),
        "parallax_a_x": _q8(parallax.get("a_x"), 1.0), "parallax_a_y": _q8(parallax.get("a_y"), 1.0),
        "parallax_b_x": _q8(parallax.get("b_x"), 0.25), "parallax_b_y": _q8(parallax.get("b_y"), 0.125),
        "camera_x":_int(camera.get("x"),0),"camera_y":_int(camera.get("y"),0),
        "camera_speed_x":_q8(camera.get("speed_x"),0),"camera_speed_y":_q8(camera.get("speed_y"),0),
        "menu_move_sfx":max(0,min(0xFFFF,_int(scene.get("menu_move_sfx"),0))),"menu_validate_sfx":max(0,min(0xFFFF,_int(scene.get("menu_validate_sfx"),0))),
        "video_mode": _int(scene.get("video_mode")), "flags": 1 if str(scene.get("type","")).upper()=="MENU" else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validation DSCENE V2 DMS-1")
    parser.add_argument("scene", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        scene, warnings = load_scene(args.scene.resolve())
        for warning in warnings:
            print("AVERTISSEMENT :", warning)
        diagnostics = validate_scene(scene, args.scene.resolve())
        for severity, message in diagnostics:
            print(("ERREUR" if severity == "ERROR" else severity), ":", message)
        if any(severity == "ERROR" for severity, _ in diagnostics):
            return 2
        print(f"PASS : scène {scene.get('name')} - {len(scene.get('objects', []))} objets - Mode {scene.get('video_mode')}")
        if args.out and not args.validate_only:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(scene, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print("PASS : DSCENE V2 normalisée ->", args.out)
        return 0
    except Exception as exc:
        print("ERREUR DSCENE :", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
