from __future__ import annotations

import json
import math
import os
import re
import struct
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

APP_NAME = "DMS Asset Lab"
APP_VERSION = "0.3.0"
PROJECT_VERSION = 2
DRES_VERSION = 3

DMS_MODES = {
    "Mode 0 - STANDARD": {
        "id": 0, "w": 320, "h": 224, "palettes": 4,
        "sprites_total": 80, "sprites_scanline": 20,
        "description": "BG A + BG B • 64 couleurs simultanées",
    },
    "Mode 1 - HIGH COLOR": {
        "id": 1, "w": 320, "h": 224, "palettes": 8,
        "sprites_total": 80, "sprites_scanline": 20,
        "description": "BG A • 128 couleurs simultanées",
    },
    "Mode 2 - SCROLL": {
        "id": 2, "w": 320, "h": 224, "palettes": 4,
        "sprites_total": 48, "sprites_scanline": 12,
        "description": "BG A + BG B • line-scroll A/B",
    },
    "Mode 3 - SPRITE": {
        "id": 3, "w": 320, "h": 224, "palettes": 4,
        "sprites_total": 128, "sprites_scanline": 32,
        "description": "BG A • budget sprites renforcé",
    },
    "Mode 4 - LOW RES": {
        "id": 4, "w": 256, "h": 224, "palettes": 8,
        "sprites_total": 96, "sprites_scanline": 24,
        "description": "256×224 natif • BG A + BG B • 128 couleurs",
    },
}

PALETTE_OVERLAY_COLORS = [
    "#56b4e9", "#e69f00", "#009e73", "#cc79a7",
    "#f0e442", "#0072b2", "#d55e00", "#9b59b6",
]


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def natural_key(text):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(text))]


def safe_symbol(text):
    s = "".join(c if c.isalnum() else "_" for c in text.upper())
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "DMS_RESOURCE"


def rgb888_to_rgb333(rgb):
    r, g, b = rgb
    return (
        int(round(r * 7 / 255)),
        int(round(g * 7 / 255)),
        int(round(b * 7 / 255)),
    )


def rgb333_to_rgb888(rgb):
    r, g, b = rgb
    return (
        int(round(r * 255 / 7)),
        int(round(g * 255 / 7)),
        int(round(b * 255 / 7)),
    )


def rgb333_word(rgb):
    r, g, b = rgb
    return ((r & 7) << 6) | ((g & 7) << 3) | (b & 7)


def rgb333_hex(rgb):
    return f"{rgb333_word(rgb):03X}"


def rgb_dist_sq(a, b):
    return sum((a[i] - b[i]) ** 2 for i in range(3))


def nearest_palette_color(color, palette):
    if not palette:
        return (0, 0, 0)
    return min(palette, key=lambda p: rgb_dist_sq(color, p))


@dataclass
class Hitbox:
    enabled: bool = False
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0


@dataclass
class PixelFrame:
    name: str
    width: int
    height: int
    pixels: list
    duration_ms: int = 120
    source: str = ""
    source_rect: tuple | None = None
    animation: str = "IDLE"
    pivot_x: int | None = None
    pivot_y: int | None = None
    preferred_palette: int = -1
    hitbox: Hitbox = field(default_factory=Hitbox)
    cell_palette_overrides: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.pivot_x is None:
            self.pivot_x = self.width // 2
        if self.pivot_y is None:
            self.pivot_y = self.height - 1


@dataclass
class TileInfo:
    frame_index: int
    cx: int
    cy: int
    tx: int
    ty: int
    w: int
    h: int
    colors: set = field(default_factory=set)
    palette_id: int | None = None
    forced_palette: int = -1
    override_source: str = "AUTO"
    empty: bool = False
    canonical_id: int | None = None
    flip_x: bool = False
    flip_y: bool = False


@dataclass
class FrameMetrics:
    frame_index: int
    active_bounds: tuple | None
    nonempty_cells: int
    max_scanline_cells: int
    source_colors: int
    rgb333_colors: int
    transparent_pixels: int


@dataclass
class Issue:
    severity: str
    scope: str
    message: str


@dataclass
class Analysis:
    frames: list
    tiles: list
    palettes: list
    palette_locked: list
    unique_tiles: list
    issues: list
    frame_metrics: list
    total_cells: int
    nonempty_cells: int
    duplicate_cells: int
    flip_reused_cells: int
    vram_bytes: int
    max_scanline_cells: int
    max_frame_cells: int
    source_colors: int
    rgb333_colors: int
    rgb333_collisions: int
    collision_map: dict

    @property
    def warnings(self):
        return [i.message for i in self.issues if i.severity in ("WARN", "ERROR")]

    def issue_counts(self):
        return {
            sev: sum(1 for i in self.issues if i.severity == sev)
            for sev in ("ERROR", "WARN", "INFO")
        }


class PNGLoader:
    """Tk-native PNG IO. No pip package is required."""

    def __init__(self, master):
        self.master = master
        self._keepalive = []

    def load_file(self, path: str) -> PixelFrame:
        img = tk.PhotoImage(master=self.master, file=path)
        self._keepalive.append(img)
        w, h = img.width(), img.height()
        pixels = []
        transparency_supported = hasattr(img, "transparency_get")
        for y in range(h):
            row = []
            for x in range(w):
                c = img.get(x, y)
                if isinstance(c, str):
                    if c.startswith("#") and len(c) >= 7:
                        rgb = tuple(int(c[i:i+2], 16) for i in (1, 3, 5))
                    else:
                        parts = [int(v) for v in c.split()]
                        rgb = tuple(parts[:3])
                else:
                    rgb = tuple(int(v) for v in c[:3])
                alpha = 255
                if transparency_supported:
                    try:
                        if img.transparency_get(x, y):
                            alpha = 0
                    except tk.TclError:
                        pass
                row.append((rgb[0], rgb[1], rgb[2], alpha))
            pixels.append(row)
        return PixelFrame(
            Path(path).stem, w, h, pixels, source=path,
            pivot_x=w // 2, pivot_y=h - 1,
        )

    @staticmethod
    def crop_frame(frame: PixelFrame, x0, y0, fw, fh, name):
        px = [row[x0:x0+fw] for row in frame.pixels[y0:y0+fh]]
        return PixelFrame(
            name, fw, fh, px,
            source=frame.source,
            source_rect=(x0, y0, fw, fh),
            pivot_x=fw // 2, pivot_y=fh - 1,
        )

    @classmethod
    def slice_sheet(cls, frame: PixelFrame, fw, fh, margin_x=0, margin_y=0, spacing_x=0, spacing_y=0):
        out = []
        idx = 0
        y0 = margin_y
        while y0 + fh <= frame.height:
            x0 = margin_x
            while x0 + fw <= frame.width:
                out.append(cls.crop_frame(frame, x0, y0, fw, fh, f"{frame.name}_{idx:02d}"))
                idx += 1
                x0 += fw + spacing_x
            y0 += fh + spacing_y
        return out


def active_bounds(frame: PixelFrame):
    xs, ys = [], []
    transparent = 0
    for y, row in enumerate(frame.pixels):
        for x, (_r, _g, _b, a) in enumerate(row):
            if a:
                xs.append(x)
                ys.append(y)
            else:
                transparent += 1
    if not xs:
        return None, transparent
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1), transparent


def tile_variant(tile_pixels, flip_x=False, flip_y=False):
    rows = tile_pixels[::-1] if flip_y else tile_pixels
    if flip_x:
        rows = [row[::-1] for row in rows]
    return tuple(tuple(row) for row in rows)


def normalize_palette_locks(palette_locks, max_palettes, capacity):
    normalized = {}
    issues = []
    for key, colors in (palette_locks or {}).items():
        try:
            pi = int(key)
        except Exception:
            continue
        if pi < 0 or pi >= max_palettes:
            issues.append(Issue("WARN", "PALETTE", f"Palette verrouillée P{pi} ignorée dans ce mode."))
            continue
        unique = []
        seen = set()
        for c in colors:
            c = tuple(int(v) for v in c)
            if c not in seen:
                seen.add(c)
                unique.append(c)
        if len(unique) > capacity:
            issues.append(Issue(
                "ERROR", f"P{pi}",
                f"Palette verrouillée P{pi}: {len(unique)} couleurs opaques > limite {capacity}."
            ))
        normalized[pi] = unique[:capacity]
    return normalized, issues


def pack_palettes(tile_color_sets, forced_palettes, override_sources, max_palettes, capacity, palette_locks=None):
    locks, issues = normalize_palette_locks(palette_locks, max_palettes, capacity)
    palettes = [set(locks.get(i, [])) for i in range(max_palettes)]
    locked = [i in locks for i in range(max_palettes)]
    used = [bool(palettes[i]) or locked[i] for i in range(max_palettes)]
    assignment = [None] * len(tile_color_sets)

    # Forced cells first: per-cell override or frame preference.
    for i, colors in enumerate(tile_color_sets):
        if not colors:
            continue
        forced = forced_palettes[i]
        if forced is None or forced < 0:
            continue
        scope = f"CELL {i}"
        src = override_sources[i]
        if forced >= max_palettes:
            issues.append(Issue("ERROR", scope, f"{src}: P{forced} indisponible dans ce mode."))
            continue
        if len(colors) > capacity:
            issues.append(Issue(
                "ERROR", scope,
                f"{src}: cellule à {len(colors)} couleurs RGB333 > limite {capacity}."
            ))
            continue
        if locked[forced]:
            missing = colors - palettes[forced]
            if missing:
                issues.append(Issue(
                    "ERROR", scope,
                    f"{src}: P{forced} est verrouillée et il manque {len(missing)} couleur(s) à cette cellule."
                ))
                continue
            assignment[i] = forced
            used[forced] = True
        else:
            union = palettes[forced] | colors
            if len(union) > capacity:
                issues.append(Issue(
                    "ERROR", scope,
                    f"{src}: P{forced} demanderait {len(union)} couleurs opaques > {capacity}."
                ))
                continue
            palettes[forced] = union
            used[forced] = True
            assignment[i] = forced

    # Auto cells, largest color sets first.
    pending = [
        (i, colors) for i, colors in enumerate(tile_color_sets)
        if colors and assignment[i] is None and (forced_palettes[i] is None or forced_palettes[i] < 0)
    ]
    pending.sort(key=lambda it: len(it[1]), reverse=True)

    for i, colors in pending:
        if len(colors) > capacity:
            issues.append(Issue(
                "ERROR", f"CELL {i}",
                f"Cellule à {len(colors)} couleurs RGB333 > limite {capacity}."
            ))
            continue

        candidates = []
        for pi in range(max_palettes):
            if locked[pi]:
                if colors.issubset(palettes[pi]):
                    candidates.append((0, 0, pi))
            elif used[pi]:
                union = palettes[pi] | colors
                if len(union) <= capacity:
                    growth = len(union) - len(palettes[pi])
                    candidates.append((growth, len(palettes[pi]), pi))

        if candidates:
            candidates.sort()
            pi = candidates[0][2]
            if not locked[pi]:
                palettes[pi] |= colors
            assignment[i] = pi
            used[pi] = True
            continue

        free = next((pi for pi in range(max_palettes) if not used[pi] and not locked[pi]), None)
        if free is None:
            issues.append(Issue(
                "ERROR", f"CELL {i}",
                f"Aucune des {max_palettes} palettes ne peut accueillir cette cellule sans remappage."
            ))
        else:
            palettes[free] = set(colors)
            used[free] = True
            assignment[i] = free

    ordered = [sorted(p, key=lambda c: (sum(c), rgb333_word(c))) for p in palettes]
    return ordered, locked, assignment, issues


def analyze_frames(frames, mode_name, cell_size=8, reserve_transparent=True, palette_locks=None):
    mode = DMS_MODES[mode_name]
    capacity = 15 if reserve_transparent else 16

    tiles = []
    color_sets = []
    pixel_sets = []
    forced_palettes = []
    override_sources = []
    issues = []
    frame_metrics = []
    global_source = set()
    global_q = set()
    q_sources = {}

    for fi, frame in enumerate(frames):
        fsource, fq = set(), set()
        bounds, transparent = active_bounds(frame)
        frame_tile_start = len(tiles)

        for row in frame.pixels:
            for r, g, b, a in row:
                if not a:
                    continue
                src = (r, g, b)
                q = rgb888_to_rgb333(src)
                fsource.add(src)
                fq.add(q)
                global_source.add(src)
                global_q.add(q)
                q_sources.setdefault(q, set()).add(src)

        for cy, ty in enumerate(range(0, frame.height, cell_size)):
            for cx, tx in enumerate(range(0, frame.width, cell_size)):
                tw = min(cell_size, frame.width - tx)
                th = min(cell_size, frame.height - ty)
                colors = set()
                rows = []
                for y in range(ty, ty + th):
                    rr = []
                    for x in range(tx, tx + tw):
                        r, g, b, a = frame.pixels[y][x]
                        if a == 0 and reserve_transparent:
                            rr.append(None)
                        else:
                            c = rgb888_to_rgb333((r, g, b))
                            colors.add(c)
                            rr.append(c)
                    rr += [None] * (cell_size - len(rr))
                    rows.append(rr)
                while len(rows) < cell_size:
                    rows.append([None] * cell_size)

                key = f"{cx},{cy}"
                if key in frame.cell_palette_overrides:
                    forced = int(frame.cell_palette_overrides[key])
                    source = f"override cellule {key}"
                else:
                    forced = int(frame.preferred_palette)
                    source = "palette frame" if forced >= 0 else "AUTO"

                tile = TileInfo(
                    fi, cx, cy, tx, ty, tw, th,
                    colors=colors,
                    empty=not colors,
                    forced_palette=forced,
                    override_source=source,
                )
                tiles.append(tile)
                color_sets.append(colors)
                pixel_sets.append(rows)
                forced_palettes.append(forced)
                override_sources.append(source)

        ftiles = [t for t in tiles[frame_tile_start:] if not t.empty]
        fscan = 0
        for y in range(frame.height):
            fscan = max(fscan, sum(1 for t in ftiles if t.ty <= y < t.ty + t.h))

        frame_metrics.append(FrameMetrics(
            fi, bounds, len(ftiles), fscan, len(fsource), len(fq), transparent
        ))

        if frame.hitbox.enabled:
            hb = frame.hitbox
            if hb.w <= 0 or hb.h <= 0:
                issues.append(Issue("WARN", frame.name, "Hitbox active mais vide."))
            if hb.x < 0 or hb.y < 0 or hb.x + hb.w > frame.width or hb.y + hb.h > frame.height:
                issues.append(Issue("WARN", frame.name, "Hitbox dépasse le canvas de la frame."))

        if frame.pivot_x is not None and frame.pivot_y is not None:
            if not (-frame.width <= frame.pivot_x <= frame.width * 2 and -frame.height <= frame.pivot_y <= frame.height * 2):
                issues.append(Issue("WARN", frame.name, "Pivot très éloigné du canvas ; vérifie sa position."))

    palettes, palette_locked, assignment, palette_issues = pack_palettes(
        color_sets, forced_palettes, override_sources,
        mode["palettes"], capacity, palette_locks,
    )
    issues.extend(palette_issues)

    unique_tiles = []
    canonical = {}
    duplicates = 0
    flip_reused = 0

    for i, (tile, px) in enumerate(zip(tiles, pixel_sets)):
        tile.palette_id = assignment[i]
        if tile.empty:
            continue

        if tile.palette_id is None or tile.palette_id >= len(palettes):
            # Diagnostic fallback only. Export readiness remains ERROR.
            pal = sorted(tile.colors, key=rgb333_word)[:capacity]
        else:
            pal = palettes[tile.palette_id]

        offset = 1 if reserve_transparent else 0
        local = []
        for row in px:
            lr = []
            for c in row:
                if c is None and reserve_transparent:
                    lr.append(0)
                elif c in pal:
                    lr.append(pal.index(c) + offset)
                else:
                    nc = nearest_palette_color(c, pal)
                    lr.append(pal.index(nc) + offset if pal else 0)
            local.append(lr)

        variants = [
            (tile_variant(local), (False, False)),
            (tile_variant(local, True, False), (True, False)),
            (tile_variant(local, False, True), (False, True)),
            (tile_variant(local, True, True), (True, True)),
        ]
        hit = next(((canonical[v], flags) for v, flags in variants if v in canonical), None)
        if hit:
            tile.canonical_id = hit[0]
            tile.flip_x, tile.flip_y = hit[1]
            duplicates += 1
            if hit[1] != (False, False):
                flip_reused += 1
        else:
            uid = len(unique_tiles)
            normal = variants[0][0]
            unique_tiles.append(normal)
            tile.canonical_id = uid
            for v, _flags in variants:
                canonical[v] = uid

    vram = len(unique_tiles) * math.ceil(cell_size * cell_size / 2)
    max_frame = max((m.nonempty_cells for m in frame_metrics), default=0)
    max_scan = max((m.max_scanline_cells for m in frame_metrics), default=0)

    # Hardware sprite object geometry is intentionally not invented.
    if max_frame > mode["sprites_total"]:
        issues.append(Issue(
            "WARN", "SPRITE PROXY",
            f"{max_frame} blocs actifs/frame > budget {mode['sprites_total']} objets. "
            "C'est un proxy conservateur tant que la géométrie sprite DMS-1 n'est pas figée."
        ))
    if max_scan > mode["sprites_scanline"]:
        issues.append(Issue(
            "WARN", "SCANLINE PROXY",
            f"{max_scan} blocs/scanline > budget {mode['sprites_scanline']} objets. "
            "C'est un proxy conservateur tant que la géométrie sprite DMS-1 n'est pas figée."
        ))

    collision_map = {
        rgb333_hex(q): sorted(list(srcs))
        for q, srcs in q_sources.items() if len(srcs) > 1
    }
    collisions = sum(max(0, len(srcs) - 1) for srcs in q_sources.values())
    if collisions:
        issues.append(Issue(
            "INFO", "RGB333",
            f"{collisions} collision(s) de quantification : plusieurs couleurs source convergent vers le même RGB333."
        ))

    # Overrides that target a cell that does not exist with current cell size.
    for fi, frame in enumerate(frames):
        valid = {f"{t.cx},{t.cy}" for t in tiles if t.frame_index == fi}
        stale = [k for k in frame.cell_palette_overrides if k not in valid]
        if stale:
            issues.append(Issue(
                "WARN", frame.name,
                f"{len(stale)} override(s) de cellule ne correspondent plus à la grille actuelle."
            ))

    return Analysis(
        frames, tiles, palettes, palette_locked, unique_tiles, issues, frame_metrics,
        len(tiles), sum(1 for t in tiles if not t.empty),
        duplicates, flip_reused, vram, max_scan, max_frame,
        len(global_source), len(global_q), collisions, collision_map,
    )


def pack_4bpp(tile):
    vals = [v & 0xF for row in tile for v in row]
    if len(vals) % 2:
        vals.append(0)
    out = bytearray()
    for i in range(0, len(vals), 2):
        out.append((vals[i] << 4) | vals[i+1])
    return bytes(out)


def animation_map(frames):
    out = {}
    for i, f in enumerate(frames):
        out.setdefault(f.animation or "UNNAMED", []).append(i)
    return out


def animation_descriptors(frames):
    result = {}
    for name, ids in animation_map(frames).items():
        result[name] = {
            "frames": ids,
            "count": len(ids),
            "total_duration_ms": sum(frames[i].duration_ms for i in ids),
        }
    return result


def tile_usage_counts(analysis):
    counts = {}
    for t in analysis.tiles:
        if t.canonical_id is not None:
            counts[t.canonical_id] = counts.get(t.canonical_id, 0) + 1
    return counts


def build_manifest(analysis, mode_name, cell_size, reserve_transparent, project_name, palette_locks):
    return {
        "format": "DRES",
        "format_version": DRES_VERSION,
        "generator": f"{APP_NAME} {APP_VERSION}",
        "project": project_name,
        "video_mode": {"name": mode_name, **DMS_MODES[mode_name]},
        "rgb_format": "RGB333",
        "bpp": 4,
        "analysis_cell_size": cell_size,
        "analysis_cell_is_not_yet_a_frozen_sprite_geometry": True,
        "reserve_palette_index_0_for_transparency": reserve_transparent,
        "palette_locks": {
            str(k): [list(c) for c in v] for k, v in sorted((palette_locks or {}).items())
        },
        "animations": animation_map(analysis.frames),
        "animation_descriptors": animation_descriptors(analysis.frames),
        "frames": [
            {
                "name": f.name,
                "animation": f.animation,
                "width": f.width,
                "height": f.height,
                "duration_ms": f.duration_ms,
                "pivot": [f.pivot_x, f.pivot_y],
                "preferred_palette": f.preferred_palette,
                "cell_palette_overrides": dict(f.cell_palette_overrides),
                "hitbox": asdict(f.hitbox),
                "active_bounds": (
                    list(analysis.frame_metrics[i].active_bounds)
                    if analysis.frame_metrics[i].active_bounds else None
                ),
                "source": os.path.basename(f.source) if f.source else "",
                "source_rect": list(f.source_rect) if f.source_rect else None,
            }
            for i, f in enumerate(analysis.frames)
        ],
        "palettes": [
            {
                "id": i,
                "locked": bool(analysis.palette_locked[i]),
                "colors_rgb333": [list(c) for c in pal],
                "words_hex": [rgb333_hex(c) for c in pal],
                "opaque_count": len(pal),
            }
            for i, pal in enumerate(analysis.palettes)
        ],
        "cells": [
            {
                "frame": t.frame_index,
                "cell": [t.cx, t.cy],
                "x": t.tx, "y": t.ty, "w": t.w, "h": t.h,
                "empty": t.empty,
                "palette": t.palette_id,
                "forced_palette": t.forced_palette,
                "override_source": t.override_source,
                "tile": t.canonical_id,
                "flip_x": t.flip_x, "flip_y": t.flip_y,
                "color_count": len(t.colors),
            }
            for t in analysis.tiles
        ],
        "metrics": {
            "total_cells": analysis.total_cells,
            "nonempty_cells": analysis.nonempty_cells,
            "unique_tiles": len(analysis.unique_tiles),
            "duplicate_cells": analysis.duplicate_cells,
            "flip_reused_cells": analysis.flip_reused_cells,
            "vram_bytes_estimate": analysis.vram_bytes,
            "max_analysis_blocks_per_frame": analysis.max_frame_cells,
            "max_analysis_blocks_per_scanline": analysis.max_scanline_cells,
            "source_colors": analysis.source_colors,
            "rgb333_colors": analysis.rgb333_colors,
            "rgb333_collisions": analysis.rgb333_collisions,
        },
        "issues": [asdict(i) for i in analysis.issues],
        "collision_map": analysis.collision_map,
    }


def analysis_report_text(analysis, mode_name, cell_size, reserve_transparent):
    m = DMS_MODES[mode_name]
    counts = analysis.issue_counts()
    lines = [
        "DMS ASSET LAB - HARDWARE ANALYSIS REPORT",
        "=" * 46,
        f"Generator : {APP_VERSION}",
        f"Mode : {mode_name}",
        f"Résolution : {m['w']}×{m['h']}",
        f"Palettes : {m['palettes']} × 16",
        f"Cellule analyse : {cell_size}×{cell_size}",
        f"Index 0 transparent : {'oui' if reserve_transparent else 'non'}",
        "",
        f"Frames : {len(analysis.frames)}",
        f"Animations : {len(animation_map(analysis.frames))}",
        f"Couleurs source : {analysis.source_colors}",
        f"Couleurs RGB333 : {analysis.rgb333_colors} / 512",
        f"Collisions RGB888→RGB333 : {analysis.rgb333_collisions}",
        f"Tiles/blocs uniques : {len(analysis.unique_tiles)}",
        f"Réemplois/doublons : {analysis.duplicate_cells}",
        f"Réemplois via flip : {analysis.flip_reused_cells}",
        f"VRAM estimée : {analysis.vram_bytes} octets",
        f"Validation : {counts['ERROR']} error / {counts['WARN']} warn / {counts['INFO']} info",
        "",
        "PALETTES",
        "-" * 24,
    ]
    cap = 15 if reserve_transparent else 16
    for pi, pal in enumerate(analysis.palettes):
        state = "LOCK" if analysis.palette_locked[pi] else "AUTO"
        lines.append(f"P{pi} [{state}] : {len(pal)}/{cap} - {' '.join(rgb333_hex(c) for c in pal)}")

    lines.extend(["", "PAR FRAME", "-" * 24])
    for fm in analysis.frame_metrics:
        f = analysis.frames[fm.frame_index]
        lines.extend([
            f"[{fm.frame_index:02d}] {f.name} / {f.animation}",
            f"  {f.width}×{f.height} • {f.duration_ms} ms",
            f"  couleurs : {fm.source_colors} source / {fm.rgb333_colors} RGB333",
            f"  blocs actifs : {fm.nonempty_cells} • max scanline : {fm.max_scanline_cells}",
            f"  bounds : {fm.active_bounds} • pivot : ({f.pivot_x},{f.pivot_y})",
            f"  palette frame : {'AUTO' if f.preferred_palette < 0 else 'P'+str(f.preferred_palette)}",
            f"  overrides cellule : {len(f.cell_palette_overrides)}",
            f"  hitbox : {f.hitbox.enabled} ({f.hitbox.x},{f.hitbox.y},{f.hitbox.w},{f.hitbox.h})",
            "",
        ])

    lines.extend(["ISSUES", "-" * 24])
    if analysis.issues:
        for issue in analysis.issues:
            lines.append(f"[{issue.severity}] {issue.scope} - {issue.message}")
    else:
        lines.append("PASS - aucun problème détecté.")
    return "\n".join(lines)


def export_dres(path, analysis, mode_name, cell_size, reserve_transparent, project_name, palette_locks):
    manifest = build_manifest(
        analysis, mode_name, cell_size, reserve_transparent, project_name, palette_locks
    )

    palette_words = bytearray()
    for pal in analysis.palettes:
        entries = [0] if reserve_transparent else []
        entries.extend(rgb333_word(c) for c in pal)
        entries = entries[:16]
        entries.extend([0] * (16 - len(entries)))
        for word in entries:
            palette_words += struct.pack(">H", word)

    tile_blob = b"".join(pack_4bpp(t) for t in analysis.unique_tiles)
    report = analysis_report_text(analysis, mode_name, cell_size, reserve_transparent)

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        z.writestr("tiles.bin", tile_blob)
        z.writestr("palettes.bin", bytes(palette_words))
        z.writestr("analysis_report.txt", report)
        z.writestr(
            "README.txt",
            "DMS Resource V3 generated by DMS Asset Lab.\n"
            "manifest.json = source de vérité.\n"
            "tiles.bin = données 4 bpp.\n"
            "palettes.bin = mots RGB333 big-endian.\n"
            "analysis_report.txt = rapport lisible.\n"
            "Le format inclut désormais les overrides de palette par cellule et les palettes verrouillées.\n"
        )


def generate_gdk_metadata(folder, analysis, project_name, dres_filename):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    symbol = safe_symbol(project_name)
    base = symbol.lower()

    anims = animation_map(analysis.frames)
    anim_names = list(anims.keys())

    header = [
        "#pragma once",
        "#include <stdint.h>",
        "",
        f"/* Generated by DMS Asset Lab {APP_VERSION}. Metadata handoff draft. */",
        f"#define {symbol}_FRAME_COUNT {len(analysis.frames)}",
        f"#define {symbol}_ANIMATION_COUNT {len(anims)}",
        f"#define {symbol}_UNIQUE_TILE_COUNT {len(analysis.unique_tiles)}",
        f"#define {symbol}_PALETTE_COUNT {len(analysis.palettes)}",
        "",
        "typedef struct {",
        "    uint16_t duration_ms;",
        "    int16_t pivot_x;",
        "    int16_t pivot_y;",
        "    int16_t hitbox_x;",
        "    int16_t hitbox_y;",
        "    uint16_t hitbox_w;",
        "    uint16_t hitbox_h;",
        "    uint8_t hitbox_enabled;",
        "    int8_t preferred_palette;",
        "} DMSAssetFrameMeta;",
        "",
        "typedef enum {",
    ]
    for i, f in enumerate(analysis.frames):
        header.append(f"    {symbol}_FRAME_{safe_symbol(f.name)} = {i},")
    header.extend([f"}} {symbol}_FrameId;", "", "typedef enum {"])
    for i, name in enumerate(anim_names):
        header.append(f"    {symbol}_ANIM_{safe_symbol(name)} = {i},")
    header.extend([
        f"}} {symbol}_AnimationId;",
        "",
        f"extern const DMSAssetFrameMeta {base}_frame_meta[{symbol}_FRAME_COUNT];",
        f"extern const uint16_t {base}_animation_first[{symbol}_ANIMATION_COUNT];",
        f"extern const uint16_t {base}_animation_count[{symbol}_ANIMATION_COUNT];",
        "",
        f"/* Resource container expected by future compiler: {dres_filename} */",
    ])

    c = [
        f'#include "{base}.h"',
        "",
        f"const DMSAssetFrameMeta {base}_frame_meta[{symbol}_FRAME_COUNT] = {{",
    ]
    for f in analysis.frames:
        hb = f.hitbox
        c.append(
            "    { "
            f"{f.duration_ms}, {f.pivot_x}, {f.pivot_y}, "
            f"{hb.x}, {hb.y}, {hb.w}, {hb.h}, {1 if hb.enabled else 0}, {f.preferred_palette} "
            "},"
        )
    c.append("};")
    c.append("")
    c.append(f"const uint16_t {base}_animation_first[{symbol}_ANIMATION_COUNT] = {{")
    for name in anim_names:
        c.append(f"    {anims[name][0]},")
    c.append("};")
    c.append("")
    c.append(f"const uint16_t {base}_animation_count[{symbol}_ANIMATION_COUNT] = {{")
    for name in anim_names:
        c.append(f"    {len(anims[name])},")
    c.append("};")

    (folder / f"{base}.h").write_text("\n".join(header) + "\n", encoding="utf-8")
    (folder / f"{base}_meta.c").write_text("\n".join(c) + "\n", encoding="utf-8")

    summary = {
        "generator": APP_VERSION,
        "resource": dres_filename,
        "frames": [f.name for f in analysis.frames],
        "animations": animation_descriptors(analysis.frames),
        "unique_tiles": len(analysis.unique_tiles),
        "palettes": len(analysis.palettes),
    }
    (folder / f"{base}_handoff.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )


class DMSAssetLab(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} - {APP_VERSION}")
        self.geometry("1580x940")
        self.minsize(1220, 740)
        self.configure(bg="#17191d")

        self.loader = PNGLoader(self)
        self.frames = []
        self.missing_frame_defs = []
        self.analysis = None
        self.palette_locks = {}
        self.project_file = None
        self.preview_img = None
        self.preview_scale = 1
        self.preview_origin = (0, 0)
        self.selected_cell = None
        self.drag_hitbox_start = None
        self.drag_hitbox_rect = None
        self.playing = False
        self.play_pos = 0
        self.play_after = None

        self.mode_var = tk.StringVar(value="Mode 0 - STANDARD")
        self.cell_var = tk.IntVar(value=8)
        self.transparent_var = tk.BooleanVar(value=True)
        self.project_var = tk.StringVar(value="DMS_CHARACTER")

        self.sheet_fw_var = tk.IntVar(value=64)
        self.sheet_fh_var = tk.IntVar(value=64)
        self.sheet_margin_x_var = tk.IntVar(value=0)
        self.sheet_margin_y_var = tk.IntVar(value=0)
        self.sheet_spacing_x_var = tk.IntVar(value=0)
        self.sheet_spacing_y_var = tk.IntVar(value=0)

        self.animation_filter_var = tk.StringVar(value="ALL")
        self.zoom_var = tk.StringVar(value="Fit")
        self.quantized_preview_var = tk.BooleanVar(value=True)
        self.grid_var = tk.BooleanVar(value=False)
        self.cell_overlay_var = tk.BooleanVar(value=True)
        self.palette_map_var = tk.BooleanVar(value=False)
        self.bounds_var = tk.BooleanVar(value=True)
        self.align_pivot_var = tk.BooleanVar(value=True)
        self.loop_var = tk.BooleanVar(value=True)
        self.play_speed_var = tk.StringVar(value="1×")
        self.interaction_var = tk.StringVar(value="INSPECT")

        self.frame_name_var = tk.StringVar()
        self.frame_anim_var = tk.StringVar(value="IDLE")
        self.duration_var = tk.IntVar(value=120)
        self.pivot_x_var = tk.IntVar(value=0)
        self.pivot_y_var = tk.IntVar(value=0)
        self.palette_pref_var = tk.StringVar(value="AUTO")
        self.hitbox_enabled_var = tk.BooleanVar(value=False)
        self.hitbox_x_var = tk.IntVar(value=0)
        self.hitbox_y_var = tk.IntVar(value=0)
        self.hitbox_w_var = tk.IntVar(value=0)
        self.hitbox_h_var = tk.IntVar(value=0)

        self.batch_anim_var = tk.StringVar(value="WALK")
        self.batch_duration_var = tk.IntVar(value=120)
        self.batch_palette_var = tk.StringVar(value="UNCHANGED")

        self.cell_override_var = tk.StringVar(value="AUTO")
        self.lock_palette_var = tk.StringVar(value="P0")

        self._style()
        self._build_ui()
        self._bind_shortcuts()
        self._update_mode_banner()
        self._refresh_animation_filter()
        self._empty_diagnostics()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=("Segoe UI", 9))
        style.configure("TFrame", background="#17191d")
        style.configure("TLabelframe", background="#17191d", foreground="#ececec")
        style.configure("TLabelframe.Label", background="#17191d", foreground="#ececec", font=("Segoe UI", 9, "bold"))
        style.configure("TLabel", background="#17191d", foreground="#d9d9d9")
        style.configure("Title.TLabel", background="#17191d", foreground="#f2f2f2", font=("Segoe UI", 17, "bold"))
        style.configure("Sub.TLabel", background="#17191d", foreground="#9ea6af")
        style.configure("Accent.TButton", font=("Segoe UI", 9, "bold"))
        style.configure("TCheckbutton", background="#17191d", foreground="#d9d9d9")
        style.configure("TRadiobutton", background="#17191d", foreground="#d9d9d9")
        style.configure("Treeview", rowheight=24, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("TNotebook", background="#17191d")
        style.configure("TNotebook.Tab", padding=(10, 6))

    def _bind_shortcuts(self):
        self.bind("<Control-s>", lambda e: self.save_project())
        self.bind("<Control-Shift-S>", lambda e: self.save_project_as())
        self.bind("<space>", lambda e: self.toggle_play())
        self.bind("<Delete>", lambda e: self.remove_frames())

    def _build_ui(self):
        top = ttk.Frame(self, padding=(12, 9))
        top.pack(fill="x")
        ttk.Label(top, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(
            top,
            text="V0.3 • Palette Studio • Animation Workflow • Cell Inspector • DRES V3",
            style="Sub.TLabel",
        ).pack(side="left", padx=14)
        ttk.Button(top, text="Ouvrir projet", command=self.open_project).pack(side="right", padx=3)
        ttk.Button(top, text="Sauver projet", command=self.save_project).pack(side="right", padx=3)
        ttk.Button(top, text="Exporter .dres", command=self.export_resource, style="Accent.TButton").pack(side="right", padx=8)

        main = ttk.Panedwindow(self, orient="horizontal")
        main.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        left = ttk.Frame(main, padding=6)
        center = ttk.Frame(main, padding=6)
        right = ttk.Frame(main, padding=6)
        main.add(left, weight=3)
        main.add(center, weight=5)
        main.add(right, weight=5)
        self._build_left(left)
        self._build_center(center)
        self._build_right(right)

        bottom = ttk.Frame(self, padding=(12, 0, 12, 10))
        bottom.pack(fill="x")
        self.status = ttk.Label(bottom, text="Prêt.")
        self.status.pack(side="left")
        ttk.Label(bottom, text="Autonome • aucune API • aucune dépendance pip", style="Sub.TLabel").pack(side="right")

    def _build_left(self, parent):
        project = ttk.LabelFrame(parent, text="Projet / profil DMS-1", padding=8)
        project.pack(fill="x")
        ttk.Label(project, text="Nom").grid(row=0, column=0, sticky="w")
        ttk.Entry(project, textvariable=self.project_var).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(project, text="Mode vidéo").grid(row=1, column=0, sticky="w", pady=(7, 0))
        mode_cb = ttk.Combobox(project, textvariable=self.mode_var, values=list(DMS_MODES), state="readonly")
        mode_cb.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(7, 0))
        mode_cb.bind("<<ComboboxSelected>>", lambda e: self._mode_changed())
        ttk.Label(project, text="Cellule analyse").grid(row=2, column=0, sticky="w", pady=(7, 0))
        cell_cb = ttk.Combobox(project, textvariable=self.cell_var, values=[4, 8, 12, 16, 24, 32], state="readonly", width=8)
        cell_cb.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(7, 0))
        cell_cb.bind("<<ComboboxSelected>>", lambda e: self._cell_size_changed())
        ttk.Checkbutton(project, text="Index palette 0 = transparent", variable=self.transparent_var, command=self.run_analysis).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(7, 0)
        )
        project.columnconfigure(1, weight=1)
        self.mode_banner = ttk.Label(project, text="", wraplength=340, style="Sub.TLabel")
        self.mode_banner.grid(row=4, column=0, columnspan=2, sticky="w", pady=(7, 0))

        imp = ttk.LabelFrame(parent, text="Import", padding=8)
        imp.pack(fill="x", pady=8)
        ttk.Button(imp, text="PNG(s)", command=self.import_pngs, style="Accent.TButton").pack(side="left", fill="x", expand=True)
        ttk.Button(imp, text="Dossier PNG", command=self.import_folder).pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(imp, text="Sprite Sheet", command=self.import_sheet).pack(side="left", fill="x", expand=True)

        filter_row = ttk.Frame(parent)
        filter_row.pack(fill="x", pady=(0, 6))
        ttk.Label(filter_row, text="Afficher animation").pack(side="left")
        self.anim_filter_cb = ttk.Combobox(filter_row, textvariable=self.animation_filter_var, state="readonly", width=18)
        self.anim_filter_cb.pack(side="right")
        self.anim_filter_cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_frame_tree())

        frame_box = ttk.LabelFrame(parent, text="Frames - multi-sélection possible", padding=6)
        frame_box.pack(fill="both", expand=True)
        columns = ("anim", "size", "dur", "pal")
        self.frame_tree = ttk.Treeview(frame_box, columns=columns, show="tree headings", selectmode="extended")
        self.frame_tree.heading("#0", text="Frame")
        self.frame_tree.heading("anim", text="Anim")
        self.frame_tree.heading("size", text="Taille")
        self.frame_tree.heading("dur", text="ms")
        self.frame_tree.heading("pal", text="Pal")
        self.frame_tree.column("#0", width=145, stretch=True)
        self.frame_tree.column("anim", width=70, stretch=False)
        self.frame_tree.column("size", width=62, stretch=False)
        self.frame_tree.column("dur", width=44, stretch=False)
        self.frame_tree.column("pal", width=46, stretch=False)
        self.frame_tree.pack(fill="both", expand=True)
        self.frame_tree.bind("<<TreeviewSelect>>", lambda e: self.on_frame_selected())

        buttons = ttk.Frame(frame_box)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="↑", width=3, command=lambda: self.move_selected(-1)).pack(side="left")
        ttk.Button(buttons, text="↓", width=3, command=lambda: self.move_selected(1)).pack(side="left", padx=3)
        ttk.Button(buttons, text="Dupliquer", command=self.duplicate_frames).pack(side="left", padx=3)
        ttk.Button(buttons, text="Suppr.", command=self.remove_frames).pack(side="left", padx=3)
        ttk.Button(buttons, text="Analyser", command=self.run_analysis, style="Accent.TButton").pack(side="right")

        batch = ttk.LabelFrame(parent, text="Batch / animation", padding=7)
        batch.pack(fill="x", pady=(8, 0))
        ttk.Label(batch, text="Anim").grid(row=0, column=0, sticky="w")
        ttk.Entry(batch, textvariable=self.batch_anim_var, width=10).grid(row=0, column=1, padx=3)
        ttk.Label(batch, text="Durée").grid(row=0, column=2, sticky="w")
        ttk.Entry(batch, textvariable=self.batch_duration_var, width=6).grid(row=0, column=3, padx=3)
        ttk.Label(batch, text="Palette").grid(row=0, column=4, sticky="w")
        self.batch_palette_cb = ttk.Combobox(batch, textvariable=self.batch_palette_var, state="readonly", width=10)
        self.batch_palette_cb.grid(row=0, column=5, padx=3)
        ttk.Button(batch, text="Appliquer aux sélectionnées", command=self.apply_batch).grid(row=1, column=0, columnspan=6, sticky="ew", pady=(6, 0))

    def _build_center(self, parent):
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Label(toolbar, text="Preview").pack(side="left")
        zoom = ttk.Combobox(toolbar, textvariable=self.zoom_var, values=["Fit", "1×", "2×", "3×", "4×", "6×", "8×"], state="readonly", width=7)
        zoom.pack(side="left", padx=5)
        zoom.bind("<<ComboboxSelected>>", lambda e: self.show_selected())
        for text, var in (
            ("RGB333", self.quantized_preview_var),
            ("Pixels", self.grid_var),
            ("Cellules", self.cell_overlay_var),
            ("Map palettes", self.palette_map_var),
            ("Bounds", self.bounds_var),
            ("Align pivot", self.align_pivot_var),
        ):
            ttk.Checkbutton(toolbar, text=text, variable=var, command=self.show_selected).pack(side="left", padx=2)

        prev = ttk.LabelFrame(parent, text="Personnage entier - inspection hardware sans quitter la vue artiste", padding=6)
        prev.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(prev, bg="#1f2227", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.show_selected())
        self.canvas.bind("<Button-1>", self.canvas_press)
        self.canvas.bind("<B1-Motion>", self.canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.canvas_release)

        controls = ttk.Frame(parent)
        controls.pack(fill="x", pady=(7, 0))
        ttk.Button(controls, text="▶ / ■ Lecture", command=self.toggle_play).pack(side="left")
        ttk.Checkbutton(controls, text="Loop", variable=self.loop_var).pack(side="left", padx=6)
        ttk.Label(controls, text="Vitesse").pack(side="left")
        ttk.Combobox(controls, textvariable=self.play_speed_var, values=["0.5×", "1×", "2×"], state="readonly", width=6).pack(side="left", padx=4)
        ttk.Label(controls, text="Interaction").pack(side="left", padx=(12, 3))
        for label, value in (("Inspect", "INSPECT"), ("Pivot", "PIVOT"), ("Hitbox", "HITBOX")):
            ttk.Radiobutton(controls, text=label, variable=self.interaction_var, value=value).pack(side="left", padx=2)
        self.frame_info = ttk.Label(controls, text="Aucune frame", style="Sub.TLabel")
        self.frame_info.pack(side="right")

        props = ttk.LabelFrame(parent, text="Propriétés de la frame active", padding=8)
        props.pack(fill="x", pady=(8, 0))
        fields = [
            ("Nom", self.frame_name_var, 14),
            ("Animation", self.frame_anim_var, 11),
            ("Durée ms", self.duration_var, 7),
            ("Pivot X", self.pivot_x_var, 7),
            ("Pivot Y", self.pivot_y_var, 7),
        ]
        for col, (label, var, width) in enumerate(fields):
            ttk.Label(props, text=label).grid(row=0, column=col, sticky="w", padx=(0, 5))
            ttk.Entry(props, textvariable=var, width=width).grid(row=1, column=col, sticky="ew", padx=(0, 5))
        ttk.Label(props, text="Palette frame").grid(row=0, column=5, sticky="w")
        self.palette_pref_cb = ttk.Combobox(props, textvariable=self.palette_pref_var, state="readonly", width=8)
        self.palette_pref_cb.grid(row=1, column=5, sticky="w")
        ttk.Button(props, text="Appliquer", command=self.apply_frame_properties, style="Accent.TButton").grid(row=1, column=6, padx=(10, 0))

        hb = ttk.Frame(props)
        hb.grid(row=2, column=0, columnspan=7, sticky="ew", pady=(8, 0))
        ttk.Checkbutton(hb, text="Hitbox", variable=self.hitbox_enabled_var, command=self.show_selected).pack(side="left")
        for label, var in (("X", self.hitbox_x_var), ("Y", self.hitbox_y_var), ("W", self.hitbox_w_var), ("H", self.hitbox_h_var)):
            ttk.Label(hb, text=label).pack(side="left", padx=(10, 2))
            ttk.Entry(hb, textvariable=var, width=6).pack(side="left")
        ttk.Button(hb, text="Pivot actif → sélection", command=self.copy_pivot_to_selected).pack(side="right", padx=3)
        ttk.Button(hb, text="Hitbox active → sélection", command=self.copy_hitbox_to_selected).pack(side="right", padx=3)

    def _build_right(self, parent):
        nb = ttk.Notebook(parent)
        nb.pack(fill="both", expand=True)
        hardware = ttk.Frame(nb, padding=8)
        palettes = ttk.Frame(nb, padding=8)
        cell = ttk.Frame(nb, padding=8)
        animation = ttk.Frame(nb, padding=8)
        export = ttk.Frame(nb, padding=8)
        nb.add(hardware, text="Hardware")
        nb.add(palettes, text="Palette Studio")
        nb.add(cell, text="Cell Inspector")
        nb.add(animation, text="Animations")
        nb.add(export, text="Export / GDK")

        # Hardware
        cards = ttk.Frame(hardware)
        cards.pack(fill="x")
        self.card_vars = {}
        for i, key in enumerate(["STATUS", "RGB333", "PALETTES", "UNIQUE", "VRAM", "SCANLINE"]):
            box = ttk.LabelFrame(cards, text=key, padding=6)
            box.grid(row=i // 3, column=i % 3, sticky="nsew", padx=3, pady=3)
            v = tk.StringVar(value="-")
            self.card_vars[key] = v
            ttk.Label(box, textvariable=v, font=("Segoe UI", 11, "bold")).pack()
            cards.columnconfigure(i % 3, weight=1)
        self.hardware_text = self._make_text(hardware, height=12)
        self.hardware_text.pack(fill="both", expand=True, pady=(8, 0))
        issue_box = ttk.LabelFrame(hardware, text="Validation", padding=6)
        issue_box.pack(fill="both", expand=True, pady=(8, 0))
        self.issue_tree = ttk.Treeview(issue_box, columns=("severity", "scope", "message"), show="headings", height=8)
        self.issue_tree.heading("severity", text="Niveau")
        self.issue_tree.heading("scope", text="Zone")
        self.issue_tree.heading("message", text="Message")
        self.issue_tree.column("severity", width=65, stretch=False)
        self.issue_tree.column("scope", width=100, stretch=False)
        self.issue_tree.column("message", width=420, stretch=True)
        self.issue_tree.pack(fill="both", expand=True)

        # Palette Studio
        ttk.Label(
            palettes,
            text="AUTO répartit les cellules. LOCK fige le contenu d'une palette physique. "
                 "Les overrides de cellule permettent d'intervenir localement sans découper le personnage à la main.",
            wraplength=520, style="Sub.TLabel"
        ).pack(anchor="w")
        self.palette_canvas = tk.Canvas(palettes, height=330, bg="#1f2227", highlightthickness=0)
        self.palette_canvas.pack(fill="x", pady=(10, 8))
        lockrow = ttk.Frame(palettes)
        lockrow.pack(fill="x")
        ttk.Label(lockrow, text="Palette").pack(side="left")
        self.lock_palette_cb = ttk.Combobox(lockrow, textvariable=self.lock_palette_var, state="readonly", width=7)
        self.lock_palette_cb.pack(side="left", padx=4)
        ttk.Button(lockrow, text="LOCK contenu actuel", command=self.lock_current_palette).pack(side="left", padx=3)
        ttk.Button(lockrow, text="UNLOCK", command=self.unlock_palette).pack(side="left", padx=3)
        ttk.Button(lockrow, text="Tout déverrouiller", command=self.unlock_all_palettes).pack(side="left", padx=3)
        self.palette_text = self._make_text(palettes, height=9)
        self.palette_text.pack(fill="both", expand=True, pady=(8, 0))

        # Cell inspector
        ttk.Label(cell, text="Clique une cellule dans la preview avec Interaction = Inspect.", style="Sub.TLabel").pack(anchor="w")
        self.cell_text = self._make_text(cell, height=16)
        self.cell_text.pack(fill="both", expand=True, pady=(8, 8))
        cbar = ttk.Frame(cell)
        cbar.pack(fill="x")
        ttk.Label(cbar, text="Override palette").pack(side="left")
        self.cell_override_cb = ttk.Combobox(cbar, textvariable=self.cell_override_var, state="readonly", width=8)
        self.cell_override_cb.pack(side="left", padx=5)
        ttk.Button(cbar, text="Appliquer à cette cellule", command=self.apply_cell_override).pack(side="left", padx=3)
        ttk.Button(cbar, text="Reset AUTO", command=self.reset_cell_override).pack(side="left", padx=3)
        ttk.Button(cbar, text="Reset overrides frame", command=self.reset_frame_overrides).pack(side="right")

        # Animation manager
        self.animation_tree = ttk.Treeview(animation, columns=("count", "duration"), show="tree headings", height=10)
        self.animation_tree.heading("#0", text="Animation")
        self.animation_tree.heading("count", text="Frames")
        self.animation_tree.heading("duration", text="Durée totale")
        self.animation_tree.column("#0", width=180)
        self.animation_tree.column("count", width=70, stretch=False)
        self.animation_tree.column("duration", width=100, stretch=False)
        self.animation_tree.pack(fill="x")
        ar = ttk.Frame(animation)
        ar.pack(fill="x", pady=(8, 0))
        ttk.Button(ar, text="Filtrer sur l'animation", command=self.filter_selected_animation).pack(side="left", padx=3)
        ttk.Button(ar, text="Renommer animation", command=self.rename_animation).pack(side="left", padx=3)
        ttk.Button(ar, text="Lecture animation", command=self.play_selected_animation).pack(side="left", padx=3)
        self.animation_text = self._make_text(animation, height=14)
        self.animation_text.pack(fill="both", expand=True, pady=(8, 0))

        # Export
        ttk.Label(
            export,
            text="DRES V3 : animations, pivots, hitbox, palette locks, overrides par cellule, "
                 "tiles, flips, validation et collisions RGB333.",
            wraplength=520,
        ).pack(anchor="w")
        ttk.Button(export, text="Exporter .dres V3", command=self.export_resource, style="Accent.TButton").pack(fill="x", pady=(14, 5))
        ttk.Button(export, text="Exporter bundle futur DMS-GDK", command=self.export_bundle).pack(fill="x", pady=5)
        ttk.Button(export, text="Exporter rapport TXT", command=self.export_report).pack(fill="x", pady=5)
        ttk.Separator(export).pack(fill="x", pady=14)
        ttk.Button(export, text="Sauver projet .dproj", command=self.save_project).pack(fill="x", pady=5)
        ttk.Button(export, text="Sauver sous…", command=self.save_project_as).pack(fill="x", pady=5)
        ttk.Button(export, text="Ouvrir projet .dproj", command=self.open_project).pack(fill="x", pady=5)
        self.export_text = self._make_text(export, height=12)
        self.export_text.pack(fill="both", expand=True, pady=(10, 0))
        self._set_text(
            self.export_text,
            "Bundle GDK V0.3 :\n"
            "• ressource .dres\n"
            "• header C avec enums frame/animation\n"
            "• fichier _meta.c avec timings, pivots et hitboxes\n"
            "• handoff JSON\n\n"
            "Ce metadata C est un handoff draft ; le resource compiler final consommera le .dres."
        )

    def _make_text(self, parent, height=10):
        t = tk.Text(parent, height=height, wrap="word", bg="#202329", fg="#e3e3e3", insertbackground="white", relief="flat")
        t.configure(state="disabled")
        return t

    @staticmethod
    def _set_text(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    # ------------------------------------------------------------------
    # Import / frames
    # ------------------------------------------------------------------

    def import_pngs(self):
        paths = filedialog.askopenfilenames(title="Importer PNG(s)", filetypes=[("PNG", "*.png")])
        if paths:
            self._import_paths(list(paths))

    def import_folder(self):
        folder = filedialog.askdirectory(title="Importer tous les PNG d'un dossier")
        if not folder:
            return
        paths = sorted(Path(folder).glob("*.png"), key=lambda p: natural_key(p.name))
        if not paths:
            messagebox.showinfo("Import dossier", "Aucun PNG trouvé dans ce dossier.")
            return
        self._import_paths([str(p) for p in paths])

    def _import_paths(self, paths):
        loaded = []
        try:
            for path in paths:
                loaded.append(self.loader.load_file(str(path)))
        except Exception as e:
            messagebox.showerror("Import PNG", f"Impossible de lire un fichier.\n\n{e}")
            return
        start = len(self.frames)
        self.frames.extend(loaded)
        self._refresh_animation_filter()
        self.refresh_frame_tree()
        if loaded:
            self.select_frame(start)
        self.run_analysis()
        self.status.configure(text=f"{len(loaded)} PNG importé(s).")

    def import_sheet(self):
        path = filedialog.askopenfilename(title="Importer Sprite Sheet", filetypes=[("PNG", "*.png")])
        if not path:
            return
        dialog = tk.Toplevel(self)
        dialog.title("Sprite Sheet - grille")
        dialog.transient(self)
        dialog.grab_set()
        f = ttk.Frame(dialog, padding=16)
        f.pack()
        fields = [
            ("Largeur frame", self.sheet_fw_var),
            ("Hauteur frame", self.sheet_fh_var),
            ("Marge X", self.sheet_margin_x_var),
            ("Marge Y", self.sheet_margin_y_var),
            ("Espacement X", self.sheet_spacing_x_var),
            ("Espacement Y", self.sheet_spacing_y_var),
        ]
        for row, (label, var) in enumerate(fields):
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Spinbox(f, from_=0 if "Marge" in label or "Espacement" in label else 1, to=2048, textvariable=var, width=10).grid(row=row, column=1, padx=8)
        anim_var = tk.StringVar(value="IDLE")
        ttk.Label(f, text="Animation").grid(row=len(fields), column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=anim_var, width=12).grid(row=len(fields), column=1, padx=8)

        def perform():
            try:
                sheet = self.loader.load_file(path)
                sliced = self.loader.slice_sheet(
                    sheet,
                    int(self.sheet_fw_var.get()), int(self.sheet_fh_var.get()),
                    int(self.sheet_margin_x_var.get()), int(self.sheet_margin_y_var.get()),
                    int(self.sheet_spacing_x_var.get()), int(self.sheet_spacing_y_var.get()),
                )
                if not sliced:
                    raise ValueError("Aucune frame complète avec ces réglages.")
                anim = anim_var.get().strip().upper() or "IDLE"
                for fr in sliced:
                    fr.animation = anim
                start = len(self.frames)
                self.frames.extend(sliced)
                dialog.destroy()
                self._refresh_animation_filter()
                self.refresh_frame_tree()
                self.select_frame(start)
                self.run_analysis()
                self.status.configure(text=f"Sprite sheet : {len(sliced)} frames importées.")
            except Exception as e:
                messagebox.showerror("Sprite Sheet", str(e), parent=dialog)

        ttk.Button(f, text="Importer", command=perform, style="Accent.TButton").grid(row=len(fields)+1, column=0, columnspan=2, pady=(10, 0))

    def selected_indices(self):
        out = []
        for iid in self.frame_tree.selection():
            try:
                out.append(int(iid))
            except Exception:
                pass
        return sorted(set(out))

    def active_index(self):
        focus = self.frame_tree.focus()
        try:
            return int(focus) if focus else (self.selected_indices()[0] if self.selected_indices() else None)
        except Exception:
            return None

    def refresh_frame_tree(self):
        selected = self.selected_indices()
        active = self.active_index()
        for item in self.frame_tree.get_children():
            self.frame_tree.delete(item)
        filt = self.animation_filter_var.get()
        for idx, fr in enumerate(self.frames):
            if filt != "ALL" and fr.animation != filt:
                continue
            pal = "AUTO" if fr.preferred_palette < 0 else f"P{fr.preferred_palette}"
            self.frame_tree.insert("", "end", iid=str(idx), text=fr.name, values=(fr.animation, f"{fr.width}×{fr.height}", fr.duration_ms, pal))
        for idx in selected:
            if self.frame_tree.exists(str(idx)):
                self.frame_tree.selection_add(str(idx))
        if active is not None and self.frame_tree.exists(str(active)):
            self.frame_tree.focus(str(active))
            self.frame_tree.see(str(active))

    def select_frame(self, idx, additive=False):
        if not self.frames:
            return
        idx = clamp(idx, 0, len(self.frames) - 1)
        if not self.frame_tree.exists(str(idx)):
            self.animation_filter_var.set("ALL")
            self.refresh_frame_tree()
        if not additive:
            self.frame_tree.selection_set(str(idx))
        else:
            self.frame_tree.selection_add(str(idx))
        self.frame_tree.focus(str(idx))
        self.frame_tree.see(str(idx))
        self.on_frame_selected()

    def on_frame_selected(self):
        i = self.active_index()
        if i is None or i >= len(self.frames):
            return
        f = self.frames[i]
        self.frame_name_var.set(f.name)
        self.frame_anim_var.set(f.animation)
        self.duration_var.set(f.duration_ms)
        self.pivot_x_var.set(f.pivot_x)
        self.pivot_y_var.set(f.pivot_y)
        self.palette_pref_var.set("AUTO" if f.preferred_palette < 0 else f"P{f.preferred_palette}")
        self.hitbox_enabled_var.set(f.hitbox.enabled)
        self.hitbox_x_var.set(f.hitbox.x)
        self.hitbox_y_var.set(f.hitbox.y)
        self.hitbox_w_var.set(f.hitbox.w)
        self.hitbox_h_var.set(f.hitbox.h)
        self.selected_cell = None
        self.show_selected()
        self.render_cell_inspector()
        self.render_animation_summary()

    def duplicate_frames(self):
        ids = self.selected_indices()
        if not ids:
            return
        insert_at = max(ids) + 1
        copies = []
        for idx in ids:
            f = self.frames[idx]
            copies.append(PixelFrame(
                f.name + "_copy", f.width, f.height,
                [list(row) for row in f.pixels],
                duration_ms=f.duration_ms, source=f.source, source_rect=f.source_rect,
                animation=f.animation, pivot_x=f.pivot_x, pivot_y=f.pivot_y,
                preferred_palette=f.preferred_palette,
                hitbox=Hitbox(**asdict(f.hitbox)),
                cell_palette_overrides=dict(f.cell_palette_overrides),
            ))
        for off, fr in enumerate(copies):
            self.frames.insert(insert_at + off, fr)
        self._refresh_animation_filter()
        self.refresh_frame_tree()
        for idx in range(insert_at, insert_at + len(copies)):
            self.select_frame(idx, additive=(idx != insert_at))
        self.run_analysis()

    def remove_frames(self):
        ids = self.selected_indices()
        if not ids:
            return
        for idx in reversed(ids):
            del self.frames[idx]
        self._refresh_animation_filter()
        self.refresh_frame_tree()
        if self.frames:
            self.select_frame(min(ids[0], len(self.frames) - 1))
        else:
            self._empty_diagnostics()
            self.show_selected()
        self.run_analysis()

    def move_selected(self, delta):
        ids = self.selected_indices()
        if not ids:
            return
        if delta < 0:
            if ids[0] == 0:
                return
            for idx in ids:
                self.frames[idx - 1], self.frames[idx] = self.frames[idx], self.frames[idx - 1]
            new_ids = [i - 1 for i in ids]
        else:
            if ids[-1] == len(self.frames) - 1:
                return
            for idx in reversed(ids):
                self.frames[idx + 1], self.frames[idx] = self.frames[idx], self.frames[idx + 1]
            new_ids = [i + 1 for i in ids]
        self.refresh_frame_tree()
        self.frame_tree.selection_set(*[str(i) for i in new_ids if self.frame_tree.exists(str(i))])
        if new_ids:
            self.frame_tree.focus(str(new_ids[0]))
        self.run_analysis()

    def apply_frame_properties(self):
        i = self.active_index()
        if i is None:
            return
        try:
            f = self.frames[i]
            f.name = self.frame_name_var.get().strip() or f.name
            f.animation = self.frame_anim_var.get().strip().upper() or "IDLE"
            f.duration_ms = max(16, int(self.duration_var.get()))
            f.pivot_x = int(self.pivot_x_var.get())
            f.pivot_y = int(self.pivot_y_var.get())
            p = self.palette_pref_var.get().upper()
            f.preferred_palette = -1 if p == "AUTO" else int(p[1:])
            f.hitbox = Hitbox(
                bool(self.hitbox_enabled_var.get()),
                int(self.hitbox_x_var.get()), int(self.hitbox_y_var.get()),
                max(0, int(self.hitbox_w_var.get())), max(0, int(self.hitbox_h_var.get())),
            )
        except Exception as e:
            messagebox.showerror("Propriétés", f"Valeur invalide.\n\n{e}")
            return
        self._refresh_animation_filter()
        self.refresh_frame_tree()
        self.select_frame(i)
        self.run_analysis()
        self.status.configure(text="Propriétés appliquées.")

    def apply_batch(self):
        ids = self.selected_indices()
        if not ids:
            messagebox.showinfo("Batch", "Sélectionne une ou plusieurs frames.")
            return
        anim = self.batch_anim_var.get().strip().upper()
        try:
            duration = max(16, int(self.batch_duration_var.get()))
        except Exception:
            duration = None
        p = self.batch_palette_var.get()
        for idx in ids:
            f = self.frames[idx]
            if anim:
                f.animation = anim
            if duration is not None:
                f.duration_ms = duration
            if p == "AUTO":
                f.preferred_palette = -1
            elif p.startswith("P"):
                f.preferred_palette = int(p[1:])
        self._refresh_animation_filter()
        self.refresh_frame_tree()
        self.run_analysis()
        self.status.configure(text=f"Batch appliqué à {len(ids)} frame(s).")

    def copy_pivot_to_selected(self):
        i = self.active_index()
        ids = self.selected_indices()
        if i is None or not ids:
            return
        src = self.frames[i]
        for idx in ids:
            self.frames[idx].pivot_x = src.pivot_x
            self.frames[idx].pivot_y = src.pivot_y
        self.on_frame_selected()
        self.run_analysis()

    def copy_hitbox_to_selected(self):
        i = self.active_index()
        ids = self.selected_indices()
        if i is None or not ids:
            return
        src = self.frames[i]
        for idx in ids:
            self.frames[idx].hitbox = Hitbox(**asdict(src.hitbox))
        self.on_frame_selected()
        self.run_analysis()

    # ------------------------------------------------------------------
    # Preview / interactions
    # ------------------------------------------------------------------

    def _checkerboard(self, cw, ch):
        size = 16
        for y in range(0, ch, size):
            for x in range(0, cw, size):
                fill = "#24272d" if ((x // size) + (y // size)) % 2 == 0 else "#2c3037"
                self.canvas.create_rectangle(x, y, x + size, y + size, fill=fill, outline=fill)

    def _display_scale(self, frame):
        z = self.zoom_var.get()
        if z == "Fit":
            cw = max(100, self.canvas.winfo_width())
            ch = max(100, self.canvas.winfo_height())
            return max(1, min(10, int(min((cw - 80) / frame.width, (ch - 80) / frame.height))))
        try:
            return int(z.replace("×", ""))
        except Exception:
            return 1

    def _preview_position(self, frame, image_w, image_h):
        cw = max(100, self.canvas.winfo_width())
        ch = max(100, self.canvas.winfo_height())
        if self.align_pivot_var.get():
            anchor_x = cw // 2
            anchor_y = int(ch * 0.66)
            return anchor_x - frame.pivot_x * self.preview_scale, anchor_y - frame.pivot_y * self.preview_scale
        return cw // 2 - image_w // 2, ch // 2 - image_h // 2

    def show_selected(self):
        self.canvas.delete("all")
        cw = max(100, self.canvas.winfo_width())
        ch = max(100, self.canvas.winfo_height())
        self._checkerboard(cw, ch)
        i = self.active_index()
        if i is None or not self.frames:
            self.canvas.create_text(cw // 2, ch // 2, text="Importe un PNG ou une sprite sheet", fill="#8b949e", font=("Segoe UI", 12))
            return
        f = self.frames[i]
        scale = self._display_scale(f)
        self.preview_scale = scale
        img = tk.PhotoImage(width=f.width, height=f.height)
        transparent_points = []
        for y, row in enumerate(f.pixels):
            colors = []
            for x, (r, g, b, a) in enumerate(row):
                if a == 0:
                    colors.append("#000000")
                    transparent_points.append((x, y))
                else:
                    rgb = rgb333_to_rgb888(rgb888_to_rgb333((r, g, b))) if self.quantized_preview_var.get() else (r, g, b)
                    colors.append("#%02x%02x%02x" % rgb)
            img.put("{" + " ".join(colors) + "}", to=(0, y))
        if hasattr(img, "transparency_set"):
            for x, y in transparent_points:
                try:
                    img.transparency_set(x, y, True)
                except tk.TclError:
                    break
        if scale > 1:
            img = img.zoom(scale, scale)
        self.preview_img = img
        x0, y0 = self._preview_position(f, img.width(), img.height())
        self.preview_origin = (x0, y0)
        self.canvas.create_image(x0, y0, image=img, anchor="nw")
        self.canvas.create_rectangle(x0, y0, x0 + img.width(), y0 + img.height(), outline="#69717c")

        # Pixel grid
        if self.grid_var.get() and scale >= 3:
            for x in range(x0, x0 + img.width() + 1, scale):
                self.canvas.create_line(x, y0, x, y0 + img.height(), fill="#343942")
            for y in range(y0, y0 + img.height() + 1, scale):
                self.canvas.create_line(x0, y, x0 + img.width(), y, fill="#343942")

        # Cell overlay + palette map
        if self.cell_overlay_var.get() or self.palette_map_var.get():
            cell_px = int(self.cell_var.get()) * scale
            cols = math.ceil(f.width / int(self.cell_var.get()))
            rows = math.ceil(f.height / int(self.cell_var.get()))
            tile_lookup = {}
            if self.analysis:
                tile_lookup = {(t.cx, t.cy): t for t in self.analysis.tiles if t.frame_index == i}
            for cy in range(rows):
                for cx in range(cols):
                    xx = x0 + cx * cell_px
                    yy = y0 + cy * cell_px
                    t = tile_lookup.get((cx, cy))
                    outline = "#53606e"
                    width = 1
                    if self.palette_map_var.get() and t and t.palette_id is not None:
                        outline = PALETTE_OVERLAY_COLORS[t.palette_id % len(PALETTE_OVERLAY_COLORS)]
                        width = 2
                        if scale >= 2 and not t.empty:
                            self.canvas.create_text(xx + 3, yy + 3, text=f"P{t.palette_id}", anchor="nw", fill=outline, font=("Segoe UI", 8, "bold"))
                    if self.cell_overlay_var.get() or self.palette_map_var.get():
                        self.canvas.create_rectangle(xx, yy, xx + cell_px, yy + cell_px, outline=outline, width=width)

        # Active bounds
        if self.bounds_var.get():
            b, _ = active_bounds(f)
            if b:
                bx0, by0, bx1, by1 = b
                self.canvas.create_rectangle(
                    x0 + bx0 * scale, y0 + by0 * scale,
                    x0 + bx1 * scale, y0 + by1 * scale,
                    outline="#f3c969", width=2,
                )

        # Pivot
        px = x0 + f.pivot_x * scale
        py = y0 + f.pivot_y * scale
        self.canvas.create_line(px - 9, py, px + 9, py, fill="#ff5f5f", width=2)
        self.canvas.create_line(px, py - 9, px, py + 9, fill="#ff5f5f", width=2)
        if self.align_pivot_var.get():
            self.canvas.create_text(px + 11, py - 9, text="PIVOT", anchor="sw", fill="#ff8d8d", font=("Segoe UI", 8, "bold"))

        # Hitbox
        if f.hitbox.enabled:
            hb = f.hitbox
            self.canvas.create_rectangle(
                x0 + hb.x * scale, y0 + hb.y * scale,
                x0 + (hb.x + hb.w) * scale, y0 + (hb.y + hb.h) * scale,
                outline="#58d68d", width=2,
            )

        # Selected cell
        if self.selected_cell and self.selected_cell[0] == i:
            _fi, cx, cy = self.selected_cell
            csize = int(self.cell_var.get()) * scale
            self.canvas.create_rectangle(
                x0 + cx * csize, y0 + cy * csize,
                x0 + (cx + 1) * csize, y0 + (cy + 1) * csize,
                outline="#ffffff", width=3,
            )

        self.frame_info.configure(
            text=f"{i+1}/{len(self.frames)} • {f.name} • {f.animation} • {f.width}×{f.height} • {scale}×"
        )

    def _canvas_to_frame(self, event):
        i = self.active_index()
        if i is None:
            return None
        f = self.frames[i]
        x0, y0 = self.preview_origin
        s = max(1, self.preview_scale)
        fx = int(math.floor((event.x - x0) / s))
        fy = int(math.floor((event.y - y0) / s))
        if fx < 0 or fy < 0 or fx >= f.width or fy >= f.height:
            return None
        return fx, fy

    def canvas_press(self, event):
        pos = self._canvas_to_frame(event)
        if pos is None:
            return
        i = self.active_index()
        f = self.frames[i]
        fx, fy = pos
        mode = self.interaction_var.get()
        if mode == "PIVOT":
            f.pivot_x, f.pivot_y = fx, fy
            self.pivot_x_var.set(fx)
            self.pivot_y_var.set(fy)
            self.show_selected()
            self.run_analysis()
            self.status.configure(text=f"Pivot placé à ({fx},{fy}).")
        elif mode == "INSPECT":
            cell_size = int(self.cell_var.get())
            cx, cy = fx // cell_size, fy // cell_size
            self.selected_cell = (i, cx, cy)
            self.render_cell_inspector()
            self.show_selected()
        elif mode == "HITBOX":
            self.drag_hitbox_start = (fx, fy)
            f.hitbox.enabled = True
            f.hitbox.x, f.hitbox.y, f.hitbox.w, f.hitbox.h = fx, fy, 0, 0
            self.hitbox_enabled_var.set(True)
            self.show_selected()

    def canvas_drag(self, event):
        if self.interaction_var.get() != "HITBOX" or not self.drag_hitbox_start:
            return
        i = self.active_index()
        if i is None:
            return
        f = self.frames[i]
        x0, y0 = self.preview_origin
        s = max(1, self.preview_scale)
        fx = clamp(int(math.floor((event.x - x0) / s)), 0, f.width)
        fy = clamp(int(math.floor((event.y - y0) / s)), 0, f.height)
        sx, sy = self.drag_hitbox_start
        left, top = min(sx, fx), min(sy, fy)
        right, bottom = max(sx, fx), max(sy, fy)
        f.hitbox.x, f.hitbox.y = left, top
        f.hitbox.w, f.hitbox.h = right - left, bottom - top
        self.hitbox_x_var.set(f.hitbox.x)
        self.hitbox_y_var.set(f.hitbox.y)
        self.hitbox_w_var.set(f.hitbox.w)
        self.hitbox_h_var.set(f.hitbox.h)
        self.show_selected()

    def canvas_release(self, event):
        if self.interaction_var.get() == "HITBOX" and self.drag_hitbox_start:
            self.drag_hitbox_start = None
            self.run_analysis()
            self.status.configure(text="Hitbox dessinée directement dans la preview.")

    # ------------------------------------------------------------------
    # Playback / animation manager
    # ------------------------------------------------------------------

    def _refresh_animation_filter(self):
        names = sorted({f.animation for f in self.frames if f.animation})
        values = ["ALL"] + names
        self.anim_filter_cb["values"] = values
        if self.animation_filter_var.get() not in values:
            self.animation_filter_var.set("ALL")
        self.refresh_animation_tree()

    def playback_sequence(self):
        filt = self.animation_filter_var.get()
        if filt != "ALL":
            return [i for i, f in enumerate(self.frames) if f.animation == filt]
        i = self.active_index()
        if i is not None:
            anim = self.frames[i].animation
            seq = [j for j, f in enumerate(self.frames) if f.animation == anim]
            if seq:
                return seq
        return list(range(len(self.frames)))

    def toggle_play(self):
        if self.playing:
            self.stop_playback()
            return
        seq = self.playback_sequence()
        if not seq:
            return
        active = self.active_index()
        self.playing = True
        self.play_pos = seq.index(active) if active in seq else 0
        self._play_tick()

    def stop_playback(self):
        self.playing = False
        if self.play_after:
            try:
                self.after_cancel(self.play_after)
            except Exception:
                pass
        self.play_after = None

    def _play_tick(self):
        if not self.playing:
            return
        seq = self.playback_sequence()
        if not seq:
            self.stop_playback()
            return
        if self.play_pos >= len(seq):
            if self.loop_var.get():
                self.play_pos = 0
            else:
                self.stop_playback()
                return
        idx = seq[self.play_pos]
        self.select_frame(idx)
        speed = {"0.5×": 0.5, "1×": 1.0, "2×": 2.0}.get(self.play_speed_var.get(), 1.0)
        delay = max(8, int(self.frames[idx].duration_ms / speed))
        self.play_pos += 1
        self.play_after = self.after(delay, self._play_tick)

    def refresh_animation_tree(self):
        if not hasattr(self, "animation_tree"):
            return
        for item in self.animation_tree.get_children():
            self.animation_tree.delete(item)
        for name, desc in animation_descriptors(self.frames).items():
            self.animation_tree.insert("", "end", iid=name, text=name, values=(desc["count"], f"{desc['total_duration_ms']} ms"))
        self.render_animation_summary()

    def render_animation_summary(self):
        if not hasattr(self, "animation_text"):
            return
        lines = []
        for name, desc in animation_descriptors(self.frames).items():
            ids = desc["frames"]
            durations = [self.frames[i].duration_ms for i in ids]
            pivots = {(self.frames[i].pivot_x, self.frames[i].pivot_y) for i in ids}
            lines.append(
                f"{name} - {len(ids)} frame(s) - {desc['total_duration_ms']} ms\n"
                f"  frames : {', '.join(self.frames[i].name for i in ids)}\n"
                f"  timings : {durations}\n"
                f"  pivots distincts : {len(pivots)}\n"
            )
        self._set_text(self.animation_text, "\n".join(lines) if lines else "Aucune animation.")

    def selected_animation_name(self):
        sel = self.animation_tree.selection()
        return sel[0] if sel else None

    def filter_selected_animation(self):
        name = self.selected_animation_name()
        if name:
            self.animation_filter_var.set(name)
            self.refresh_frame_tree()

    def rename_animation(self):
        old = self.selected_animation_name()
        if not old:
            return
        new = simpledialog.askstring("Renommer animation", "Nouveau nom :", initialvalue=old, parent=self)
        if not new:
            return
        new = new.strip().upper()
        for f in self.frames:
            if f.animation == old:
                f.animation = new
        self._refresh_animation_filter()
        self.refresh_frame_tree()
        self.run_analysis()

    def play_selected_animation(self):
        name = self.selected_animation_name()
        if not name:
            return
        self.animation_filter_var.set(name)
        self.refresh_frame_tree()
        ids = [i for i, f in enumerate(self.frames) if f.animation == name]
        if ids:
            self.select_frame(ids[0])
            if not self.playing:
                self.toggle_play()

    # ------------------------------------------------------------------
    # Analysis / palettes / cell inspector
    # ------------------------------------------------------------------

    def _mode_changed(self):
        self._update_mode_banner()
        self.run_analysis()

    def _cell_size_changed(self):
        if any(f.cell_palette_overrides for f in self.frames):
            messagebox.showwarning(
                "Cellule analyse",
                "La taille de cellule a changé. Les overrides existants sont conservés mais certains peuvent ne plus correspondre à la nouvelle grille. Le diagnostic les signalera."
            )
        self.selected_cell = None
        self.run_analysis()
        self.show_selected()

    def _update_mode_banner(self):
        m = DMS_MODES[self.mode_var.get()]
        self.mode_banner.configure(
            text=f"{m['w']}×{m['h']} • {m['palettes']} palettes ×16 • "
                 f"{m['sprites_total']} total / {m['sprites_scanline']} scanline • {m['description']}"
        )
        palette_values = ["AUTO"] + [f"P{i}" for i in range(m["palettes"])]
        self.palette_pref_cb["values"] = palette_values
        self.cell_override_cb["values"] = palette_values
        self.lock_palette_cb["values"] = [f"P{i}" for i in range(m["palettes"])]
        self.batch_palette_cb["values"] = ["UNCHANGED", "AUTO"] + [f"P{i}" for i in range(m["palettes"])]
        if self.palette_pref_var.get() not in palette_values:
            self.palette_pref_var.set("AUTO")
        if self.cell_override_var.get() not in palette_values:
            self.cell_override_var.set("AUTO")
        if self.lock_palette_var.get() not in self.lock_palette_cb["values"]:
            self.lock_palette_var.set("P0")
        if self.batch_palette_var.get() not in self.batch_palette_cb["values"]:
            self.batch_palette_var.set("UNCHANGED")

    def run_analysis(self):
        if not self.frames:
            self.analysis = None
            self._empty_diagnostics()
            return
        try:
            self.analysis = analyze_frames(
                self.frames, self.mode_var.get(), int(self.cell_var.get()),
                self.transparent_var.get(), self.palette_locks,
            )
            self.render_analysis()
            self.render_cell_inspector()
            self.refresh_animation_tree()
            self.show_selected()
            self.status.configure(text="Analyse DMS terminée.")
        except Exception as e:
            messagebox.showerror("Analyse", str(e))
            self.status.configure(text=f"Erreur analyse : {e}")

    def render_analysis(self):
        a = self.analysis
        if not a:
            return
        m = DMS_MODES[self.mode_var.get()]
        counts = a.issue_counts()
        status = "ERROR" if counts["ERROR"] else ("WARN" if counts["WARN"] else "PASS")
        used_palettes = sum(1 for p in a.palettes if p)
        self.card_vars["STATUS"].set(status)
        self.card_vars["RGB333"].set(f"{a.rgb333_colors}/512")
        self.card_vars["PALETTES"].set(f"{used_palettes}/{m['palettes']}")
        self.card_vars["UNIQUE"].set(str(len(a.unique_tiles)))
        self.card_vars["VRAM"].set(f"{a.vram_bytes} B")
        self.card_vars["SCANLINE"].set(f"{a.max_scanline_cells} blocs")
        redundancy = 100 * a.duplicate_cells / a.nonempty_cells if a.nonempty_cells else 0
        txt = (
            f"Frames : {len(a.frames)} • Animations : {len(animation_map(a.frames))}\n"
            f"Couleurs source : {a.source_colors} • RGB333 : {a.rgb333_colors} • collisions : {a.rgb333_collisions}\n\n"
            f"Blocs actifs : {a.nonempty_cells}/{a.total_cells}\n"
            f"Tiles/blocs uniques : {len(a.unique_tiles)}\n"
            f"Réemplois : {a.duplicate_cells} ({redundancy:.1f} %) • via flip : {a.flip_reused_cells}\n"
            f"VRAM graphique estimée : {a.vram_bytes} octets\n\n"
            f"Profil : {self.mode_var.get()}\n"
            f"Budget profil : {m['sprites_total']} objets total / {m['sprites_scanline']} scanline\n"
            f"Analyse proxy : {a.max_frame_cells} blocs/frame / {a.max_scanline_cells} blocs/scanline\n\n"
            "Le nombre exact d'objets sprites n'est toujours pas inventé : la géométrie sprite hardware reste à figer côté VDP/GDK."
        )
        self._set_text(self.hardware_text, txt)
        for item in self.issue_tree.get_children():
            self.issue_tree.delete(item)
        if a.issues:
            for n, issue in enumerate(a.issues):
                self.issue_tree.insert("", "end", iid=str(n), values=(issue.severity, issue.scope, issue.message))
        else:
            self.issue_tree.insert("", "end", values=("PASS", "GLOBAL", "Aucun problème détecté."))
        self.draw_palettes()

    def draw_palettes(self):
        self.palette_canvas.delete("all")
        if not self.analysis:
            return
        cap = 15 if self.transparent_var.get() else 16
        width = max(450, self.palette_canvas.winfo_width())
        sw = max(18, min(27, (width - 150) // 16))
        y = 14
        lines = []
        for pi, pal in enumerate(self.analysis.palettes):
            state = "LOCK" if self.analysis.palette_locked[pi] else "AUTO"
            self.palette_canvas.create_text(12, y + 12, text=f"P{pi}", anchor="w", fill="#ededed", font=("Segoe UI", 10, "bold"))
            self.palette_canvas.create_text(42, y + 12, text=state, anchor="w", fill="#f3c969" if state == "LOCK" else "#9aa3ad", font=("Segoe UI", 8, "bold"))
            cols = ([None] if self.transparent_var.get() else []) + pal
            for ci in range(16):
                x = 88 + ci * sw
                if ci < len(cols):
                    c = cols[ci]
                    fill = "#2b2f36" if c is None else "#%02x%02x%02x" % rgb333_to_rgb888(c)
                else:
                    fill = "#17191d"
                self.palette_canvas.create_rectangle(x, y, x + sw - 2, y + 24, fill=fill, outline="#505762")
            self.palette_canvas.create_text(94 + 16 * sw, y + 12, text=f"{len(pal)}/{cap}", anchor="w", fill="#9aa3ad")
            lines.append(f"P{pi} [{state}] - {len(pal)}/{cap} - {' '.join(rgb333_hex(c) for c in pal)}")
            y += 39
        if self.analysis.collision_map:
            lines.append("\nCollisions RGB333 :")
            for code, srcs in self.analysis.collision_map.items():
                lines.append(f"  {code} <= {srcs}")
        self._set_text(self.palette_text, "\n".join(lines))

    def lock_current_palette(self):
        if not self.analysis:
            return
        try:
            pi = int(self.lock_palette_var.get()[1:])
        except Exception:
            return
        pal = self.analysis.palettes[pi]
        if not pal:
            messagebox.showinfo("Palette LOCK", f"P{pi} est vide : rien à verrouiller.")
            return
        self.palette_locks[pi] = [tuple(c) for c in pal]
        self.run_analysis()
        self.status.configure(text=f"P{pi} verrouillée avec {len(pal)} couleurs RGB333.")

    def unlock_palette(self):
        try:
            pi = int(self.lock_palette_var.get()[1:])
        except Exception:
            return
        self.palette_locks.pop(pi, None)
        self.run_analysis()
        self.status.configure(text=f"P{pi} repasse en AUTO.")

    def unlock_all_palettes(self):
        self.palette_locks.clear()
        self.run_analysis()
        self.status.configure(text="Toutes les palettes repassent en AUTO.")

    def tile_for_selected_cell(self):
        if not self.analysis or not self.selected_cell:
            return None
        fi, cx, cy = self.selected_cell
        return next((t for t in self.analysis.tiles if t.frame_index == fi and t.cx == cx and t.cy == cy), None)

    def render_cell_inspector(self):
        if not hasattr(self, "cell_text"):
            return
        t = self.tile_for_selected_cell()
        if not t:
            self._set_text(self.cell_text, "Aucune cellule sélectionnée.\n\nInteraction = Inspect, puis clique une cellule du sprite.")
            self.cell_override_var.set("AUTO")
            return
        usage = tile_usage_counts(self.analysis).get(t.canonical_id, 0) if t.canonical_id is not None else 0
        colors = " ".join(rgb333_hex(c) for c in sorted(t.colors, key=rgb333_word)) or "-"
        frame = self.frames[t.frame_index]
        key = f"{t.cx},{t.cy}"
        override = frame.cell_palette_overrides.get(key, -1)
        self.cell_override_var.set("AUTO" if override < 0 else f"P{override}")
        text = (
            f"Frame : {frame.name}\n"
            f"Cellule : ({t.cx},{t.cy}) • pixels ({t.tx},{t.ty}) • {t.w}×{t.h}\n"
            f"Vide : {t.empty}\n\n"
            f"Couleurs RGB333 : {len(t.colors)}\n{colors}\n\n"
            f"Palette assignée : {'-' if t.palette_id is None else 'P'+str(t.palette_id)}\n"
            f"Forçage : {t.override_source}\n"
            f"Override explicite : {'AUTO' if override < 0 else 'P'+str(override)}\n\n"
            f"Tile canonique : {t.canonical_id}\n"
            f"Réutilisations de cette tile : {usage}\n"
            f"Flip X : {t.flip_x} • Flip Y : {t.flip_y}"
        )
        self._set_text(self.cell_text, text)

    def apply_cell_override(self):
        t = self.tile_for_selected_cell()
        if not t:
            return
        f = self.frames[t.frame_index]
        key = f"{t.cx},{t.cy}"
        p = self.cell_override_var.get()
        if p == "AUTO":
            f.cell_palette_overrides.pop(key, None)
        else:
            f.cell_palette_overrides[key] = int(p[1:])
        self.run_analysis()
        self.render_cell_inspector()

    def reset_cell_override(self):
        t = self.tile_for_selected_cell()
        if not t:
            return
        self.frames[t.frame_index].cell_palette_overrides.pop(f"{t.cx},{t.cy}", None)
        self.run_analysis()
        self.render_cell_inspector()

    def reset_frame_overrides(self):
        i = self.active_index()
        if i is None:
            return
        self.frames[i].cell_palette_overrides.clear()
        self.run_analysis()
        self.render_cell_inspector()

    def _empty_diagnostics(self):
        if hasattr(self, "card_vars"):
            for v in self.card_vars.values():
                v.set("-")
        if hasattr(self, "hardware_text"):
            self._set_text(self.hardware_text, "Importe un sprite pour lancer l'analyse.")
        if hasattr(self, "palette_text"):
            self._set_text(self.palette_text, "")
        if hasattr(self, "cell_text"):
            self._set_text(self.cell_text, "Aucune cellule sélectionnée.")
        if hasattr(self, "palette_canvas"):
            self.palette_canvas.delete("all")
        if hasattr(self, "issue_tree"):
            for item in self.issue_tree.get_children():
                self.issue_tree.delete(item)

    # ------------------------------------------------------------------
    # Project V2 / backward compatible V1
    # ------------------------------------------------------------------

    def project_data(self, path):
        base = Path(path).parent
        frames = []
        for f in self.frames:
            src = f.source
            try:
                src = os.path.relpath(src, base) if src else ""
            except Exception:
                pass
            frames.append({
                "name": f.name,
                "duration_ms": f.duration_ms,
                "source": src,
                "source_rect": list(f.source_rect) if f.source_rect else None,
                "animation": f.animation,
                "pivot_x": f.pivot_x,
                "pivot_y": f.pivot_y,
                "preferred_palette": f.preferred_palette,
                "hitbox": asdict(f.hitbox),
                "cell_palette_overrides": dict(f.cell_palette_overrides),
            })
        for fd in self.missing_frame_defs:
            item={k:v for k,v in fd.items() if not str(k).startswith("_")}
            abs_src=fd.get("_missing_source_abs",item.get("source",""))
            try: item["source"]=os.path.relpath(abs_src,base) if abs_src else ""
            except Exception: item["source"]=str(abs_src or "")
            frames.append(item)
        return {
            "format": "DMS_ASSET_PROJECT",
            "version": PROJECT_VERSION,
            "app_version": APP_VERSION,
            "project_name": self.project_var.get().strip() or "DMS_CHARACTER",
            "mode": self.mode_var.get(),
            "cell_size": int(self.cell_var.get()),
            "reserve_transparent": bool(self.transparent_var.get()),
            "palette_locks": {str(k): [list(c) for c in v] for k, v in self.palette_locks.items()},
            "frames": frames,
        }

    def save_project(self):
        if not self.project_file:
            return self.save_project_as()
        try:
            target=Path(self.project_file); tmp=target.with_name(target.name+".tmp")
            tmp.write_text(json.dumps(self.project_data(target), indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp,target)
            suffix=f" • {len(self.missing_frame_defs)} source(s) manquante(s) préservée(s)" if self.missing_frame_defs else ""
            self.status.configure(text=f"Projet sauvé : {target.name}{suffix}")
        except Exception as e:
            try: tmp.unlink(missing_ok=True)
            except Exception: pass
            messagebox.showerror("Projet", str(e))

    def save_project_as(self):
        path = filedialog.asksaveasfilename(
            title="Sauver projet DMS Asset Lab",
            defaultextension=".dproj",
            initialfile=(self.project_var.get().strip() or "DMS_CHARACTER") + ".dproj",
            filetypes=[("DMS Asset Project", "*.dproj")],
        )
        if not path:
            return
        try:
            target=Path(path); tmp=target.with_name(target.name+".tmp")
            tmp.write_text(json.dumps(self.project_data(target), indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp,target)
            self.project_file=str(target)
            self.status.configure(text=f"Projet sauvé : {target.name}")
        except Exception as e:
            try: tmp.unlink(missing_ok=True)
            except Exception: pass
            messagebox.showerror("Projet", str(e))

    def open_project(self):
        path = filedialog.askopenfilename(title="Ouvrir projet", filetypes=[("DMS Asset Project", "*.dproj"), ("JSON", "*.json")])
        if not path:
            return
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            if d.get("format") != "DMS_ASSET_PROJECT":
                raise ValueError("Fichier projet DMS invalide.")
            base = Path(path).parent
            frames = []
            missing = []
            self.missing_frame_defs = []
            for fd in d.get("frames", []):
                src = Path(fd.get("source", ""))
                src = src if src.is_absolute() else base / src
                if not src.exists():
                    missing.append(str(src))
                    kept=dict(fd); kept["_missing_source_abs"]=str(src.resolve())
                    self.missing_frame_defs.append(kept)
                    continue
                original = self.loader.load_file(str(src))
                rect = fd.get("source_rect")
                if rect:
                    x, y, w, h = map(int, rect)
                    fr = self.loader.crop_frame(original, x, y, w, h, fd.get("name", "frame"))
                else:
                    fr = original
                fr.name = fd.get("name", fr.name)
                fr.duration_ms = int(fd.get("duration_ms", 120))
                fr.animation = fd.get("animation", "IDLE")
                fr.pivot_x = int(fd.get("pivot_x", fr.width // 2))
                fr.pivot_y = int(fd.get("pivot_y", fr.height - 1))
                fr.preferred_palette = int(fd.get("preferred_palette", -1))
                fr.hitbox = Hitbox(**fd.get("hitbox", {}))
                fr.cell_palette_overrides = {
                    str(k): int(v) for k, v in fd.get("cell_palette_overrides", {}).items()
                }
                frames.append(fr)

            locks = {}
            for key, colors in d.get("palette_locks", {}).items():
                locks[int(key)] = [tuple(int(v) for v in c) for c in colors]

            self.stop_playback()
            self.frames = frames
            self.palette_locks = locks
            self.project_var.set(d.get("project_name", "DMS_CHARACTER"))
            mode = d.get("mode", "Mode 0 - STANDARD")
            self.mode_var.set(mode if mode in DMS_MODES else "Mode 0 - STANDARD")
            self.cell_var.set(int(d.get("cell_size", 8)))
            self.transparent_var.set(bool(d.get("reserve_transparent", True)))
            self.project_file = path
            self._update_mode_banner()
            self._refresh_animation_filter()
            self.refresh_frame_tree()
            if frames:
                self.select_frame(0)
            self.run_analysis()
            if missing:
                messagebox.showwarning("Projet partiel", "Sources introuvables :\n\n" + "\n".join(missing[:10]))
            self.status.configure(text=f"Projet ouvert : {Path(path).name} (format V{d.get('version', 1)})")
        except Exception as e:
            messagebox.showerror("Projet", f"Impossible d'ouvrir le projet.\n\n{e}")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _confirm_export_errors(self):
        if not self.analysis:
            return False
        errors = self.analysis.issue_counts()["ERROR"]
        if not errors:
            return True
        return messagebox.askyesno(
            "Export avec erreurs",
            f"Le diagnostic contient {errors} ERROR.\n\n"
            "Le .dres peut être exporté pour inspection, mais il n'est pas considéré prêt pour le futur GDK. Continuer ?"
        )

    def export_resource(self):
        if not self.frames:
            messagebox.showinfo("Export", "Importe au moins une frame.")
            return None
        self.run_analysis()
        if not self.analysis or not self._confirm_export_errors():
            return None
        path = filedialog.asksaveasfilename(
            title="Exporter .dres V3", defaultextension=".dres",
            initialfile=(self.project_var.get().strip() or "DMS_RESOURCE") + ".dres",
            filetypes=[("DMS Resource", "*.dres")]
        )
        if not path:
            return None
        try:
            export_dres(
                path, self.analysis, self.mode_var.get(), int(self.cell_var.get()),
                self.transparent_var.get(), self.project_var.get().strip() or "DMS_RESOURCE",
                self.palette_locks,
            )
            counts = self.analysis.issue_counts()
            messagebox.showinfo(
                "Export DMS",
                f"DRES V3 exporté.\n\n"
                f"Frames : {len(self.frames)}\nAnimations : {len(animation_map(self.frames))}\n"
                f"Tiles uniques : {len(self.analysis.unique_tiles)}\n"
                f"Validation : {counts['ERROR']} error / {counts['WARN']} warn / {counts['INFO']} info"
            )
            self.status.configure(text=f"Exporté : {Path(path).name}")
            return path
        except Exception as e:
            messagebox.showerror("Export", str(e))
            return None

    def export_report(self):
        if not self.frames:
            return
        self.run_analysis()
        if not self.analysis:
            return
        path = filedialog.asksaveasfilename(
            title="Rapport hardware", defaultextension=".txt",
            initialfile=(self.project_var.get().strip() or "DMS_RESOURCE") + "_REPORT.txt",
            filetypes=[("Text", "*.txt")]
        )
        if path:
            Path(path).write_text(
                analysis_report_text(self.analysis, self.mode_var.get(), int(self.cell_var.get()), self.transparent_var.get()),
                encoding="utf-8"
            )
            self.status.configure(text=f"Rapport exporté : {Path(path).name}")

    def export_bundle(self):
        if not self.frames:
            return
        self.run_analysis()
        if not self.analysis or not self._confirm_export_errors():
            return
        folder = filedialog.askdirectory(title="Dossier bundle futur DMS-GDK")
        if not folder:
            return
        name = self.project_var.get().strip() or "DMS_RESOURCE"
        base = safe_symbol(name).lower()
        dres = Path(folder) / f"{base}.dres"
        try:
            export_dres(
                str(dres), self.analysis, self.mode_var.get(), int(self.cell_var.get()),
                self.transparent_var.get(), name, self.palette_locks,
            )
            generate_gdk_metadata(folder, self.analysis, name, dres.name)
            self.status.configure(text="Bundle DMS-GDK V0.3 exporté.")
            messagebox.showinfo(
                "Bundle GDK",
                f"Bundle créé :\n\n{dres.name}\n{base}.h\n{base}_meta.c\n{base}_handoff.json"
            )
        except Exception as e:
            messagebox.showerror("Bundle GDK", str(e))


if __name__ == "__main__":
    DMSAssetLab().mainloop()
