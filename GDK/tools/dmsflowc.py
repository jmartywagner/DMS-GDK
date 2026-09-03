#!/usr/bin/env python3
"""DMS Game Flow compiler/validator V0.1.

DFLOW is the editable, versioned source of truth for the game-level state graph.
The compiler emits deterministic C/H, a compact binary table and a JSON manifest.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

FORMAT = "DFLOW"
FORMAT_VERSION = 1
NODE_TYPES = ("SCREEN", "MENU", "GAME", "CUTSCENE", "SUBFLOW")
RESOURCE_FIELDS = {
    "scene": ("SCENE", (".dscene",)),
    "map": ("MAP", (".dmap",)),
    "collision": ("COLLISION", (".dcoll",)),
    "actor": ("ACTOR", (".dactor",)),
    "music": ("MUSIC", (".dmr",)),
    "image": ("IMAGE", (".dimg",)),
    "sprite": ("SPRITE", (".dres",)),
    "audio": ("AUDIO", tuple()),
}
FX_NAMES = ("NONE", "FADE_IN", "FADE_OUT", "FLASH", "SHAKE")
C_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

@dataclass
class Diagnostic:
    severity: str
    message: str
    item: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "message": self.message, "item": self.item}

class FlowError(RuntimeError):
    pass

def c_symbol(text: str, prefix: str = "") -> str:
    s = re.sub(r"[^A-Za-z0-9_]+", "_", str(text).upper()).strip("_") or "ITEM"
    if s[0].isdigit():
        s = "N_" + s
    return prefix + s

def stable_event_id(value: str) -> int:
    text=c_symbol(value)
    return (zlib.crc32(text.encode("utf-8")) & 0xFFFF) or 1

def normalize_path(path: str) -> str:
    return str(path or "").replace("\\", "/")

def new_flow(name: str = "MAIN FLOW") -> dict[str, Any]:
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "generator": "DMS Game Flow Builder V0.1",
        "name": name,
        "main_flow": "MAIN",
        "resource_manifest": "resources.dmsres",
        "flows": [{"id": "MAIN", "name": name, "entry_state": ""}],
        "nodes": [],
        "transitions": [],
        "settings": {"autosave": True, "grid": 16},
    }

def load_flow(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise FlowError(f"DFLOW illisible : {exc}") from exc
    if data.get("format") != FORMAT:
        raise FlowError(f"format {data.get('format')!r}, attendu {FORMAT}")
    if int(data.get("format_version", -1)) != FORMAT_VERSION:
        raise FlowError(f"DFLOW version {data.get('format_version')} non supportée (attendue : {FORMAT_VERSION})")
    data.setdefault("flows", [])
    data.setdefault("nodes", [])
    data.setdefault("transitions", [])
    data.setdefault("main_flow", "MAIN")
    data.setdefault("resource_manifest", "resources.dmsres")
    return data

def save_flow(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try: tmp.unlink(missing_ok=True)
        except Exception: pass

def _parse_resources_manifest(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return out
    for lineno, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        parts = re.findall(r'"[^"]*"|\S+', line)
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts]
        if len(parts) < 3:
            continue
        kind, name, raw_path = parts[:3]
        opts: dict[str, str] = {}
        for token in parts[3:]:
            if "=" in token:
                k, v = token.split("=", 1)
                opts[k.upper()] = v
        out[c_symbol(name)] = {"kind": kind.upper(), "path": raw_path, "options": opts, "lineno": lineno}
    return out

def _node_index(flow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(n.get("id", "")): n for n in flow.get("nodes", []) if n.get("id")}

def _flow_index(flow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(f.get("id", "")): f for f in flow.get("flows", []) if f.get("id")}

def _effective_destination(dest: str, nodes: dict[str, dict[str, Any]], flows: dict[str, dict[str, Any]]) -> str:
    seen: set[str] = set()
    cur = dest
    while cur in nodes and str(nodes[cur].get("type", "")).upper() == "SUBFLOW":
        if cur in seen:
            return cur
        seen.add(cur)
        sf = str(nodes[cur].get("subflow_id", ""))
        entry = str((flows.get(sf) or {}).get("entry_state", ""))
        if not entry:
            return cur
        cur = entry
    return cur

def validate_flow(flow: dict[str, Any], source_path: Path | None = None) -> list[Diagnostic]:
    d: list[Diagnostic] = []
    nodes_list = flow.get("nodes", [])
    transitions = flow.get("transitions", [])
    flows_list = flow.get("flows", [])
    nodes = _node_index(flow)
    flows = _flow_index(flow)

    if flow.get("format") != FORMAT:
        d.append(Diagnostic("ERROR", f"format attendu {FORMAT}"))
    if int(flow.get("format_version", -1)) != FORMAT_VERSION:
        d.append(Diagnostic("ERROR", f"version DFLOW non supportée : {flow.get('format_version')}"))
    if not flows_list:
        d.append(Diagnostic("ERROR", "aucun flow défini"))
    if len(nodes) != len(nodes_list):
        d.append(Diagnostic("ERROR", "ID de nœud vide ou dupliqué"))
    if len(flows) != len(flows_list):
        d.append(Diagnostic("ERROR", "ID de sous-flow vide ou dupliqué"))

    main_flow = str(flow.get("main_flow", ""))
    if not main_flow or main_flow not in flows:
        d.append(Diagnostic("ERROR", "entrée principale absente : main_flow invalide"))
    else:
        entry = str(flows[main_flow].get("entry_state", ""))
        if not entry or entry not in nodes:
            d.append(Diagnostic("ERROR", "entrée principale absente ou état d'entrée inexistant", main_flow))

    names: set[str] = set()
    for n in nodes_list:
        nid = str(n.get("id", ""))
        typ = str(n.get("type", "SCREEN")).upper()
        name = str(n.get("name", nid)).strip()
        if not nid or not C_IDENT.match(nid):
            d.append(Diagnostic("ERROR", "ID de nœud invalide pour le C (lettres/chiffres/_ uniquement)", nid or "?"))
        if not name:
            d.append(Diagnostic("ERROR", "nom de nœud vide", nid))
        key = name.upper()
        if key in names:
            d.append(Diagnostic("WARN", f"nom de nœud dupliqué : {name}", nid))
        names.add(key)
        if typ not in NODE_TYPES:
            d.append(Diagnostic("ERROR", f"type de nœud inconnu : {typ}", nid))
        fid = str(n.get("flow_id", "MAIN"))
        if fid not in flows:
            d.append(Diagnostic("ERROR", f"flow parent inexistant : {fid}", nid))
        if typ == "SUBFLOW":
            sf = str(n.get("subflow_id", ""))
            if not sf or sf not in flows:
                d.append(Diagnostic("ERROR", "SUBFLOW sans sous-flow valide", nid))
        for cb_key in ("enter_callback", "update_callback", "exit_callback"):
            cb = str(n.get(cb_key, "")).strip()
            if cb and not C_IDENT.match(cb):
                d.append(Diagnostic("ERROR", f"callback C invalide : {cb}", nid))
        mode = int(n.get("video_mode", -1) if str(n.get("video_mode", "")).strip() else -1)
        if mode not in (-1, 0, 1, 2, 3, 4):
            d.append(Diagnostic("ERROR", f"video_mode invalide : {mode}", nid))

    for f in flows_list:
        fid = str(f.get("id", ""))
        entry = str(f.get("entry_state", ""))
        if entry:
            if entry not in nodes:
                d.append(Diagnostic("ERROR", f"état d'entrée inexistant : {entry}", fid))
            elif str(nodes[entry].get("flow_id", "MAIN")) != fid:
                d.append(Diagnostic("ERROR", f"l'état d'entrée {entry} n'appartient pas au flow {fid}", fid))

    conflicts: dict[tuple[str, str, str, int], str] = {}
    outgoing: dict[str, int] = {nid: 0 for nid in nodes}
    for i, t in enumerate(transitions):
        tid = str(t.get("id", f"T{i}"))
        src = str(t.get("source", "")); dst = str(t.get("destination", ""))
        if src not in nodes:
            d.append(Diagnostic("ERROR", f"transition : source inexistante {src}", tid))
        if dst not in nodes:
            d.append(Diagnostic("ERROR", f"transition vers un état inexistant : {dst}", tid))
        if src in outgoing:
            outgoing[src] += 1
        event = str(t.get("event", "AUTO") or "AUTO").upper()
        cond = str(t.get("condition", "")).strip()
        if cond and not C_IDENT.match(cond):
            d.append(Diagnostic("ERROR", f"condition callback C invalide : {cond}", tid))
        prio = int(t.get("priority", 100))
        if prio < 0 or prio > 65535:
            d.append(Diagnostic("ERROR", f"priorité invalide : {prio}", tid))
        key = (src, event, cond, prio)
        if key in conflicts:
            d.append(Diagnostic("ERROR", f"deux transitions conflictuelles ({event}, priorité {prio})", tid))
        else:
            conflicts[key] = tid
        delay = int(t.get("delay_frames", 0))
        if delay < 0 or delay > 65535:
            d.append(Diagnostic("ERROR", f"délai invalide : {delay}", tid))
        fx = str(t.get("visual_fx", "NONE")).upper()
        if fx not in FX_NAMES:
            d.append(Diagnostic("ERROR", f"FX de transition inconnu : {fx}", tid))

    for nid, count in outgoing.items():
        if count == 0 and str(nodes[nid].get("type", "")).upper() != "SUBFLOW":
            d.append(Diagnostic("WARN", "état sans sortie", nid))

    # Reachability from main entry, treating SUBFLOW nodes as aliases to child entry.
    if main_flow in flows:
        start = str(flows[main_flow].get("entry_state", ""))
        if start in nodes:
            graph: dict[str, list[str]] = {nid: [] for nid in nodes}
            for t in transitions:
                src = str(t.get("source", "")); dst = str(t.get("destination", ""))
                if src in graph and dst in nodes:
                    graph[src].append(_effective_destination(dst, nodes, flows))
            for nid, n in nodes.items():
                if str(n.get("type", "")).upper() == "SUBFLOW":
                    eff = _effective_destination(nid, nodes, flows)
                    if eff != nid:
                        graph[nid].append(eff)
            seen: set[str] = set(); stack = [_effective_destination(start, nodes, flows)]
            while stack:
                cur = stack.pop()
                if cur in seen or cur not in nodes:
                    continue
                seen.add(cur); stack.extend(graph.get(cur, []))
            for nid in nodes:
                if str(nodes[nid].get("type", "")).upper() != "SUBFLOW" and nid not in seen:
                    d.append(Diagnostic("WARN", "état inaccessible depuis l'entrée principale", nid))

    # Internal subflow recursion check.
    sf_graph: dict[str, set[str]] = {fid: set() for fid in flows}
    for n in nodes_list:
        if str(n.get("type", "")).upper() == "SUBFLOW":
            parent = str(n.get("flow_id", "MAIN")); child = str(n.get("subflow_id", ""))
            if parent in sf_graph and child in flows:
                sf_graph[parent].add(child)
    visiting: set[str] = set(); done: set[str] = set()
    def visit(fid: str, chain: list[str]) -> None:
        if fid in visiting:
            d.append(Diagnostic("ERROR", "sous-flow récursif infini : " + " → ".join(chain + [fid]), fid)); return
        if fid in done: return
        visiting.add(fid)
        for nxt in sf_graph.get(fid, ()): visit(nxt, chain + [fid])
        visiting.remove(fid); done.add(fid)
    for fid in flows: visit(fid, [])

    # Resource validation against resources.dmsres or direct paths.
    if source_path is not None:
        base = source_path.parent
        manifest_name = normalize_path(str(flow.get("resource_manifest", "resources.dmsres")))
        manifest_path = (base / manifest_name).resolve() if manifest_name else base / "resources.dmsres"
        resources = _parse_resources_manifest(manifest_path)
        if manifest_name and not manifest_path.is_file():
            d.append(Diagnostic("WARN", f"manifeste ressources introuvable : {manifest_name}"))
        for n in nodes_list:
            nid = str(n.get("id", ""))
            for field, (expected_kind, exts) in RESOURCE_FIELDS.items():
                value = str(n.get(field, "")).strip()
                if not value: continue
                sym = c_symbol(value[1:] if value.startswith("@") else value)
                if sym.startswith("RES_"):
                    sym = sym[4:]
                r = resources.get(sym)
                if r:
                    if expected_kind != "SCENE" and r["kind"] != expected_kind:
                        d.append(Diagnostic("ERROR", f"{field}={value} est {r['kind']}, attendu {expected_kind}", nid))
                    continue
                p = Path(value)
                if not p.is_absolute(): p = (base / p).resolve()
                if not p.exists():
                    d.append(Diagnostic("ERROR", f"ressource référencée absente : {value}", nid)); continue
                if exts and p.suffix.lower() not in exts:
                    d.append(Diagnostic("WARN", f"extension inhabituelle pour {field} : {p.suffix}", nid))
        # DMAP/DCOLL compatibility when both point at manifest resource symbols.
        for n in nodes_list:
            mv = str(n.get("map", "")).strip(); cv = str(n.get("collision", "")).strip()
            if not mv or not cv: continue
            ms = c_symbol(mv[1:] if mv.startswith("@") else mv).removeprefix("RES_")
            cs = c_symbol(cv[1:] if cv.startswith("@") else cv).removeprefix("RES_")
            mr = resources.get(ms); cr = resources.get(cs)
            if mr and cr and cr.get("options", {}).get("MAP"):
                linked = c_symbol(cr["options"]["MAP"])
                if linked != ms:
                    d.append(Diagnostic("ERROR", f"DCOLL incompatible : {cv} est lié à MAP={linked}, pas {ms}", str(n.get("id", ""))))
    return d

def _flatten_nodes(flow: dict[str, Any]) -> list[dict[str, Any]]:
    """Runtime nodes: SUBFLOW containers are removed; destinations resolve to child entries."""
    return [n for n in flow.get("nodes", []) if str(n.get("type", "")).upper() != "SUBFLOW"]

def _event_names(flow: dict[str, Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for t in flow.get("transitions", []):
        ev = str(t.get("event", "AUTO") or "AUTO").upper().strip()
        if ev in ("AUTO", "ALWAYS", "TOUJOURS", ""):
            continue
        sym = c_symbol(ev)
        if sym not in seen:
            seen.add(sym); names.append(sym)
    return names

def _res_macro(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    if raw.startswith("@"):
        raw = raw[1:]
    sym = c_symbol(raw)
    return sym if sym.startswith("RES_") else "RES_" + sym

def _c_string(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\"').replace("\n", "\\n").replace("\r", "")

def _fx_lines(fx: str, duration: int, indent: str = "    ") -> list[str]:
    fx = str(fx or "NONE").upper(); duration = max(1, int(duration or 16))
    if fx == "FADE_IN": return [f"{indent}FX_fadeIn(0xFFu, {duration}u);"]
    if fx == "FADE_OUT": return [f"{indent}FX_fadeOut(0xFFu, {duration}u);"]
    if fx == "FLASH": return [f"{indent}FX_flash(0x1FFu, 0xFFu, {duration}u);"]
    if fx == "SHAKE": return [f"{indent}FX_shake(4u, {duration}u, 10u);"]
    return []

def _state_enter_lines(n: dict[str, Any], res_macro=_res_macro) -> list[str]:
    lines: list[str] = []
    sc = res_macro(str(n.get("scene", "")))
    mode = int(n.get("video_mode", -1) if str(n.get("video_mode", "")).strip() else -1)
    if sc:
        lines.append(f"    (void)SCENE_start({sc});")
    elif mode >= 0:
        lines.append(f"    VDP_setMode({mode}u);")
    mp = res_macro(str(n.get("map", "")))
    co = res_macro(str(n.get("collision", "")))
    ac = res_macro(str(n.get("actor", "")))
    mu = res_macro(str(n.get("music", "")))
    if mp and not sc: lines.append(f"    BG_loadMap({mp});")
    if co and not sc: lines.append(f"    COLL_bind({co});")
    if ac and not sc: lines.append(f"    (void)ACTOR_spawn({ac}, {int(n.get('actor_x', 152))}, {int(n.get('actor_y', 96))});")
    if mu: lines.append(f"    MUS_play({mu});")
    lines.extend(_fx_lines(str(n.get("enter_fx", "NONE")), int(n.get("enter_fx_duration", 16))))
    cb = str(n.get("enter_callback", "")).strip()
    if cb: lines.append(f"    {cb}();")
    return lines or ["    /* aucune action ENTER automatique */"]

def _state_update_lines(n: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if str(n.get("scene", "")).strip(): lines.append("    SCENE_update();")
    cb = str(n.get("update_callback", "")).strip()
    if cb: lines.append(f"    {cb}();")
    return lines or ["    /* UPDATE vide : le flow reste non bloquant */"]

def _state_exit_lines(n: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if str(n.get("scene", "")).strip(): lines.append("    SCENE_stop();")
    lines.extend(_fx_lines(str(n.get("exit_fx", "NONE")), int(n.get("exit_fx_duration", 16))))
    if bool(n.get("stop_music_on_exit", False)): lines.append("    MUS_stop();")
    cb = str(n.get("exit_callback", "")).strip()
    if cb: lines.append(f"    {cb}();")
    return lines or ["    /* aucune action EXIT automatique */"]

def compile_flow(flow: dict[str, Any], out_dir: Path, source_path: Path | None = None,
                 stem: str = "game_flow") -> dict[str, Path]:
    diagnostics = validate_flow(flow, source_path)
    errors = [x for x in diagnostics if x.severity == "ERROR"]
    if errors:
        raise FlowError("; ".join((f"{x.item}: " if x.item else "") + x.message for x in errors))
    out_dir.mkdir(parents=True, exist_ok=True)
    nodes_all = _node_index(flow); flows = _flow_index(flow)
    nodes = _flatten_nodes(flow)
    runtime_ids = {str(n["id"]): i for i, n in enumerate(nodes)}
    main_entry = str(flows[str(flow["main_flow"])].get("entry_state", ""))
    main_entry = _effective_destination(main_entry, nodes_all, flows)
    if main_entry not in runtime_ids:
        raise FlowError("l'entrée principale ne mène pas à un état runtime")

    events = _event_names(flow)
    event_ids = {ev: stable_event_id(ev) for ev in events}
    if len(set(event_ids.values())) != len(event_ids):
        collisions={}
        for name,eid in event_ids.items(): collisions.setdefault(eid,[]).append(name)
        bad=[names for names in collisions.values() if len(names)>1]
        raise FlowError("collision CRC16 événement: " + "; ".join("/".join(names) for names in bad))
    state_enum = {str(n["id"]): c_symbol(str(n["id"]), "FLOW_") for n in nodes}

    # Resolve transitions, dropping any impossible SUBFLOW source container.
    runtime_transitions: list[dict[str, Any]] = []
    for t in flow.get("transitions", []):
        src = str(t.get("source", "")); dst = str(t.get("destination", ""))
        if src not in runtime_ids:
            # SUBFLOW containers are organisational aliases, not runtime states.
            continue
        dst_eff = _effective_destination(dst, nodes_all, flows)
        if dst_eff not in runtime_ids:
            continue
        q = dict(t); q["destination_effective"] = dst_eff
        runtime_transitions.append(q)
    runtime_transitions.sort(key=lambda t: (runtime_ids[str(t["source"])], int(t.get("priority", 100)), str(t.get("id", ""))))

    callbacks: set[str] = set()
    conditions: set[str] = set()
    for n in nodes:
        callbacks.update(str(n.get(k, "")).strip() for k in ("enter_callback", "update_callback", "exit_callback") if str(n.get(k, "")).strip())
    for t in runtime_transitions:
        c = str(t.get("condition", "")).strip()
        if c: conditions.add(c)
    needs_resources = any(str(n.get(k, "")).strip() for n in nodes for k in ("scene", "map", "collision", "actor", "music"))

    resource_table: dict[str, dict[str, Any]] = {}
    resource_by_path: dict[Path, str] = {}
    if source_path is not None:
        manifest_name = normalize_path(str(flow.get("resource_manifest", "resources.dmsres")))
        mp = (source_path.parent / manifest_name).resolve()
        resource_table = _parse_resources_manifest(mp)
        for rsym, rec in resource_table.items():
            rp = Path(str(rec.get("path", "")))
            if not rp.is_absolute(): rp = (mp.parent / rp).resolve()
            resource_by_path[rp] = rsym
    def resolve_res_macro(value: str) -> str:
        raw = str(value or "").strip()
        if not raw: return ""
        sym = c_symbol(raw[1:] if raw.startswith("@") else raw).removeprefix("RES_")
        if sym in resource_table: return "RES_" + sym
        if source_path is not None:
            rp = Path(raw)
            if not rp.is_absolute(): rp = (source_path.parent / rp).resolve()
            if rp in resource_by_path: return "RES_" + resource_by_path[rp]
        return _res_macro(raw)

    h_guard = c_symbol(stem, "DMS_") + "_H"
    h: list[str] = [f"#ifndef {h_guard}", f"#define {h_guard}", "", "#include <stdint.h>", "#include <dms_flow.h>", ""]
    h += ["typedef enum {"]
    for n in nodes:
        h.append(f"    {state_enum[str(n['id'])]} = {runtime_ids[str(n['id'])]},")
    h += [f"    FLOW_STATE_COUNT = {len(nodes)}", "} GameFlowState;", "", "typedef enum {", "    FLOW_EVENT_AUTO = 0,"]
    for ev, eid in event_ids.items():
        h.append(f"    FLOW_EVENT_{ev} = {eid},")
    h += ["} GameFlowEvent;", "", "extern const DmsFlowDefinition dms_flow_definition;", "", "#endif", ""]

    c: list[str] = [f'#include "{stem}.h"', "#include <dms1.h>"]
    if needs_resources: c.append('#include "resources.h"')
    c.append("")
    for cb in sorted(callbacks): c.append(f"extern void {cb}(void);")
    for cond in sorted(conditions): c.append(f"extern uint8_t {cond}(void);")
    if callbacks or conditions: c.append("")

    for n in nodes:
        ns = c_symbol(str(n["id"])).lower()
        c += [f"static void dflow_enter_{ns}(void)", "{"] + _state_enter_lines(n, resolve_res_macro) + ["}", "", f"static void dflow_update_{ns}(void)", "{"] + _state_update_lines(n) + ["}", "", f"static void dflow_exit_{ns}(void)", "{"] + _state_exit_lines(n) + ["}", ""]

    # Transition FX callbacks.
    fx_fn_by_index: dict[int, str] = {}
    for idx, t in enumerate(runtime_transitions):
        fx = str(t.get("visual_fx", "NONE")).upper()
        if fx == "NONE": continue
        fn = f"dflow_transition_fx_{idx}"
        fx_fn_by_index[idx] = fn
        c += [f"static void {fn}(void)", "{"] + _fx_lines(fx, int(t.get("fx_duration", t.get("delay_frames", 16) or 16))) + ["}", ""]

    c += ["static const DmsFlowStateDef dflow_states[] = {"]
    type_codes = {"SCREEN": 0, "MENU": 1, "GAME": 2, "CUTSCENE": 3}
    for n in nodes:
        ns = c_symbol(str(n["id"])).lower(); typ = type_codes.get(str(n.get("type", "SCREEN")).upper(), 0)
        c.append(f'    {{"{_c_string(str(n.get("name", n["id"])))}", {typ}u, dflow_enter_{ns}, dflow_update_{ns}, dflow_exit_{ns}}},')
    c += ["};", ""]
    if runtime_transitions:
        c += ["static const DmsFlowTransitionDef dflow_transitions[] = {"]
        for idx, t in enumerate(runtime_transitions):
            src = runtime_ids[str(t["source"])]; dst = runtime_ids[str(t["destination_effective"])]
            ev = str(t.get("event", "AUTO") or "AUTO").upper().strip()
            ev_id = 0 if ev in ("AUTO", "ALWAYS", "TOUJOURS", "") else event_ids[c_symbol(ev)]
            cond = str(t.get("condition", "")).strip() or "0"
            fxfn = fx_fn_by_index.get(idx, "0")
            c.append(f"    {{{src}u, {dst}u, {ev_id}u, {int(t.get('delay_frames', 0))}u, {int(t.get('priority', 100))}u, {cond}, {fxfn}}},")
        c += ["};", ""]
    else:
        c += ["static const DmsFlowTransitionDef dflow_transitions[1] = {", "    {0u, 0u, 0u, 0u, 0u, 0, 0}", "};", ""]
    c += ["const DmsFlowDefinition dms_flow_definition = {", f"    dflow_states, {len(nodes)}u,", f"    dflow_transitions, {len(runtime_transitions)}u,", f"    {runtime_ids[main_entry]}u", "};", ""]

    h_path = out_dir / f"{stem}.h"; c_path = out_dir / f"{stem}.c"
    h_path.write_text("\n".join(h), encoding="utf-8")
    c_path.write_text("\n".join(c), encoding="utf-8")

    # Compact binary: DFLW + v1 + counts + entry, state records, transition records.
    blob = bytearray(b"DFLW")
    blob += struct.pack(">HHHH", FORMAT_VERSION, len(nodes), len(runtime_transitions), runtime_ids[main_entry])
    for n in nodes:
        name_hash = sum((i + 1) * b for i, b in enumerate(str(n.get("id", "")).encode("utf-8"))) & 0xFFFF
        typ = type_codes.get(str(n.get("type", "SCREEN")).upper(), 0)
        mode = int(n.get("video_mode", -1) if str(n.get("video_mode", "")).strip() else -1)
        blob += struct.pack(">HBB", name_hash, typ & 0xFF, 0xFF if mode < 0 else mode & 0xFF)
    for t in runtime_transitions:
        src = runtime_ids[str(t["source"])]; dst = runtime_ids[str(t["destination_effective"])]
        ev = str(t.get("event", "AUTO") or "AUTO").upper().strip(); ev_id = 0 if ev in ("AUTO", "ALWAYS", "TOUJOURS", "") else event_ids[c_symbol(ev)]
        blob += struct.pack(">HHHHH", src, dst, ev_id, int(t.get("delay_frames", 0)) & 0xFFFF, int(t.get("priority", 100)) & 0xFFFF)
    bin_path = out_dir / f"{stem}_data.bin"; bin_path.write_bytes(blob)

    manifest = {
        "format": "DFLOW-COMPILED",
        "format_version": 1,
        "source": normalize_path(os.path.relpath(source_path.resolve(), out_dir.resolve())) if source_path else "",
        "states": [{"id": n["id"], "runtime_id": runtime_ids[str(n["id"])], "type": n.get("type", "SCREEN")} for n in nodes],
        "events": {"AUTO": 0, **event_ids},
        "transitions": [{"source": t["source"], "destination": t["destination_effective"], "event": t.get("event", "AUTO"), "delay_frames": int(t.get("delay_frames", 0)), "priority": int(t.get("priority", 100))} for t in runtime_transitions],
        "entry": main_entry,
        "diagnostics": [x.as_dict() for x in diagnostics],
    }
    mf_path = out_dir / f"{stem}_manifest.json"; mf_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"h": h_path, "c": c_path, "bin": bin_path, "manifest": mf_path}

def format_diagnostics(items: Iterable[Diagnostic]) -> str:
    rows = []
    for x in items:
        prefix = "ERREUR" if x.severity == "ERROR" else "ATTENTION" if x.severity == "WARN" else x.severity
        where = f" [{x.item}]" if x.item else ""
        rows.append(f"{prefix}{where} : {x.message}")
    return "\n".join(rows) if rows else "PASS : aucune erreur ni avertissement."

def main() -> int:
    ap = argparse.ArgumentParser(description="Compilateur DMS Game Flow DFLOW V1")
    ap.add_argument("flow", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--stem", default="game_flow")
    ap.add_argument("--validate-only", action="store_true")
    args = ap.parse_args()
    try:
        source = args.flow.resolve(); flow = load_flow(source)
        diagnostics = validate_flow(flow, source)
        print(format_diagnostics(diagnostics))
        if any(x.severity == "ERROR" for x in diagnostics): return 2
        if not args.validate_only:
            out = (args.out or source.parent / "generated").resolve()
            paths = compile_flow(flow, out, source, args.stem)
            print("DFLOW EXPORT :", ", ".join(str(p) for p in paths.values()))
        return 0
    except Exception as exc:
        print("ERREUR DFLOW :", exc)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
