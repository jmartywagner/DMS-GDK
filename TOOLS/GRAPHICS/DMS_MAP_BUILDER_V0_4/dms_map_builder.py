from __future__ import annotations

import json
import hashlib
import os
import struct
import zlib
import zipfile
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


APP_NAME = "DMS Map Builder"
APP_VERSION = "0.6.0"

# ---------------------------------------------------------------------------
# DMS-1 CONTRACTS USED BY THIS TOOL
# ---------------------------------------------------------------------------

DMS_MODES = {
    "Mode 0 - STANDARD": {
        "id": 0, "screen_w": 320, "screen_h": 224, "palettes": 4,
        "has_bg_a": True, "has_bg_b": True,
        "description": "320×224 • BG A + BG B • 4 palettes"
    },
    "Mode 1 - HIGH COLOR": {
        "id": 1, "screen_w": 320, "screen_h": 224, "palettes": 8,
        "has_bg_a": True, "has_bg_b": False,
        "description": "320×224 • BG A • 8 palettes"
    },
    "Mode 2 - SCROLL": {
        "id": 2, "screen_w": 320, "screen_h": 224, "palettes": 4,
        "has_bg_a": True, "has_bg_b": True,
        "description": "320×224 • BG A + BG B • line-scroll • 4 palettes"
    },
    "Mode 3 - SPRITE": {
        "id": 3, "screen_w": 320, "screen_h": 224, "palettes": 4,
        "has_bg_a": True, "has_bg_b": False,
        "description": "320×224 • BG A • sprites renforcés • 4 palettes"
    },
    "Mode 4 - LOW RES": {
        "id": 4, "screen_w": 256, "screen_h": 224, "palettes": 8,
        "has_bg_a": True, "has_bg_b": True,
        "description": "256×224 natif • BG A + BG B • 8 palettes"
    },
}

# Explicit priority contract requested for DMS map resources.
PRIORITY_CODES = {
    0: "BG A - derrière sprites",
    1: "BG A - devant sprites",
    2: "BG B - derrière sprites",
    3: "BG B - devant sprites",
}

COLLISION_TYPES = [
    "NONE", "SOLID", "ONE_WAY", "HAZARD", "LADDER", "WATER", "SLOW", "CUSTOM"
]
TRIGGER_TYPES = [
    "NONE", "TOUCH", "ENTER_CELL", "ACTION_BUTTON", "LEAVE_CELL", "TIMER", "CUSTOM"
]
ACTION_TYPES = [
    "NONE", "SET_PALETTE", "CHANGE_TILE", "SET_FLAG",
    "PLAY_SFX", "PLAY_MUSIC", "CHANGE_SCENE", "CUSTOM"
]
LAYER_NAMES = ["BG B", "BG A", "OBJECTS", "COLLISION", "EVENTS"]


@dataclass
class EventDef:
    enabled: bool = False
    trigger: str = "NONE"
    action: str = "NONE"
    param_a: str = ""
    param_b: str = ""
    once: bool = False
    note: str = ""


@dataclass
class Cell:
    tile_id: int = -1
    palette: int = 0
    flip_x: bool = False
    flip_y: bool = False
    priority_code: int = 0


@dataclass
class ObjectDef:
    object_id: int
    name: str
    tile_id: int
    x: int
    y: int
    palette: int = 0
    flip_x: bool = False
    flip_y: bool = False
    event: EventDef = field(default_factory=EventDef)
    note: str = ""


@dataclass
class MapState:
    name: str = "DMS_MAP"
    width: int = 40
    height: int = 28
    tile_size: int = 8
    mode: str = "Mode 0 - STANDARD"
    bg_a: list = field(default_factory=list)
    bg_b: list = field(default_factory=list)
    collisions: list = field(default_factory=list)
    events: list = field(default_factory=list)
    objects: list = field(default_factory=list)
    next_object_id: int = 1
    note: str = ""

    def init_grids(self):
        if not self.bg_a:
            self.bg_a = [[Cell(priority_code=0) for _ in range(self.width)] for __ in range(self.height)]
        if not self.bg_b:
            self.bg_b = [[Cell(priority_code=2) for _ in range(self.width)] for __ in range(self.height)]
        if not self.collisions:
            self.collisions = [["NONE" for _ in range(self.width)] for __ in range(self.height)]
        if not self.events:
            self.events = [[EventDef() for _ in range(self.width)] for __ in range(self.height)]


# ---------------------------------------------------------------------------
# SERIALIZATION / COMPAT
# ---------------------------------------------------------------------------

def cell_to_dict(c: Cell):
    return {
        "tile_id": int(c.tile_id),
        "palette": int(c.palette),
        "flip_x": bool(c.flip_x),
        "flip_y": bool(c.flip_y),
        "priority_code": int(c.priority_code),
    }


def dict_to_cell(d, layer_name):
    # V0.2 native field.
    if "priority_code" in d:
        code = int(d.get("priority_code", 0 if layer_name == "BG A" else 2))
    else:
        # V0.1 compatibility: priority was a bool.
        high = bool(d.get("priority", False))
        if layer_name == "BG A":
            code = 1 if high else 0
        else:
            code = 3 if high else 2

    return Cell(
        tile_id=int(d.get("tile_id", -1)),
        palette=int(d.get("palette", 0)),
        flip_x=bool(d.get("flip_x", False)),
        flip_y=bool(d.get("flip_y", False)),
        priority_code=code
    )


def map_to_project_dict(state: MapState, tileset_info: dict):
    return {
        "format": "DMS_MAP_PROJECT",
        "version": 2,
        "app_version": APP_VERSION,
        "map": {
            "name": state.name,
            "width": state.width,
            "height": state.height,
            "tile_size": state.tile_size,
            "mode": state.mode,
            "note": state.note,
        },
        "priority_contract": {str(k): v for k, v in PRIORITY_CODES.items()},
        "tileset": tileset_info,
        "bg_a": [[cell_to_dict(c) for c in row] for row in state.bg_a],
        "bg_b": [[cell_to_dict(c) for c in row] for row in state.bg_b],
        "collisions": state.collisions,
        "events": [[asdict(e) for e in row] for row in state.events],
        "objects": [asdict(o) for o in state.objects],
        "next_object_id": state.next_object_id,
    }


def project_dict_to_state(data):
    md = data.get("map", {})
    st = MapState(
        name=md.get("name", "DMS_MAP"),
        width=max(1, int(md.get("width", 40))),
        height=max(1, int(md.get("height", 28))),
        tile_size=int(md.get("tile_size", 8)),
        mode=md.get("mode", "Mode 0 - STANDARD"),
        note=md.get("note", "")
    )

    raw_a = data.get("bg_a", [])
    raw_b = data.get("bg_b", [])
    st.bg_a = [[dict_to_cell(c, "BG A") for c in row] for row in raw_a]
    st.bg_b = [[dict_to_cell(c, "BG B") for c in row] for row in raw_b]
    st.collisions = data.get("collisions", [])
    st.events = [[EventDef(**e) for e in row] for row in data.get("events", [])]

    st.objects = []
    for od in data.get("objects", []):
        ev = EventDef(**od.get("event", {}))
        st.objects.append(ObjectDef(
            object_id=int(od.get("object_id", 0)),
            name=od.get("name", "Object"),
            tile_id=int(od.get("tile_id", -1)),
            x=int(od.get("x", 0)),
            y=int(od.get("y", 0)),
            palette=int(od.get("palette", 0)),
            flip_x=bool(od.get("flip_x", False)),
            flip_y=bool(od.get("flip_y", False)),
            event=ev,
            note=od.get("note", "")
        ))

    st.next_object_id = int(data.get("next_object_id", 1))
    st.init_grids()

    # Defensive repair if an older/partial project has short grids.
    def repair_cells(grid, default_code):
        out = [[Cell(priority_code=default_code) for _ in range(st.width)] for __ in range(st.height)]
        for y in range(min(st.height, len(grid))):
            for x in range(min(st.width, len(grid[y]))):
                out[y][x] = grid[y][x]
        return out

    st.bg_a = repair_cells(st.bg_a, 0)
    st.bg_b = repair_cells(st.bg_b, 2)

    col = [["NONE" for _ in range(st.width)] for __ in range(st.height)]
    for y in range(min(st.height, len(st.collisions))):
        for x in range(min(st.width, len(st.collisions[y]))):
            col[y][x] = st.collisions[y][x]
    st.collisions = col

    evg = [[EventDef() for _ in range(st.width)] for __ in range(st.height)]
    for y in range(min(st.height, len(st.events))):
        for x in range(min(st.width, len(st.events[y]))):
            evg[y][x] = st.events[y][x]
    st.events = evg

    return st


# ---------------------------------------------------------------------------
# TILESET
# ---------------------------------------------------------------------------

class PNGTileset:
    """Source de tiles pour l'éditeur.

    Compatibilité :
    - PNG brut (ancien workflow) : rendu fidèle au PNG, mais banques de palette inconnues.
    - DIMG V2 : tiles 4 bpp + banques RGB333 exactes du pipeline DMS-1.
    """
    def __init__(self, master):
        self.master = master
        self.path = ""
        self.source_kind = "NONE"
        self.image = None
        self.tile_size = 8
        self.margin = 0
        self.spacing = 0
        self.columns = 0
        self.rows = 0
        self.tiles_base = []
        self.tile_patterns = []
        self.palette_banks = {}
        self.palette_ids = []
        self.tile_palette_usage = {}
        self.dimg_manifest = None
        self.png_transparency_mask = None
        self.png_transparency_mode = None
        self.generated = []
        # Caches réutilisés par le viewport. Pour DIMG, la palette fait partie de la clé.
        self._variant_cache = {}
        # Les aperçus UI et le rendu map ne partagent plus leurs PhotoImage.
        # Un changement de zoom map peut purger son cache sans faire disparaître
        # les vignettes du navigateur de tiles.
        self._display_cache_ui = {}
        self._display_cache_map = {}

    def clear(self):
        self.path = ""
        self.source_kind = "NONE"
        self.image = None
        self.tiles_base = []
        self.tile_patterns = []
        self.palette_banks = {}
        self.palette_ids = []
        self.tile_palette_usage = {}
        self.dimg_manifest = None
        self.png_transparency_mask = None
        self.png_transparency_mode = None
        self.columns = 0
        self.rows = 0
        self._variant_cache.clear()
        self._display_cache_ui.clear()
        self._display_cache_map.clear()

    @staticmethod
    def _paeth(a, b, c):
        p = a + b - c
        pa, pb, pc = abs(p-a), abs(p-b), abs(p-c)
        return a if pa <= pb and pa <= pc else (b if pb <= pc else c)

    @classmethod
    def _png_indexed_transparency_mask(cls, path):
        """Lit uniquement le masque de transparence d'un PNG indexé, sans Pillow.

        Contrat : on respecte uniquement la transparence réellement déclarée
        dans le PNG. Sans chunk tRNS, le PNG indexé est entièrement opaque.
        Retourne (largeur, hauteur, bytearray mask, mode) ou None.
        """
        try:
            data = Path(path).read_bytes()
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                return None
            pos = 8
            width = height = bitdepth = ctype = interlace = None
            raw = bytearray()
            trns = None
            while pos + 12 <= len(data):
                n = struct.unpack_from(">I", data, pos)[0]
                kind = data[pos+4:pos+8]
                payload = data[pos+8:pos+8+n]
                pos += 12 + n
                if kind == b"IHDR":
                    width, height, bitdepth, ctype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", payload)
                elif kind == b"tRNS":
                    trns = bytes(payload)
                elif kind == b"IDAT":
                    raw.extend(payload)
                elif kind == b"IEND":
                    break
            if ctype != 3 or interlace != 0 or bitdepth not in (1, 2, 4, 8) or not raw:
                return None

            stride = (width * bitdepth + 7) // 8
            dec = zlib.decompress(bytes(raw))
            expected = height * (stride + 1)
            if len(dec) < expected:
                return None

            rows = []
            curpos = 0
            prev = bytearray(stride)
            for _y in range(height):
                ftype = dec[curpos]
                curpos += 1
                cur = bytearray(dec[curpos:curpos+stride])
                curpos += stride
                for x in range(stride):
                    a = cur[x-1] if x >= 1 else 0
                    b = prev[x]
                    c = prev[x-1] if x >= 1 else 0
                    if ftype == 1:
                        cur[x] = (cur[x] + a) & 255
                    elif ftype == 2:
                        cur[x] = (cur[x] + b) & 255
                    elif ftype == 3:
                        cur[x] = (cur[x] + ((a+b)//2)) & 255
                    elif ftype == 4:
                        cur[x] = (cur[x] + cls._paeth(a,b,c)) & 255
                    elif ftype != 0:
                        return None
                rows.append(cur)
                prev = cur

            if trns is None:
                return None
            alphas = list(trns)
            mask = bytearray(width * height)
            mask_bits = (1 << bitdepth) - 1
            per_byte = 8 // bitdepth
            for y, row in enumerate(rows):
                outx = 0
                for byte in row:
                    for slot in range(per_byte):
                        if outx >= width:
                            break
                        shift = 8 - bitdepth * (slot + 1)
                        idx = (byte >> shift) & mask_bits
                        alpha = alphas[idx] if idx < len(alphas) else 255
                        if alpha < 128:
                            mask[y * width + outx] = 1
                        outx += 1
            return width, height, mask, "ALPHA"
        except Exception:
            return None

    def load(self, path, tile_size=8, margin=0, spacing=0):
        """Charge un PNG brut en appliquant exactement la transparence du pipeline DMS."""
        self.clear()
        self.path = str(path)
        self.source_kind = "PNG"
        self.tile_size = int(tile_size)
        self.margin = int(margin)
        self.spacing = int(spacing)
        parsed = self._png_indexed_transparency_mask(path)
        if parsed is not None:
            _w, _h, self.png_transparency_mask, self.png_transparency_mode = parsed
        self.image = tk.PhotoImage(master=self.master, file=str(path))
        self._slice()
        return len(self.tiles_base)

    def load_dimg(self, path):
        """Charge directement un DIMG V2 : tiles uniques + palettes RGB333."""
        self.clear()
        self.path = str(path)
        self.source_kind = "DIMG"
        with zipfile.ZipFile(path, "r") as z:
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
            if manifest.get("format") != "DIMG":
                raise ValueError("Le fichier n'est pas une ressource DIMG.")
            if int(manifest.get("format_version", 0)) < 2:
                raise ValueError("DIMG trop ancien : V2 requis pour les palettes explicites.")
            self.dimg_manifest = manifest
            self.tile_size = int(manifest.get("tiles", {}).get("tile_size", 8))
            self.margin = 0
            self.spacing = 0
            self.palette_ids = [int(x) for x in manifest.get("selected_palette_ids", [])]
            palette_entries = manifest.get("palettes", [])
            for entry in palette_entries:
                pid = int(entry.get("physical_id", 0))
                colors = []
                for c in entry.get("colors_rgb333", []):
                    if isinstance(c, (list, tuple)) and len(c) >= 3:
                        colors.append(tuple(max(0, min(7, int(v))) for v in c[:3]))
                if colors:
                    colors = colors[:16]
                    colors.extend([(0, 0, 0)] * (16 - len(colors)))
                    self.palette_banks[pid] = colors
            if not self.palette_ids and "palette_ids.bin" in z.namelist():
                self.palette_ids = [int(v) for v in z.read("palette_ids.bin")]
            if not self.palette_ids:
                self.palette_ids = sorted(self.palette_banks)

            # Compatibilité avec les DIMG V2 initiaux qui ne répétaient pas les couleurs
            # dans manifest.json : palettes.bin reste la source contractuelle exacte.
            if "palettes.bin" in z.namelist() and self.palette_ids:
                praw = z.read("palettes.bin")
                for bank_index, pid in enumerate(self.palette_ids):
                    if pid in self.palette_banks and any(self.palette_banks[pid]):
                        continue
                    start = bank_index * 16 * 2
                    if start + 32 > len(praw):
                        continue
                    colors = []
                    for i in range(16):
                        word = struct.unpack(">H", praw[start+i*2:start+i*2+2])[0]
                        colors.append(((word >> 6) & 7, (word >> 3) & 7, word & 7))
                    self.palette_banks[pid] = colors

            raw = z.read("tiles.bin")
            unique = int(manifest.get("tiles", {}).get("unique", 0))
            bytes_per_tile = self.tile_size * self.tile_size // 2
            if bytes_per_tile <= 0:
                raise ValueError("Taille de tile DIMG invalide.")
            if unique <= 0:
                unique = len(raw) // bytes_per_tile
            if len(raw) < unique * bytes_per_tile:
                raise ValueError("DIMG tronqué : tiles.bin est incomplet.")
            for tid in range(unique):
                chunk = raw[tid * bytes_per_tile:(tid + 1) * bytes_per_tile]
                vals = []
                for b in chunk:
                    vals.extend(((b >> 4) & 0x0F, b & 0x0F))
                vals = vals[:self.tile_size * self.tile_size]
                pat = [
                    vals[y * self.tile_size:(y + 1) * self.tile_size]
                    for y in range(self.tile_size)
                ]
                self.tile_patterns.append(pat)

            # Le navigateur peut indiquer dans quelles banques chaque tile est réellement utilisée.
            for entry in manifest.get("tilemap", []):
                try:
                    tid = int(entry.get("tile", -1))
                    pid = int(entry.get("palette", 0))
                except Exception:
                    continue
                if tid >= 0:
                    self.tile_palette_usage.setdefault(tid, set()).add(pid)

        self.columns = max(1, int(len(self.tile_patterns) ** 0.5)) if self.tile_patterns else 0
        self.rows = (len(self.tile_patterns) + self.columns - 1) // self.columns if self.columns else 0
        # tiles_base sert aussi de test historique "une source est chargée".
        self.tiles_base = [None] * len(self.tile_patterns)
        return len(self.tile_patterns)

    def has_palette_data(self):
        return self.source_kind == "DIMG" and bool(self.palette_banks)

    @staticmethod
    def rgb333_hex(c):
        r, g, b = (max(0, min(7, int(v))) for v in c[:3])
        rr = round(r * 255 / 7)
        gg = round(g * 255 / 7)
        bb = round(b * 255 / 7)
        return f"#{rr:02x}{gg:02x}{bb:02x}"

    def palette_colors_hex(self, palette_id):
        pal = self.palette_banks.get(int(palette_id), [])
        return [self.rgb333_hex(c) for c in pal]

    def _slice(self):
        self.tiles_base = []
        self.generated = []
        self.tile_patterns = []
        self._variant_cache.clear()
        self._display_cache_ui.clear()
        self._display_cache_map.clear()
        if not self.image:
            return
        ts = self.tile_size
        step = ts + self.spacing
        usable_w = max(0, self.image.width() - self.margin * 2)
        usable_h = max(0, self.image.height() - self.margin * 2)
        self.columns = max(0, (usable_w + self.spacing) // step)
        self.rows = max(0, (usable_h + self.spacing) // step)

        for row in range(self.rows):
            for col in range(self.columns):
                x0 = self.margin + col * step
                y0 = self.margin + row * step
                if x0 + ts > self.image.width() or y0 + ts > self.image.height():
                    continue
                out = tk.PhotoImage(master=self.master, width=ts, height=ts)
                out.tk.call(out, "copy", self.image, "-from", x0, y0, x0+ts, y0+ts, "-to", 0, 0)
                # PNG indexé : applique le masque exact par INDEX, pas par couleur RGB.
                # Ainsi un noir opaque à un autre index reste bien opaque.
                if self.png_transparency_mask is not None:
                    src_w = self.image.width()
                    for py in range(ts):
                        gy = y0 + py
                        for px in range(ts):
                            gx = x0 + px
                            if self.png_transparency_mask[gy * src_w + gx]:
                                out.transparency_set(px, py, True)
                self.tiles_base.append(out)

    def _dimg_base_tile(self, tile_id, palette_id):
        if tile_id < 0 or tile_id >= len(self.tile_patterns):
            return None
        if palette_id not in self.palette_banks:
            palette_id = self.palette_ids[0] if self.palette_ids else 0
        key = ("DIMG_BASE", int(tile_id), int(palette_id))
        cached = self._variant_cache.get(key)
        if cached is not None:
            return cached
        pal = self.palette_banks.get(int(palette_id), [(0,0,0)] * 16)
        colors = [self.rgb333_hex(c) for c in pal]
        colors.extend(["#000000"] * (16 - len(colors)))
        pat = self.tile_patterns[tile_id]
        out = tk.PhotoImage(master=self.master, width=self.tile_size, height=self.tile_size)
        # Contrat DMS/VDP : l'index 0 de CHAQUE palette est le slot transparent.
        # Le DIMG ne stocke plus d'alpha (seulement les indices 4 bpp), donc l'éditeur
        # doit reconstruire cette transparence pour que BG A laisse réellement voir BG B.
        for y, row in enumerate(pat):
            indices = [v & 0x0F for v in row]
            out.put("{" + " ".join(colors[v] for v in indices) + "}", to=(0, y))
            for x, idx in enumerate(indices):
                if idx == 0:
                    out.transparency_set(x, y, True)
        self._variant_cache[key] = out
        return out

    def tile(self, tile_id, flip_x=False, flip_y=False, palette_id=None):
        count = len(self.tile_patterns) if self.source_kind == "DIMG" else len(self.tiles_base)
        if tile_id < 0 or tile_id >= count:
            return None
        pal_key = int(palette_id) if self.source_kind == "DIMG" and palette_id is not None else None
        key = (int(tile_id), bool(flip_x), bool(flip_y), pal_key)
        cached = self._variant_cache.get(key)
        if cached is not None:
            return cached
        if self.source_kind == "DIMG":
            src = self._dimg_base_tile(tile_id, pal_key)
        else:
            src = self.tiles_base[tile_id]
        if src is None:
            return None
        if not flip_x and not flip_y:
            self._variant_cache[key] = src
            return src

        ts = self.tile_size
        out = tk.PhotoImage(master=self.master, width=ts, height=ts)
        # Tk peut faire le flip en C/Tcl via subsample négatif, mais ce chemin explicite
        # reste compatible avec les versions Tk Windows utilisées par le GDK.
        for y in range(ts):
            sy = ts - 1 - y if flip_y else y
            row_colors = []
            transparent_x = []
            for x in range(ts):
                sx = ts - 1 - x if flip_x else x
                c = src.get(sx, sy)
                if isinstance(c, str):
                    row_colors.append(c)
                else:
                    vals = tuple(int(v) for v in c[:3])
                    row_colors.append("#%02x%02x%02x" % vals)
                # Un flip ne doit jamais transformer les pixels transparents en noir opaque.
                try:
                    if src.transparency_get(sx, sy):
                        transparent_x.append(x)
                except tk.TclError:
                    pass
            out.put("{" + " ".join(row_colors) + "}", to=(0, y))
            for x in transparent_x:
                out.transparency_set(x, y, True)
        self._variant_cache[key] = out
        return out

    def display_tile(self, tile_id, flip_x=False, flip_y=False, zoom=1, palette_id=None, cache_group="ui"):
        """Retourne une variante agrandie et mise en cache.

        ``ui`` conserve les vignettes/aperçus indépendamment du zoom de la map.
        ``map`` peut être purgé à chaque changement de zoom sans toucher à l'UI.
        """
        pal_key = int(palette_id) if self.source_kind == "DIMG" and palette_id is not None else None
        key = (int(tile_id), bool(flip_x), bool(flip_y), int(zoom), pal_key)
        cache = self._display_cache_map if cache_group == "map" else self._display_cache_ui
        cached = cache.get(key)
        if cached is not None:
            return cached
        base = self.tile(tile_id, flip_x, flip_y, palette_id)
        if base is None:
            return None
        z = max(1, int(zoom))
        out = base.zoom(z, z) if z > 1 else base
        cache[key] = out
        return out


class TilesetLibrary:
    """Bibliothèque de sources graphiques à IDs de tiles stables.

    Les cellules de map continuent de stocker un simple tile_id 0..1023.
    Chaque source DIMG conserve une table local->global ; ajouter/recharger une
    source ne décale donc jamais les IDs déjà peints.

    Les banques RGB333 sont fusionnées par *contenu*, jamais écrasées :
    - même Pn + mêmes 16 couleurs -> partage sans risque ;
    - conflit -> banque identique existante, sinon banque physique libre ;
    - aucune banque libre -> ajout refusé.
    """
    def __init__(self, master):
        self.master = master
        self.sources = []
        self.global_to_local = {}
        self.next_tile_id = 0
        self.tile_size = 8
        self.margin = 0
        self.spacing = 0
        self.palette_banks = {}
        self.palette_ids = []
        self.tile_palette_usage = {}
        self._variant_cache = {}
        self._display_cache_ui = {}
        self._display_cache_map = {}

    @property
    def tiles_base(self):
        # Compatibilité avec le reste de l'éditeur : longueur = espace global.
        return [None] * self.next_tile_id

    @property
    def path(self):
        return self.sources[0]["path"] if self.sources else ""

    @property
    def source_kind(self):
        if not self.sources:
            return "NONE"
        if len(self.sources) == 1:
            return self.sources[0]["kind"]
        return "LIBRARY"

    def clear(self):
        self.sources.clear()
        self.global_to_local.clear()
        self.next_tile_id = 0
        self.tile_size = 8
        self.margin = 0
        self.spacing = 0
        self.palette_banks.clear()
        self.palette_ids.clear()
        self.tile_palette_usage.clear()
        self._variant_cache.clear()
        self._display_cache_ui.clear()
        self._display_cache_map.clear()

    def clear_map_display_cache(self):
        self._display_cache_map.clear()

    @staticmethod
    def _pal_sig(pal):
        vals = [tuple(int(v) for v in c[:3]) for c in (pal or [])[:16]]
        vals.extend([(0,0,0)] * (16-len(vals)))
        # Le slot 0 est matériellement transparent : sa valeur RGB n'est jamais visible
        # et ne doit pas provoquer un faux conflit entre deux banques identiques.
        if vals: vals[0]=(0,0,0)
        return tuple(vals)

    @staticmethod
    def rgb333_hex(c):
        return PNGTileset.rgb333_hex(c)

    def palette_colors_hex(self, palette_id):
        return [self.rgb333_hex(c) for c in self.palette_banks.get(int(palette_id), [])]

    def has_palette_data(self):
        return bool(self.palette_banks)

    def _source_label(self, rec):
        return f"{rec['name']}  ({len(rec['tile_ids'])} tiles)"

    def source_filter_values(self):
        return ["Tous les tilesets"] + [self._source_label(r) for r in self.sources]

    def source_from_filter(self, label):
        if not label or label == "Tous les tilesets":
            return None
        for r in self.sources:
            if self._source_label(r) == label:
                return r
        return None

    def source_for_tile(self, tile_id):
        hit = self.global_to_local.get(int(tile_id))
        return hit[0] if hit else None

    def local_for_tile(self, tile_id):
        hit = self.global_to_local.get(int(tile_id))
        return hit[1] if hit else None

    def visible_tile_ids(self, source_filter="Tous les tilesets"):
        rec = self.source_from_filter(source_filter)
        if rec is not None:
            return list(rec["tile_ids"])
        ids=[]
        for r in self.sources:
            ids.extend(r["tile_ids"])
        return ids

    def _rebuild_indices_and_palettes(self):
        self.global_to_local.clear()
        self.tile_palette_usage.clear()
        merged={}
        for rec in self.sources:
            ts=rec["tileset"]
            pmap={int(k):int(v) for k,v in rec.get("palette_map",{}).items()}
            for spid, ppid in pmap.items():
                pal=ts.palette_banks.get(spid)
                if pal:
                    sig=self._pal_sig(pal)
                    if ppid in merged and self._pal_sig(merged[ppid]) != sig:
                        raise ValueError(f"Conflit palette interne : deux sources réclament P{ppid} avec des couleurs différentes.")
                    merged[ppid]=list(sig)
            for local,gid in enumerate(rec["tile_ids"]):
                if local >= len(ts.tiles_base):
                    continue
                self.global_to_local[int(gid)] = (rec, local)
                used=ts.tile_palette_usage.get(local,set())
                mapped={pmap.get(int(pid),int(pid)) for pid in used}
                if mapped:
                    self.tile_palette_usage[int(gid)] = mapped
        self.palette_banks=merged
        self.palette_ids=sorted(merged)
        self._variant_cache.clear(); self._display_cache_ui.clear(); self._display_cache_map.clear()

    def _plan_palette_map(self, ts, palette_limit, preferred=None, excluding_uid=None):
        palette_limit=max(1,min(8,int(palette_limit)))
        preferred={int(k):int(v) for k,v in (preferred or {}).items()}
        occupied={}
        for rec in self.sources:
            if excluding_uid and rec.get("uid")==excluding_uid:
                continue
            rts=rec["tileset"]
            for spid,ppid in rec.get("palette_map",{}).items():
                pal=rts.palette_banks.get(int(spid))
                if pal:
                    occupied[int(ppid)] = self._pal_sig(pal)
        plan={}; notes=[]
        for spid in ts.palette_ids:
            spid=int(spid)
            sig=self._pal_sig(ts.palette_banks.get(spid,[]))
            if not sig:
                continue
            pref=preferred.get(spid)
            if pref is not None and 0 <= pref < palette_limit and (pref not in occupied or occupied[pref]==sig):
                target=pref
            elif 0 <= spid < palette_limit and (spid not in occupied or occupied[spid]==sig):
                target=spid
            else:
                same=next((pid for pid,osig in occupied.items() if osig==sig and pid < palette_limit),None)
                if same is not None:
                    target=same
                else:
                    free=next((pid for pid in range(palette_limit) if pid not in occupied and pid not in plan.values()),None)
                    if free is None:
                        conflicts=", ".join(f"P{k}" for k in sorted(occupied)) or "aucune"
                        raise ValueError(
                            f"Impossible d'ajouter ce tileset : plus aucune banque libre sur ce mode ({palette_limit} banques).\n"
                            f"Banques déjà occupées : {conflicts}.\n"
                            f"La source demande une palette incompatible pour P{spid}."
                        )
                    target=free
            plan[spid]=target
            occupied[target]=sig
            if target != spid:
                notes.append(f"P{spid} source → P{target} projet")
        return plan,notes

    def _new_source_record(self, path, ts, tile_ids, palette_map):
        norm=str(Path(path).resolve()).replace("\\","/").casefold().encode("utf-8")
        uid=f"src_{len(self.sources)+1}_{hashlib.sha1(norm).hexdigest()[:10]}"
        return {
            "uid":uid,"name":Path(path).stem,"path":str(Path(path).resolve()),
            "kind":ts.source_kind,"tileset":ts,"tile_ids":[int(x) for x in tile_ids],
            "palette_map":{int(k):int(v) for k,v in palette_map.items()},
            "tile_size":ts.tile_size,"margin":ts.margin,"spacing":ts.spacing,
        }

    def _allocate_ids(self, count):
        count=int(count)
        if self.next_tile_id + count > 1024:
            raise ValueError(f"Budget tiles dépassé : {self.next_tile_id}+{count} > 1024 IDs matériels.")
        ids=list(range(self.next_tile_id,self.next_tile_id+count))
        self.next_tile_id += count
        return ids

    def add_dimg(self, path, palette_limit=4, preferred_map=None):
        ts=PNGTileset(self.master); ts.load_dimg(path)
        if self.sources and ts.tile_size != self.tile_size:
            raise ValueError(f"Taille de tile incompatible : bibliothèque {self.tile_size}px, source {ts.tile_size}px.")
        plan,notes=self._plan_palette_map(ts,palette_limit,preferred_map)
        ids=self._allocate_ids(len(ts.tiles_base))
        rec=self._new_source_record(path,ts,ids,plan)
        self.sources.append(rec)
        self.tile_size=ts.tile_size
        self._rebuild_indices_and_palettes()
        return rec,notes

    def add_png(self, path, tile_size=8, margin=0, spacing=0):
        if self.sources:
            raise ValueError("Le PNG brut reste un workflow mono-source. Utilise Image Converter pour créer un .dimg avant de l'ajouter à la bibliothèque.")
        ts=PNGTileset(self.master); ts.load(path,tile_size,margin,spacing)
        ids=self._allocate_ids(len(ts.tiles_base))
        rec=self._new_source_record(path,ts,ids,{})
        self.sources.append(rec); self.tile_size=ts.tile_size; self.margin=ts.margin; self.spacing=ts.spacing
        self._rebuild_indices_and_palettes()
        return rec,[]

    def load(self, path, tile_size=8, margin=0, spacing=0):
        self.clear(); rec,_=self.add_png(path,tile_size,margin,spacing); return len(rec["tile_ids"])

    def load_dimg(self, path, palette_limit=8):
        self.clear(); rec,_=self.add_dimg(path,palette_limit); return len(rec["tile_ids"])

    def add_dimg_with_ids(self, path, tile_ids, palette_map, palette_limit=8, uid=None, name=None):
        ts=PNGTileset(self.master); ts.load_dimg(path)
        ids=[int(x) for x in tile_ids]
        if len(ids) != len(ts.tiles_base):
            # Une source a grandi/rétréci depuis la sauvegarde : préserver les IDs existants,
            # allouer uniquement les nouvelles tiles ; les anciennes ne bougent jamais.
            if len(ids) < len(ts.tiles_base):
                self.next_tile_id=max(self.next_tile_id,max(ids,default=-1)+1)
                ids += self._allocate_ids(len(ts.tiles_base)-len(ids))
            else:
                ids=ids[:len(ts.tiles_base)]
        if any(x<0 or x>1023 for x in ids) or len(set(ids))!=len(ids):
            raise ValueError("Table d'IDs de tiles invalide dans le projet.")
        collisions=[x for x in ids if x in self.global_to_local]
        if collisions:
            raise ValueError("IDs de tiles dupliqués entre sources : " + ", ".join(map(str,collisions[:12])))
        self.next_tile_id=max(self.next_tile_id,max(ids,default=-1)+1)
        preferred={int(k):int(v) for k,v in (palette_map or {}).items()}
        plan,notes=self._plan_palette_map(ts,palette_limit,preferred)
        rec=self._new_source_record(path,ts,ids,plan)
        if uid: rec["uid"]=str(uid)
        if name: rec["name"]=str(name)
        self.sources.append(rec); self.tile_size=ts.tile_size
        self._rebuild_indices_and_palettes()
        return rec,notes

    def reload_source(self, rec, palette_limit=4):
        if rec not in self.sources:
            raise ValueError("Source inconnue.")
        path=rec["path"]
        ts=PNGTileset(self.master)
        if rec["kind"] == "DIMG": ts.load_dimg(path)
        else: ts.load(path,rec.get("tile_size",8),rec.get("margin",0),rec.get("spacing",0))
        if ts.tile_size != self.tile_size:
            raise ValueError(f"La source rechargée est en {ts.tile_size}px, bibliothèque en {self.tile_size}px.")
        old_map=dict(rec.get("palette_map",{}))
        if rec["kind"] == "DIMG":
            new_map,notes=self._plan_palette_map(ts,palette_limit,old_map,excluding_uid=rec["uid"])
        else:
            new_map,notes={},[]
        old_ids=list(rec["tile_ids"])
        if len(ts.tiles_base) > len(old_ids):
            old_ids += self._allocate_ids(len(ts.tiles_base)-len(old_ids))
        elif len(ts.tiles_base) < len(old_ids):
            # IDs excédentaires sont retirés de la source mais jamais réattribués.
            old_ids=old_ids[:len(ts.tiles_base)]
        rec["tileset"]=ts; rec["tile_ids"]=old_ids; rec["palette_map"]=new_map
        rec["tile_size"]=ts.tile_size; rec["margin"]=ts.margin; rec["spacing"]=ts.spacing
        self._rebuild_indices_and_palettes()
        return old_map,new_map,notes

    def tile(self, tile_id, flip_x=False, flip_y=False, palette_id=None):
        tile_id=int(tile_id)
        hit=self.global_to_local.get(tile_id)
        if not hit: return None
        rec,local=hit; ts=rec["tileset"]
        if ts.source_kind != "DIMG":
            return ts.tile(local,flip_x,flip_y,palette_id)
        ppid=int(palette_id) if palette_id is not None else None
        if ppid is None or ppid not in self.palette_banks:
            usages=sorted(self.tile_palette_usage.get(tile_id,[]))
            ppid=usages[0] if usages else (self.palette_ids[0] if self.palette_ids else 0)
        key=(tile_id,bool(flip_x),bool(flip_y),ppid)
        cached=self._variant_cache.get(key)
        if cached is not None: return cached
        if local >= len(ts.tile_patterns): return None
        pat=ts.tile_patterns[local]; pal=self.palette_banks.get(ppid,[(0,0,0)]*16)
        colors=[self.rgb333_hex(c) for c in pal]; colors.extend(["#000000"]*(16-len(colors)))
        base=tk.PhotoImage(master=self.master,width=self.tile_size,height=self.tile_size)
        for y,row in enumerate(pat):
            idxs=[v&15 for v in row]
            base.put("{"+" ".join(colors[v] for v in idxs)+"}",to=(0,y))
            for x,idx in enumerate(idxs):
                if idx==0: base.transparency_set(x,y,True)
        if not flip_x and not flip_y:
            self._variant_cache[key]=base; return base
        out=tk.PhotoImage(master=self.master,width=self.tile_size,height=self.tile_size)
        for y in range(self.tile_size):
            sy=self.tile_size-1-y if flip_y else y
            vals=[]; trans=[]
            for x in range(self.tile_size):
                sx=self.tile_size-1-x if flip_x else x
                c=base.get(sx,sy)
                vals.append(c if isinstance(c,str) else "#%02x%02x%02x"%tuple(int(v) for v in c[:3]))
                try:
                    if base.transparency_get(sx,sy): trans.append(x)
                except tk.TclError: pass
            out.put("{"+" ".join(vals)+"}",to=(0,y))
            for x in trans: out.transparency_set(x,y,True)
        self._variant_cache[key]=out; return out

    def display_tile(self,tile_id,flip_x=False,flip_y=False,zoom=1,palette_id=None,cache_group="ui"):
        key=(int(tile_id),bool(flip_x),bool(flip_y),int(zoom),None if palette_id is None else int(palette_id))
        cache=self._display_cache_map if cache_group=="map" else self._display_cache_ui
        if key in cache: return cache[key]
        base=self.tile(tile_id,flip_x,flip_y,palette_id)
        if base is None: return None
        z=max(1,int(zoom)); out=base.zoom(z,z) if z>1 else base
        cache[key]=out; return out

    def source_metadata(self, project_path=None):
        out=[]
        for rec in self.sources:
            path=rec["path"]
            if project_path:
                try: path=os.path.relpath(path,Path(project_path).parent)
                except Exception: pass
            out.append({
                "uid":rec["uid"],"name":rec["name"],"path":path,"source_kind":rec["kind"],
                "tile_size":rec.get("tile_size",self.tile_size),"margin":rec.get("margin",0),"spacing":rec.get("spacing",0),
                "tile_ids":list(rec["tile_ids"]),
                "palette_map":{str(k):int(v) for k,v in rec.get("palette_map",{}).items()},
            })
        return out

    @staticmethod
    def _pack_4bpp(pattern):
        flat=[int(v)&15 for row in pattern for v in row]
        out=bytearray()
        for i in range(0,len(flat),2):
            out.append((flat[i]<<4) | (flat[i+1] if i+1<len(flat) else 0))
        return bytes(out)

    def export_consolidated_dimg(self, path, mode_name):
        if not self.sources:
            raise ValueError("Aucun tileset chargé.")
        if any(r["kind"] != "DIMG" for r in self.sources):
            raise ValueError("Une bibliothèque contenant un PNG brut ne peut pas être consolidée. Convertis-le d'abord en .dimg.")
        limit=int(DMS_MODES[mode_name]["palettes"])
        if any(pid>=limit for pid in self.palette_ids):
            raise ValueError(f"Palette hors mode : ce mode autorise P0..P{limit-1}.")
        count=max(self.next_tile_id,1)
        if count>1024: raise ValueError("Plus de 1024 IDs de tiles : export matériel impossible.")
        blank=[[0]*self.tile_size for _ in range(self.tile_size)]
        patterns=[blank for _ in range(count)]
        preferred_pal=[self.palette_ids[0] if self.palette_ids else 0 for _ in range(count)]
        for gid,(rec,local) in self.global_to_local.items():
            ts=rec["tileset"]
            if local < len(ts.tile_patterns): patterns[gid]=ts.tile_patterns[local]
            uses=sorted(self.tile_palette_usage.get(gid,[]))
            if uses: preferred_pal[gid]=uses[0]
        manifest={
            "format":"DIMG","format_version":2,"generator":f"{APP_NAME} {APP_VERSION}",
            "source":"Map Builder multi-tileset consolidation",
            "mode":{"name":mode_name,**DMS_MODES[mode_name]},"settings":{"library_consolidated":True,"preserve_tile_order":True},
            "rgb_format":"RGB333","bpp":4,"selected_palette_ids":list(self.palette_ids),
            "palette_count":len(self.palette_ids),"colors_per_bank":16,
            "palettes":[{"physical_id":pid,"colors_rgb333":[list(c) for c in self.palette_banks[pid]],"count":16}
                        for pid in self.palette_ids],
            "tiles":{"tile_size":self.tile_size,"preserve_source_cell_order":True,"total":count,"unique":count,
                     "duplicates":0,"flip_reused":0,"vram_bytes_estimate":count*(self.tile_size*self.tile_size//2)},
            "tilemap":[{"tile":gid,"palette":preferred_pal[gid],"flip_x":False,"flip_y":False} for gid in range(count)],
            "library_sources":[{"name":r["name"],"tile_ids":list(r["tile_ids"]),"palette_map":r["palette_map"]} for r in self.sources],
            "warnings":[],
        }
        palette_bin=bytearray(); palette_ids_bin=bytearray()
        for pid in self.palette_ids:
            palette_ids_bin.append(pid&255)
            pal=list(self.palette_banks[pid])[:16]; pal.extend([(0,0,0)]*(16-len(pal)))
            for rr,gg,bb in pal:
                palette_bin += struct.pack(">H",((int(rr)&7)<<6)|((int(gg)&7)<<3)|(int(bb)&7))
        tiles_bin=b"".join(self._pack_4bpp(pat) for pat in patterns)
        tmap=bytearray(); pmap=bytearray()
        for gid in range(count):
            pid=int(preferred_pal[gid])&7
            tmap += struct.pack(">H",(gid&0x03ff)|(pid<<10)); pmap.append(pid)
        with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json",json.dumps(manifest,indent=2,ensure_ascii=False))
            z.writestr("palette_ids.bin",bytes(palette_ids_bin)); z.writestr("palettes.bin",bytes(palette_bin))
            z.writestr("tiles.bin",tiles_bin); z.writestr("tilemap.bin",bytes(tmap)); z.writestr("palette_map.bin",bytes(pmap))
            z.writestr("report.txt",f"DMS Map Builder - tileset consolidé\nSources : {len(self.sources)}\nTiles : {count}\nPalettes : {', '.join('P'+str(x) for x in self.palette_ids)}\n")
            z.writestr("README.txt","DIMG V2 consolidé automatiquement par DMS Map Builder. IDs stables du projet conservés.\n")
        return str(path)


# ---------------------------------------------------------------------------
# CORE HELPERS
# ---------------------------------------------------------------------------

def cell_signature(c: Cell):
    return (c.tile_id, c.palette, c.flip_x, c.flip_y, c.priority_code)


def expected_priority_code(layer, front):
    if layer == "BG A":
        return 1 if front else 0
    if layer == "BG B":
        return 3 if front else 2
    return 0


def normalize_priority_code(layer, code):
    if layer == "BG A":
        return 1 if int(code) == 1 else 0
    if layer == "BG B":
        return 3 if int(code) == 3 else 2
    return int(code)


def flood_fill_cells(grid, x, y, replacement: Cell, before_change=None):
    """
    O(N) iterative fill. Marks cells when they are queued, so a cell cannot
    be added thousands of times. This fixes the V0.1 freeze risk.
    Returns number of modified cells.
    """
    h = len(grid)
    w = len(grid[0]) if h else 0
    if not (0 <= x < w and 0 <= y < h):
        return 0

    target_sig = cell_signature(grid[y][x])
    repl_sig = cell_signature(replacement)
    if target_sig == repl_sig:
        return 0

    q = deque([(x, y)])
    queued = {(x, y)}
    changed = 0

    while q:
        cx, cy = q.popleft()
        if cell_signature(grid[cy][cx]) != target_sig:
            continue

        if before_change is not None:
            before_change(cx, cy)
        grid[cy][cx] = deepcopy(replacement)
        changed += 1

        for nx, ny in ((cx+1,cy), (cx-1,cy), (cx,cy+1), (cx,cy-1)):
            if 0 <= nx < w and 0 <= ny < h and (nx,ny) not in queued:
                if cell_signature(grid[ny][nx]) == target_sig:
                    queued.add((nx,ny))
                    q.append((nx,ny))
    return changed


def build_export_manifest(state: MapState, tileset_info: dict):
    return {
        "format": "DMAP",
        "format_version": 2,
        "generator": f"{APP_NAME} {APP_VERSION}",
        "priority_contract": {str(k): v for k, v in PRIORITY_CODES.items()},
        "map": {
            "name": state.name,
            "width_cells": state.width,
            "height_cells": state.height,
            "tile_size": state.tile_size,
            "pixel_width": state.width * state.tile_size,
            "pixel_height": state.height * state.tile_size,
            "mode": state.mode,
            "mode_spec": DMS_MODES[state.mode],
            "note": state.note,
        },
        "tileset": tileset_info,
        "layers": {
            "BG_A": [[cell_to_dict(c) for c in row] for row in state.bg_a],
            "BG_B": [[cell_to_dict(c) for c in row] for row in state.bg_b],
            "COLLISION": state.collisions,
            "EVENTS": [[asdict(e) for e in row] for row in state.events],
        },
        "objects": [asdict(o) for o in state.objects],
        "render_order": [
            "BG_B priority_code=2",
            "BG_A priority_code=0",
            "OBJECTS / SPRITES",
            "BG_B priority_code=3",
            "BG_A priority_code=1",
        ],
        "gdk_handoff": {
            "priority_code_is_explicit": True,
            "gdk_must_apply_priority_code": True,
            "final_vdp_register_encoding_is_not_defined_by_dmap": True,
        },
    }


def interim_attr_word(cell: Cell):
    """
    Tool/GDK handoff packing only.
    bits 0..9  tile id
    bits 10..12 palette
    bit 13 flip X
    bit 14 flip Y
    bit 15 front-of-sprites flag
    Layer identity remains implicit in bg_a.bin/bg_b.bin.
    Exact priority code is also exported in priority_a.bin/priority_b.bin.
    """
    tid = max(0, min(1023, int(cell.tile_id if cell.tile_id >= 0 else 0)))
    pal = max(0, min(7, int(cell.palette)))
    front = 1 if cell.priority_code in (1, 3) else 0
    return (
        (tid & 0x03FF)
        | ((pal & 0x07) << 10)
        | ((1 if cell.flip_x else 0) << 13)
        | ((1 if cell.flip_y else 0) << 14)
        | (front << 15)
    )


def export_dmap(path, state: MapState, tileset_info: dict):
    manifest = build_export_manifest(state, tileset_info)

    bg_a_bin = bytearray()
    bg_b_bin = bytearray()
    pa = bytearray()
    pb = bytearray()

    for row in state.bg_a:
        for c in row:
            bg_a_bin += struct.pack(">H", interim_attr_word(c))
            pa.append(int(c.priority_code) & 0xFF)

    for row in state.bg_b:
        for c in row:
            bg_b_bin += struct.pack(">H", interim_attr_word(c))
            pb.append(int(c.priority_code) & 0xFF)

    collision_ids = {name: i for i, name in enumerate(COLLISION_TYPES)}
    collision_bin = bytes(
        collision_ids.get(state.collisions[y][x], collision_ids["CUSTOM"]) & 0xFF
        for y in range(state.height)
        for x in range(state.width)
    )

    events = []
    for y in range(state.height):
        for x in range(state.width):
            e = state.events[y][x]
            if e.enabled:
                events.append({"x": x, "y": y, **asdict(e)})

    report = build_report_text(state)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        z.writestr("bg_a.bin", bytes(bg_a_bin))
        z.writestr("bg_b.bin", bytes(bg_b_bin))
        z.writestr("priority_a.bin", bytes(pa))
        z.writestr("priority_b.bin", bytes(pb))
        z.writestr("collision.bin", collision_bin)
        z.writestr("events.json", json.dumps(events, indent=2, ensure_ascii=False))
        z.writestr("objects.json", json.dumps([asdict(o) for o in state.objects], indent=2, ensure_ascii=False))
        z.writestr("map_report.txt", report)
        z.writestr(
            "README.txt",
            "DMAP V2 - DMS Map Builder.\n"
            "Priority codes are explicit: 0=A rear, 1=A front, 2=B rear, 3=B front.\n"
            "The GDK applies these logical codes to the final VDP implementation.\n"
        )


def build_report_text(state):
    count_a = sum(1 for row in state.bg_a for c in row if c.tile_id >= 0)
    count_b = sum(1 for row in state.bg_b for c in row if c.tile_id >= 0)
    front_a = sum(1 for row in state.bg_a for c in row if c.tile_id >= 0 and c.priority_code == 1)
    front_b = sum(1 for row in state.bg_b for c in row if c.tile_id >= 0 and c.priority_code == 3)
    coll = sum(1 for row in state.collisions for c in row if c != "NONE")
    evs = sum(1 for row in state.events for e in row if e.enabled)
    return (
        "DMS MAP BUILDER - MAP REPORT\n"
        "============================\n"
        f"Map : {state.name}\n"
        f"Mode : {state.mode}\n"
        f"Size : {state.width}×{state.height} cells / tile {state.tile_size}px\n"
        f"BG A painted : {count_a}\n"
        f"BG A front sprites (code 1) : {front_a}\n"
        f"BG B painted : {count_b}\n"
        f"BG B front sprites (code 3) : {front_b}\n"
        f"Objects : {len(state.objects)}\n"
        f"Collision cells : {coll}\n"
        f"Event cells : {evs}\n\n"
        "PRIORITY CONTRACT\n"
        "0 = BG A behind sprites\n"
        "1 = BG A in front of sprites\n"
        "2 = BG B behind sprites\n"
        "3 = BG B in front of sprites\n"
    )


def export_gdk_bundle(folder, dmap_path, state: MapState):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in state.name.upper()).strip("_") or "DMS_MAP"
    target = folder / f"{safe.lower()}.dmap"
    if Path(dmap_path).resolve() != target.resolve():
        target.write_bytes(Path(dmap_path).read_bytes())

    h = [
        "#pragma once",
        "",
        f"/* Generated by {APP_NAME} {APP_VERSION} */",
        "#define DMS_PRIO_BG_A_BEHIND 0",
        "#define DMS_PRIO_BG_A_FRONT  1",
        "#define DMS_PRIO_BG_B_BEHIND 2",
        "#define DMS_PRIO_BG_B_FRONT  3",
        "",
        f"#define {safe}_WIDTH {state.width}",
        f"#define {safe}_HEIGHT {state.height}",
        f"#define {safe}_TILE_SIZE {state.tile_size}",
        f"#define {safe}_OBJECT_COUNT {len(state.objects)}",
        "",
        "/* Future DMS-GDK resource compiler consumes the .dmap directly. */",
    ]
    (folder / f"{safe.lower()}.h").write_text("\n".join(h), encoding="utf-8")
    (folder / f"{safe.lower()}_report.txt").write_text(build_report_text(state), encoding="utf-8")


# ---------------------------------------------------------------------------
# APPLICATION
# ---------------------------------------------------------------------------

class DMSMapBuilder(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1720x960")
        self.minsize(1240, 760)
        self.configure(bg="#17191d")

        self.state = MapState()
        self.state.init_grids()
        self.tileset = TilesetLibrary(self)
        self.project_path = None

        self.selected_tile_id = -1
        self.selected_cell = None
        self.selected_object_id = None

        self.current_tool = tk.StringVar(value="BRUSH")
        self.active_layer = tk.StringVar(value="BG A")
        self.palette_var = tk.IntVar(value=0)
        self.flip_x_var = tk.BooleanVar(value=False)
        self.flip_y_var = tk.BooleanVar(value=False)
        self.front_var = tk.BooleanVar(value=False)
        self.priority_code_var = tk.StringVar(value="0")

        self.grid_var = tk.BooleanVar(value=True)
        self.tech_overlay_var = tk.BooleanVar(value=False)  # OFF by default: clean visual
        self.camera_var = tk.BooleanVar(value=True)
        self.show_bg_a_var = tk.BooleanVar(value=True)
        self.show_bg_b_var = tk.BooleanVar(value=True)
        self.show_objects_var = tk.BooleanVar(value=True)
        self.show_collision_var = tk.BooleanVar(value=False)
        self.show_events_var = tk.BooleanVar(value=True)
        self.show_editor_guides_var = tk.BooleanVar(value=True)

        self.name_var = tk.StringVar(value=self.state.name)
        self.mode_var = tk.StringVar(value=self.state.mode)
        self.map_w_var = tk.IntVar(value=self.state.width)
        self.map_h_var = tk.IntVar(value=self.state.height)
        self.tile_size_var = tk.IntVar(value=self.state.tile_size)

        self.ts_tile_var = tk.IntVar(value=8)
        self.ts_margin_var = tk.IntVar(value=0)
        self.ts_spacing_var = tk.IntVar(value=0)

        self.zoom = 2
        self.zoom_var = tk.StringVar(value="2×")
        self.tileset_thumb_var = tk.StringVar(value="64 px")
        self.tileset_source_var = tk.StringVar(value="Tous les tilesets")
        self.selected_preview_keepalive = []
        self.tile_preview_window = None
        self.tile_preview_popup_canvas = None
        self.flip_x_text_var = tk.StringVar(value="↔ Flip horizontal : NON")
        self.flip_y_text_var = tk.StringVar(value="↕ Flip vertical : NON")
        self.map_img_keepalive = []
        self.tileset_img_keepalive = []
        self._tileset_after_id = None
        self._tileset_view_last = None
        self._tileset_scrollregion_cache = None
        # Large-map viewport state.
        # V0.5.5 : sur les maps de taille normale, les lignes visibles sont composées
        # sur TOUTE la largeur de la map. Le scroll horizontal devient alors un simple
        # déplacement Canvas (aucun redraw pendant le déplacement latéral).
        # Pour les maps vraiment géantes, on conserve le viewport partiel historique.
        self.viewport_margin_tiles = 10
        self.viewport_guard_tiles = 3
        self.fast_scroll_y_margin_tiles = 3
        self.fast_scroll_max_row_px = 12288
        self.fast_scroll_pixel_budget = 12_000_000
        # Le pré-rendu pleine largeur est excellent pour le scroll à faible zoom,
        # mais devient contre-productif à fort zoom (grosses PhotoImage + pics mémoire).
        self.fast_scroll_max_zoom = 2
        self._fast_horizontal_mode = False
        self._viewport_after_id = None
        # La molette peut envoyer une rafale d'événements. On ne reconstruit la map
        # qu'une fois, sur le dernier niveau demandé, après une courte accalmie.
        self._zoom_after_id = None
        self._zoom_target = self.zoom
        self._zoom_anchor_xy = None
        self.zoom_debounce_ms = 85
        self._viewport_last = None
        self._render_bounds = None
        self._scrollregion_cache = None
        self._pan_active = False
        self._object_render_index = {}
        self._objects_by_cell = {}
        self._row_images = {}
        self._row_items = {}
        self._cursor_img = None
        self._cursor_cell = None
        self._stats_after_id = None
        self.goto_x_var = tk.IntVar(value=0)
        self.goto_y_var = tk.IntVar(value=0)
        self._sparse_history = None

        # Sélection/copie multi-tiles. La sélection est liée au calque BG actif.
        self.map_selection = None              # (x0, y0, x1, y1), bornes inclusives
        self.map_selection_layer = None
        self.selection_anchor = None
        self.selection_dragging = False
        self._direct_drag_candidate = False    # clic simple = outil ; glissé = sélection
        self._direct_drag_start_xy = None
        self._direct_drag_threshold_px = 6
        self.tile_clipboard = None             # {layer,width,height,cells}
        self.paste_mode = False
        self._paste_preview_keepalive = []

        self.painting = False
        self.last_paint = None
        self.rect_start = None

        self.undo_stack = []
        self.redo_stack = []
        self.max_history = 30
        self._gesture_snapshot_taken = False

        self.event_enabled_var = tk.BooleanVar(value=False)
        self.event_trigger_var = tk.StringVar(value="TOUCH")
        self.event_action_var = tk.StringVar(value="SET_PALETTE")
        self.event_a_var = tk.StringVar(value="P1")
        self.event_b_var = tk.StringVar(value="")
        self.event_once_var = tk.BooleanVar(value=False)
        self.event_note_var = tk.StringVar(value="")
        self.cell_collision_var = tk.StringVar(value="NONE")

        self._style()
        self._build_ui()
        self.flip_x_var.trace_add("write", self._on_flip_var_changed)
        self.flip_y_var.trace_add("write", self._on_flip_var_changed)
        self._bind_shortcuts()
        self._sync_mode_ui()
        self._update_priority_ui()
        self.refresh_stats()
        self._update_flip_buttons()
        self.redraw_map()
        self.redraw_tileset()
        self.redraw_selected_tile_preview()

    # ------------------------- STYLE / UI -------------------------

    def _style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure(".", font=("Segoe UI", 9))
        s.configure("TFrame", background="#17191d")
        s.configure("TLabelframe", background="#17191d", foreground="#ececec")
        s.configure("TLabelframe.Label", background="#17191d", foreground="#ececec",
                    font=("Segoe UI", 9, "bold"))
        s.configure("TLabel", background="#17191d", foreground="#d9d9d9")
        s.configure("Title.TLabel", background="#17191d", foreground="#f5f5f5",
                    font=("Segoe UI", 17, "bold"))
        s.configure("Sub.TLabel", background="#17191d", foreground="#9aa3ad")
        s.configure("TCheckbutton", background="#17191d", foreground="#d9d9d9", padding=2)
        s.map("TCheckbutton", foreground=[("selected", "#ffd166"), ("active", "#ffffff")])
        s.configure("TRadiobutton", background="#17191d", foreground="#d9d9d9")
        s.map("TRadiobutton", foreground=[("selected", "#ffd166"), ("active", "#ffffff")])
        s.configure("Accent.TButton", font=("Segoe UI", 9, "bold"))

    def _build_ui(self):
        top = ttk.Frame(self, padding=(12, 8))
        top.pack(fill="x")
        ttk.Label(top, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(
            top,
            text="glisser = sélectionner • molette = zoom • Ctrl+C / Ctrl+V",
            style="Sub.TLabel"
        ).pack(side="left", padx=14)

        ttk.Button(top, text="Exporter .dmap", command=self.export_map,
                   style="Accent.TButton").pack(side="right", padx=4)
        ttk.Button(top, text="Sauver", command=self.save_project).pack(side="right", padx=4)
        ttk.Button(top, text="Ouvrir", command=self.open_project).pack(side="right", padx=4)
        ttk.Button(top, text="Nouvelle map", command=self.new_map_dialog).pack(side="right", padx=4)

        body = ttk.Panedwindow(self, orient="horizontal")
        self.main_paned = body
        body.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        left = ttk.Frame(body, padding=6)
        center = ttk.Frame(body, padding=6)
        right = ttk.Frame(body, padding=6)
        body.add(left, weight=3)
        body.add(center, weight=9)
        body.add(right, weight=3)

        self._build_left(left)
        self._build_center(center)
        self._build_right(right)

        bottom = ttk.Frame(self, padding=(12, 0, 12, 10))
        bottom.pack(fill="x")
        self.status = ttk.Label(bottom, text="Prêt.")
        self.status.pack(side="left")
        ttk.Label(bottom, text="Glisser = sélectionner • Ctrl+C copier • Ctrl+V coller • Échap annuler",
                  style="Sub.TLabel").pack(side="right")

    def _build_left(self, parent):
        proj = ttk.LabelFrame(parent, text="Map DMS-1", padding=8)
        proj.pack(fill="x")
        ttk.Label(proj, text="Nom").grid(row=0, column=0, sticky="w")
        ttk.Entry(proj, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", padx=(8,0))
        ttk.Label(proj, text="Mode").grid(row=1, column=0, sticky="w", pady=(7,0))
        mode = ttk.Combobox(proj, textvariable=self.mode_var, values=list(DMS_MODES), state="readonly")
        mode.grid(row=1, column=1, sticky="ew", padx=(8,0), pady=(7,0))
        mode.bind("<<ComboboxSelected>>", lambda e: self.on_mode_change())
        ttk.Label(proj, text="Map").grid(row=2, column=0, sticky="w", pady=(7,0))
        dim = ttk.Frame(proj)
        dim.grid(row=2, column=1, sticky="w", padx=(8,0), pady=(7,0))
        ttk.Entry(dim, textvariable=self.map_w_var, width=5).pack(side="left")
        ttk.Label(dim, text="×").pack(side="left", padx=3)
        ttk.Entry(dim, textvariable=self.map_h_var, width=5).pack(side="left")
        ttk.Label(dim, text="cells").pack(side="left", padx=4)
        ttk.Label(proj, text="Cellule").grid(row=3, column=0, sticky="w", pady=(7,0))
        ttk.Combobox(proj, textvariable=self.tile_size_var, values=[8,16,32],
                     state="readonly", width=7).grid(row=3, column=1, sticky="w", padx=(8,0), pady=(7,0))
        ttk.Button(proj, text="Appliquer dimensions", command=self.apply_dimensions).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(8,0)
        )
        proj.columnconfigure(1, weight=1)
        self.mode_info = ttk.Label(proj, text="", wraplength=320, style="Sub.TLabel")
        self.mode_info.grid(row=5, column=0, columnspan=2, sticky="w", pady=(7,0))

        ts = ttk.LabelFrame(parent, text="Tileset", padding=8)
        ts.pack(fill="x", pady=8)
        ttk.Button(ts, text="Ajouter tileset DMS .dimg", command=self.import_dimg,
                   style="Accent.TButton").pack(fill="x")
        librow = ttk.Frame(ts)
        librow.pack(fill="x", pady=(5,0))
        ttk.Button(librow, text="Recharger source", command=self.reload_tileset_source).pack(side="left", fill="x", expand=True)
        ttk.Button(librow, text="Infos palettes", command=self.show_tileset_library_info).pack(side="left", padx=(5,0))
        ttk.Button(ts, text="PNG brut - remplacer bibliothèque", command=self.import_tileset).pack(fill="x", pady=(5,0))
        opts = ttk.Frame(ts)
        opts.pack(fill="x", pady=(7,0))
        ttk.Label(opts, text="Tile").pack(side="left")
        ttk.Combobox(opts, textvariable=self.ts_tile_var, values=[8,16,32],
                     state="readonly", width=5).pack(side="left", padx=3)
        ttk.Label(opts, text="marge").pack(side="left", padx=(8,2))
        ttk.Entry(opts, textvariable=self.ts_margin_var, width=4).pack(side="left")
        ttk.Label(opts, text="esp.").pack(side="left", padx=(8,2))
        ttk.Entry(opts, textvariable=self.ts_spacing_var, width=4).pack(side="left")

        preview_box = ttk.LabelFrame(parent, text="Tile sélectionnée - Original → Résultat", padding=6)
        preview_box.pack(fill="x", pady=(0,8))
        self.tile_preview_canvas = tk.Canvas(preview_box, height=118, bg="#202329", highlightthickness=0)
        self.tile_preview_canvas.pack(fill="x")
        self.tile_preview_canvas.bind("<Configure>", lambda e: self.redraw_selected_tile_preview())
        preview_actions = ttk.Frame(preview_box)
        preview_actions.pack(fill="x", pady=(5,0))
        ttk.Button(preview_actions, text="Agrandir", command=self.open_tile_preview_window).pack(side="left")
        ttk.Button(preview_actions, text="Réinitialiser flips", command=self.reset_flips).pack(side="right")

        tile_box = ttk.LabelFrame(parent, text="Tiles - taille réglable", padding=5)
        tile_box.pack(fill="both", expand=True)
        tile_toolbar = ttk.Frame(tile_box)
        tile_toolbar.pack(fill="x", pady=(0,5))
        ttk.Label(tile_toolbar, text="Source").pack(side="left")
        self.tileset_source_cb = ttk.Combobox(tile_toolbar, textvariable=self.tileset_source_var,
                                values=["Tous les tilesets"], state="readonly", width=18)
        self.tileset_source_cb.pack(side="left", padx=(5,10))
        self.tileset_source_cb.bind("<<ComboboxSelected>>", lambda e: self.redraw_tileset(force=True))
        ttk.Label(tile_toolbar, text="Vignette").pack(side="left")
        thumb_cb = ttk.Combobox(tile_toolbar, textvariable=self.tileset_thumb_var,
                                values=["32 px","48 px","64 px","80 px","96 px"],
                                state="readonly", width=7)
        thumb_cb.pack(side="left", padx=(5,0))
        thumb_cb.bind("<<ComboboxSelected>>", lambda e: self.redraw_tileset(force=True))
        ttk.Label(tile_toolbar, text="Ctrl+molette = taille", style="Sub.TLabel").pack(side="right")
        fr = ttk.Frame(tile_box)
        fr.pack(fill="both", expand=True)
        self.tileset_canvas = tk.Canvas(fr, bg="#202329", highlightthickness=0)
        sb = ttk.Scrollbar(fr, orient="vertical", command=self._tileset_scroll_y)
        self._tileset_sb = sb
        self.tileset_canvas.configure(yscrollcommand=self._tileset_yscroll_changed)
        self.tileset_canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tileset_canvas.bind("<Button-1>", self.tileset_click)
        self.tileset_canvas.bind("<Double-Button-1>", self.tileset_double_click)
        self.tileset_canvas.bind("<Configure>", lambda e: self.request_tileset_redraw())
        self.tileset_canvas.bind("<MouseWheel>", self._tileset_mousewheel)
        self.tileset_canvas.bind("<Control-MouseWheel>", self._tileset_zoom_wheel)
        self.tile_info = ttk.Label(tile_box, text="Aucune tile sélectionnée", style="Sub.TLabel", wraplength=360)
        self.tile_info.pack(fill="x", pady=(5,0))

    def _build_center(self, parent):
        tools = ttk.Frame(parent)
        tools.pack(fill="x", pady=(0,6))
        for val, lab in [
            ("BRUSH","Pinceau"), ("ERASE","Gomme"), ("PICK","Pipette"),
            ("FILL","Remplir"), ("RECT","Rectangle")
        ]:
            ttk.Radiobutton(tools, text=lab, variable=self.current_tool, value=val).pack(side="left", padx=2)

        ttk.Label(tools, text="Glisser sur BG = sélectionner • Ctrl+C / Ctrl+V", style="Sub.TLabel").pack(side="left", padx=(10,4))
        ttk.Button(tools, text="↶", width=3, command=self.undo).pack(side="left", padx=(10,2))
        ttk.Button(tools, text="↷", width=3, command=self.redo).pack(side="left", padx=2)

        ttk.Label(tools, text="Zoom").pack(side="left", padx=(12,3))
        z = ttk.Combobox(tools, textvariable=self.zoom_var,
                         values=["1×","2×","3×","4×","5×","6×","7×","8×"], state="readonly", width=5)
        z.pack(side="left")
        z.bind("<<ComboboxSelected>>", lambda e: self.set_zoom())

        ttk.Checkbutton(tools, text="Grille", variable=self.grid_var,
                        command=self.redraw_map).pack(side="left", padx=6)
        ttk.Checkbutton(tools, text="Infos tech.", variable=self.tech_overlay_var,
                        command=self.redraw_map).pack(side="left", padx=6)
        ttk.Checkbutton(tools, text="Cadre écran", variable=self.camera_var,
                        command=self.redraw_map).pack(side="left", padx=6)

        nav = ttk.Frame(parent)
        nav.pack(fill="x", pady=(0,6))
        ttk.Button(nav, text="100 %", command=self.view_100).pack(side="left", padx=2)
        ttk.Button(nav, text="Voir toute la map", command=self.show_overview).pack(side="left", padx=2)
        ttk.Label(nav, text="Aller à tile").pack(side="left", padx=(12,3))
        ttk.Entry(nav, textvariable=self.goto_x_var, width=7).pack(side="left")
        ttk.Label(nav, text="×").pack(side="left", padx=3)
        ttk.Entry(nav, textvariable=self.goto_y_var, width=6).pack(side="left")
        ttk.Button(nav, text="Aller", command=self.goto_tile).pack(side="left", padx=4)
        ttk.Button(nav, text="Solo BG A", command=lambda: self.solo_layer("BG A")).pack(side="left", padx=(12,2))
        ttk.Button(nav, text="Solo BG B", command=lambda: self.solo_layer("BG B")).pack(side="left", padx=2)
        ttk.Button(nav, text="Tout", command=self.show_all_layers).pack(side="left", padx=2)
        ttk.Label(nav, text="Molette: ZOOM • Ctrl+molette: vertical • Shift+molette: horizontal • milieu: déplacer",
                  style="Sub.TLabel").pack(side="right")

        view = ttk.LabelFrame(parent, text="Map - viewport grandes maps", padding=5)
        view.pack(fill="both", expand=True)

        frm = ttk.Frame(view)
        frm.pack(fill="both", expand=True)
        self.map_canvas = tk.Canvas(frm, bg="#1e2126", highlightthickness=0)
        self.viewport_badge = tk.Label(self.map_canvas, text="", bg="#17191d", fg="#f0f0f0",
                                       bd=1, relief="solid", padx=6, pady=2, font=("Segoe UI", 8))
        self.viewport_badge.place(x=8, y=8)
        hsb = ttk.Scrollbar(frm, orient="horizontal", command=self._scroll_x)
        vsb = ttk.Scrollbar(frm, orient="vertical", command=self._scroll_y)
        self._map_hsb = hsb
        self._map_vsb = vsb
        self.map_canvas.configure(xscrollcommand=self._xscroll_changed, yscrollcommand=self._yscroll_changed)
        self.map_canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(0, weight=1)

        self.map_canvas.bind("<Button-1>", self.map_mouse_down)
        self.map_canvas.bind("<B1-Motion>", self.map_mouse_drag)
        self.map_canvas.bind("<ButtonRelease-1>", self.map_mouse_up)
        self.map_canvas.bind("<Motion>", self.map_hover)
        self.map_canvas.bind("<Leave>", self.map_leave)
        self.map_canvas.bind("<Button-3>", self.map_right_click)
        self.map_canvas.bind("<Configure>", lambda e: self.request_viewport_redraw())
        self.map_canvas.bind("<MouseWheel>", self._mousewheel)
        self.map_canvas.bind("<Control-MouseWheel>", self._vertical_mousewheel)
        self.map_canvas.bind("<Shift-MouseWheel>", self._shift_mousewheel)
        self.map_canvas.bind("<Button-2>", self._pan_start)
        self.map_canvas.bind("<B2-Motion>", self._pan_drag)
        self.map_canvas.bind("<ButtonRelease-2>", self._pan_end)

        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(6,0))
        ttk.Label(bar, text="Calque").pack(side="left")
        self.layer_cb = ttk.Combobox(bar, textvariable=self.active_layer,
                                     values=LAYER_NAMES, state="readonly", width=11)
        self.layer_cb.pack(side="left", padx=4)
        self.layer_cb.bind("<<ComboboxSelected>>", lambda e: self.on_layer_change())

        ttk.Label(bar, text="Palette").pack(side="left", padx=(10,3))
        self.palette_cb = ttk.Combobox(bar, textvariable=self.palette_var,
                                       values=[0,1,2,3], state="readonly", width=4)
        self.palette_cb.pack(side="left")
        self.palette_cb.bind("<<ComboboxSelected>>", lambda e: self.on_palette_change())
        self.active_palette_canvas = tk.Canvas(bar, width=132, height=18, bg="#202329", highlightthickness=1,
                                               highlightbackground="#4d545e")
        self.active_palette_canvas.pack(side="left", padx=(5,4))

        self.flip_x_button = tk.Button(bar, textvariable=self.flip_x_text_var, command=self.toggle_flip_x,
                                       bd=1, relief="raised", padx=7, pady=2, cursor="hand2")
        self.flip_x_button.pack(side="left", padx=(8,3))
        self.flip_y_button = tk.Button(bar, textvariable=self.flip_y_text_var, command=self.toggle_flip_y,
                                       bd=1, relief="raised", padx=7, pady=2, cursor="hand2")
        self.flip_y_button.pack(side="left", padx=3)

        ttk.Label(bar, text="Profondeur").pack(side="left", padx=(12,3))
        self.depth_cb = ttk.Combobox(bar, values=["Derrière sprites","Devant sprites"],
                                     state="readonly", width=15)
        self.depth_cb.set("Derrière sprites")
        self.depth_cb.pack(side="left")
        self.depth_cb.bind("<<ComboboxSelected>>", lambda e: self.on_depth_change())

        self.code_badge = ttk.Label(bar, text="code 0", style="Sub.TLabel")
        self.code_badge.pack(side="left", padx=6)
        self.cursor_info = ttk.Label(bar, text="x- y-", style="Sub.TLabel")
        self.cursor_info.pack(side="right")

    def _build_right(self, parent):
        nb = ttk.Notebook(parent)
        nb.pack(fill="both", expand=True)
        layers = ttk.Frame(nb, padding=8)
        paltab = ttk.Frame(nb, padding=8)
        celltab = ttk.Frame(nb, padding=8)
        eventtab = ttk.Frame(nb, padding=8)
        objtab = ttk.Frame(nb, padding=8)
        exporttab = ttk.Frame(nb, padding=8)
        nb.add(layers, text="Calques")
        nb.add(paltab, text="Palettes")
        nb.add(celltab, text="Cellule")
        nb.add(eventtab, text="Événement")
        nb.add(objtab, text="Objets")
        nb.add(exporttab, text="Export")

        ttk.Label(layers, text="Visibilité", font=("Segoe UI",10,"bold")).pack(anchor="w")
        self.bg_b_visibility_check = None
        for text, var in [
            ("BG B",self.show_bg_b_var), ("BG A",self.show_bg_a_var),
            ("Objets / sprites",self.show_objects_var),
            ("Collisions",self.show_collision_var), ("Événements",self.show_events_var)
        ]:
            cb = ttk.Checkbutton(layers, text=text, variable=var, command=self.on_visibility_change)
            cb.pack(anchor="w", pady=3)
            if text == "BG B":
                self.bg_b_visibility_check = cb
        ttk.Checkbutton(layers, text="Repères éditeur objets / événements", variable=self.show_editor_guides_var,
                        command=lambda: self.redraw_map(force=True)).pack(anchor="w", pady=(8,3))
        layer_quick = ttk.Frame(layers)
        layer_quick.pack(fill="x", pady=(6,0))
        ttk.Button(layer_quick, text="Solo BG A", command=lambda: self.solo_layer("BG A")).pack(side="left", expand=True, fill="x", padx=(0,2))
        ttk.Button(layer_quick, text="Solo BG B", command=lambda: self.solo_layer("BG B")).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(layer_quick, text="Tout", command=self.show_all_layers).pack(side="left", expand=True, fill="x", padx=(2,0))

        ttk.Separator(layers).pack(fill="x", pady=10)
        ttk.Label(
            layers,
            text="Ordre visuel maintenant simulé :\n\n"
                 "BG B code 2\nBG A code 0\nSPRITES / OBJECTS\n"
                 "BG B code 3\nBG A code 1\n\n"
                 "Les codes sont exportés tels quels pour le GDK.",
            wraplength=360, style="Sub.TLabel"
        ).pack(anchor="w")

        ttk.Label(layers, text="BG B peut être totalement masqué par BG A dans le rendu normal. Utilise « Solo BG B » pour l'inspecter.",
                  wraplength=360, style="Sub.TLabel").pack(anchor="w", pady=(0,4))
        ttk.Separator(layers).pack(fill="x", pady=10)
        self.stats_var = tk.StringVar(value="")
        ttk.Label(layers, textvariable=self.stats_var, wraplength=360).pack(anchor="w")

        ttk.Label(paltab, text="Banques de palettes DMS-1", font=("Segoe UI",10,"bold")).pack(anchor="w")
        self.palette_source_var = tk.StringVar(value="Aucune ressource palette chargée.")
        ttk.Label(paltab, textvariable=self.palette_source_var, wraplength=360, style="Sub.TLabel").pack(anchor="w", pady=(4,8))
        self.palette_panel_canvas = tk.Canvas(paltab, bg="#202329", highlightthickness=0, height=260)
        self.palette_panel_canvas.pack(fill="both", expand=True)
        self.palette_panel_canvas.bind("<Button-1>", self.palette_panel_click)
        ttk.Label(
            paltab,
            text="Avec un .dimg, les 16 couleurs RGB333 de chaque banque sont exactes. "
                 "Avec un PNG brut, P0/P1… reste seulement un identifiant exporté : les couleurs de la banque ne sont pas connues de cet outil.",
            wraplength=360, style="Sub.TLabel"
        ).pack(anchor="w", pady=(8,0))

        self.cell_pos_var = tk.StringVar(value="Aucune cellule")
        ttk.Label(celltab, textvariable=self.cell_pos_var,
                  font=("Segoe UI",11,"bold")).pack(anchor="w")
        self.cell_detail = tk.Text(celltab, height=16, bg="#202329", fg="#e8e8e8",
                                   relief="flat", wrap="word")
        self.cell_detail.pack(fill="both", expand=True, pady=(8,0))
        self.cell_detail.configure(state="disabled")
        ttk.Label(celltab, text="Collision").pack(anchor="w", pady=(8,0))
        self.cell_collision_cb = ttk.Combobox(
            celltab, textvariable=self.cell_collision_var,
            values=COLLISION_TYPES, state="readonly"
        )
        self.cell_collision_cb.pack(fill="x", pady=4)
        ttk.Button(celltab, text="Appliquer collision",
                   command=self.apply_collision_to_selected).pack(fill="x", pady=4)

        self._form_combo(eventtab, "Trigger", self.event_trigger_var, TRIGGER_TYPES)
        self._form_combo(eventtab, "Action", self.event_action_var, ACTION_TYPES)
        ttk.Checkbutton(eventtab, text="Événement actif", variable=self.event_enabled_var,
                        command=self._event_form_changed).pack(anchor="w", pady=(8,0))
        ttk.Label(eventtab, text="Paramètre A").pack(anchor="w", pady=(8,0))
        ttk.Entry(eventtab, textvariable=self.event_a_var).pack(fill="x")
        ttk.Label(eventtab, text="Paramètre B").pack(anchor="w", pady=(8,0))
        ttk.Entry(eventtab, textvariable=self.event_b_var).pack(fill="x")
        ttk.Checkbutton(eventtab, text="Une seule fois", variable=self.event_once_var,
                        command=self._event_form_changed).pack(anchor="w", pady=(8,0))
        ttk.Label(eventtab, text="Note").pack(anchor="w", pady=(8,0))
        ttk.Entry(eventtab, textvariable=self.event_note_var).pack(fill="x")
        ttk.Button(eventtab, text="Appliquer à la cellule",
                   command=self.apply_event_to_selected,
                   style="Accent.TButton").pack(fill="x", pady=(12,4))
        ttk.Button(eventtab, text="Effacer événement",
                   command=self.clear_event_selected).pack(fill="x", pady=4)
        ttk.Label(
            eventtab,
            text="Exemple : TOUCH → SET_PALETTE → P2.\n"
                 "Le Map Builder stocke l'événement ; le runtime GDK l'exécutera.",
            wraplength=360, style="Sub.TLabel"
        ).pack(anchor="w", pady=(12,0))

        topobj = ttk.Frame(objtab)
        topobj.pack(fill="x")
        ttk.Button(topobj, text="Placer depuis tile",
                   command=self.place_object_mode,
                   style="Accent.TButton").pack(side="left")
        ttk.Button(topobj, text="Supprimer",
                   command=self.delete_selected_object).pack(side="right")
        self.object_tree = ttk.Treeview(
            objtab, columns=("pos","tile","pal"), show="tree headings", selectmode="browse"
        )
        self.object_tree.heading("#0", text="Objet")
        self.object_tree.heading("pos", text="Pos")
        self.object_tree.heading("tile", text="Tile")
        self.object_tree.heading("pal", text="Pal")
        self.object_tree.column("#0", width=120)
        self.object_tree.column("pos", width=70)
        self.object_tree.column("tile", width=45)
        self.object_tree.column("pal", width=40)
        self.object_tree.pack(fill="both", expand=True, pady=8)
        self.object_tree.bind("<<TreeviewSelect>>", lambda e: self.on_object_select())
        ttk.Button(objtab, text="Éditer objet",
                   command=self.edit_selected_object).pack(fill="x")

        ttk.Label(
            exporttab,
            text="DMAP V2 : priorité 0/1/2/3 explicite + layers + collisions + events + objects.",
            wraplength=360
        ).pack(anchor="w")
        ttk.Button(exporttab, text="Exporter .dmap", command=self.export_map,
                   style="Accent.TButton").pack(fill="x", pady=(12,5))
        ttk.Button(exporttab, text="Exporter bundle DMS-GDK",
                   command=self.export_bundle).pack(fill="x", pady=5)
        ttk.Button(exporttab, text="Sauver .dmapproj",
                   command=self.save_project).pack(fill="x", pady=(18,5))

    def _form_combo(self, parent, label, var, values):
        ttk.Label(parent, text=label).pack(anchor="w", pady=(8,0))
        ttk.Combobox(parent, textvariable=var, values=values,
                     state="readonly").pack(fill="x")

    def _bind_shortcuts(self):
        self.bind_all("<Control-z>", self._shortcut_undo)
        self.bind_all("<Control-y>", self._shortcut_redo)
        self.bind_all("<Key-b>", lambda e: self._shortcut_tool(e, "BRUSH"))
        self.bind_all("<Key-e>", lambda e: self._shortcut_tool(e, "ERASE"))
        self.bind_all("<Key-i>", lambda e: self._shortcut_tool(e, "PICK"))
        self.bind_all("<Key-f>", lambda e: self._shortcut_tool(e, "FILL"))
        self.bind_all("<Key-r>", lambda e: self._shortcut_tool(e, "RECT"))
        self.bind_all("<Control-c>", self.copy_map_selection)
        self.bind_all("<Control-C>", self.copy_map_selection)
        self.bind_all("<Control-v>", self.start_paste_mode)
        self.bind_all("<Control-V>", self.start_paste_mode)
        self.bind_all("<Escape>", self.cancel_selection_or_paste)
        self.bind_all("<Control-Key-0>", lambda e: self.view_100())
        self.bind_all("<Control-Key-plus>", lambda e: self._set_zoom(self.zoom + 1))
        self.bind_all("<Control-Key-minus>", lambda e: self._set_zoom(self.zoom - 1))
        self.bind_all("<Key-g>", self._shortcut_goto)

    # ------------------------- SELECTION / COPY / PASTE -------------------------

    def _shortcut_undo(self, event=None):
        if self._shortcut_is_text_edit(event):
            return None
        self.undo()
        return "break"

    def _shortcut_redo(self, event=None):
        if self._shortcut_is_text_edit(event):
            return None
        self.redo()
        return "break"

    def _shortcut_tool(self, event, tool):
        if self._shortcut_is_text_edit(event):
            return None
        self.current_tool.set(tool)
        return "break"

    def _shortcut_goto(self, event=None):
        if self._shortcut_is_text_edit(event):
            return None
        self.goto_tile()
        return "break"

    def _shortcut_is_text_edit(self, event=None):
        """Ne détourne jamais Ctrl+C/V d'un champ texte, d'une note ou d'une combobox."""
        w = getattr(event, "widget", None) if event is not None else self.focus_get()
        if w is None:
            w = self.focus_get()
        if w is None:
            return False
        try:
            return w.winfo_class() in {"Entry", "TEntry", "Text", "TCombobox", "Spinbox", "TSpinbox"}
        except Exception:
            return False

    @staticmethod
    def _normalized_selection(a, b):
        x0, x1 = sorted((int(a[0]), int(b[0])))
        y0, y1 = sorted((int(a[1]), int(b[1])))
        return (x0, y0, x1, y1)

    def _selection_size(self):
        if not self.map_selection:
            return (0, 0)
        x0, y0, x1, y1 = self.map_selection
        return (x1 - x0 + 1, y1 - y0 + 1)

    def _draw_selection_overlay(self):
        if not hasattr(self, "map_canvas"):
            return
        self.map_canvas.delete("selection_overlay")
        if not self.map_selection:
            return
        x0, y0, x1, y1 = self.map_selection
        ts = self.state.tile_size * self.zoom
        left, top = x0 * ts, y0 * ts
        right, bottom = (x1 + 1) * ts, (y1 + 1) * ts
        w, h = self._selection_size()
        tags = ("selection_overlay", "editor_overlay")
        self.map_canvas.create_rectangle(
            left + 1, top + 1, right - 1, bottom - 1,
            outline="#00e5ff", width=3, dash=(7, 3), tags=tags
        )
        label = f"SÉLECTION {w}×{h} • {w*h} tiles"
        # Badge posé juste dans la sélection : reste lisible même en bord de map.
        bx0, by0 = left + 4, top + 4
        self.map_canvas.create_rectangle(
            bx0, by0, bx0 + max(102, len(label) * 6), by0 + 17,
            fill="#07333a", outline="#00e5ff", width=1, tags=tags
        )
        self.map_canvas.create_text(
            bx0 + 5, by0 + 8, text=label, anchor="w", fill="#c5fbff",
            font=("Segoe UI", 7, "bold"), tags=tags
        )
        self.map_canvas.tag_raise("selection_overlay")

    def clear_map_selection(self, silent=False):
        self.map_selection = None
        self.map_selection_layer = None
        self.selection_anchor = None
        self.selection_dragging = False
        self._direct_drag_candidate = False
        self._direct_drag_start_xy = None
        if hasattr(self, "map_canvas"):
            self.map_canvas.delete("selection_overlay")
        if not silent and hasattr(self, "status"):
            self.status.configure(text="Sélection effacée.")

    def copy_map_selection(self, event=None):
        if event is not None and self._shortcut_is_text_edit(event):
            return None
        if not self.map_selection:
            self.status.configure(text="Aucune zone sélectionnée. Glisse simplement la souris sur BG A ou BG B, puis Ctrl+C.")
            return "break" if event is not None else None
        layer = self.map_selection_layer or self.active_layer.get()
        if layer not in ("BG A", "BG B"):
            self.status.configure(text="La copie multi-tiles concerne BG A / BG B.")
            return "break" if event is not None else None
        x0, y0, x1, y1 = self.map_selection
        grid = self.state.bg_a if layer == "BG A" else self.state.bg_b
        cells = [[deepcopy(grid[y][x]) for x in range(x0, x1 + 1)] for y in range(y0, y1 + 1)]
        self.tile_clipboard = {
            "layer": layer,
            "width": x1 - x0 + 1,
            "height": y1 - y0 + 1,
            "cells": cells,
        }
        self.paste_mode = False
        self.refresh_brush_preview()
        self.status.configure(
            text=f"Copié : {self.tile_clipboard['width']}×{self.tile_clipboard['height']} tiles de {layer}. Ctrl+V pour placer une copie."
        )
        return "break" if event is not None else None

    def start_paste_mode(self, event=None):
        if event is not None and self._shortcut_is_text_edit(event):
            return None
        if not self.tile_clipboard:
            self.status.configure(text="Presse d'abord Ctrl+C sur une sélection de tiles.")
            return "break" if event is not None else None
        layer = self.tile_clipboard["layer"]
        if layer == "BG B" and not DMS_MODES[self.mode_var.get()]["has_bg_b"]:
            self.status.configure(text="Impossible de coller : le mode vidéo actuel ne possède pas BG B.")
            return "break" if event is not None else None
        self.active_layer.set(layer)
        self._update_priority_ui()
        self.paste_mode = True
        self.selection_dragging = False
        self.refresh_brush_preview()
        w, h = self.tile_clipboard["width"], self.tile_clipboard["height"]
        self.status.configure(text=f"Ctrl+V : bloc {w}×{h} prêt - déplace la souris puis clique où le poser. Échap = annuler.")
        return "break" if event is not None else None

    def cancel_selection_or_paste(self, event=None):
        if self.paste_mode:
            self.paste_mode = False
            self.refresh_brush_preview()
            self.status.configure(text="Collage annulé. La copie reste en mémoire : Ctrl+V pour la réutiliser.")
            return "break"
        if self.map_selection:
            self.clear_map_selection(silent=True)
            self.refresh_brush_preview()
            self.status.configure(text="Sélection annulée.")
            return "break"
        return None

    def paste_clipboard_at(self, x, y):
        if not self.tile_clipboard:
            return False
        layer = self.tile_clipboard["layer"]
        w, h = self.tile_clipboard["width"], self.tile_clipboard["height"]
        if x < 0 or y < 0 or x + w > self.state.width or y + h > self.state.height:
            self.status.configure(text=f"Zone {w}×{h} trop grande à cet endroit : choisis une destination entièrement dans la map.")
            return False
        grid = self.state.bg_a if layer == "BG A" else self.state.bg_b
        self._begin_sparse_history()
        for dy, row in enumerate(self.tile_clipboard["cells"]):
            for dx, cell in enumerate(row):
                tx, ty = x + dx, y + dy
                self._record_cell_before(layer, tx, ty)
                grid[ty][tx] = deepcopy(cell)
        self._commit_sparse_history()
        self.map_selection = (x, y, x + w - 1, y + h - 1)
        self.map_selection_layer = layer
        self.paste_mode = False
        self.redraw_map(force=True)
        self.request_stats_refresh()
        self.status.configure(text=f"Collé : {w}×{h} tiles sur {layer}. Ctrl+V permet de reproduire encore le même bloc.")
        return True

    # ------------------------- HISTORY -------------------------

    def snapshot_state(self):
        return deepcopy(self.state)

    def _trim_history(self, stack):
        while len(stack) > self.max_history:
            stack.pop(0)

    def push_undo(self):
        """Snapshot complet réservé aux opérations structurelles rares."""
        self.undo_stack.append({"kind":"snapshot", "state":self.snapshot_state()})
        self._trim_history(self.undo_stack)
        self.redo_stack.clear()

    def push_objects_undo(self):
        """Historique objets indépendant de la taille de la tilemap."""
        self.undo_stack.append({
            "kind":"objects",
            "objects":deepcopy(self.state.objects),
            "next_object_id":int(self.state.next_object_id),
        })
        self._trim_history(self.undo_stack)
        self.redo_stack.clear()

    def _begin_sparse_history(self):
        self._sparse_history = {}

    def _cell_value(self, layer, x, y):
        if layer == "BG A": return deepcopy(self.state.bg_a[y][x])
        if layer == "BG B": return deepcopy(self.state.bg_b[y][x])
        if layer == "COLLISION": return self.state.collisions[y][x]
        if layer == "EVENTS": return deepcopy(self.state.events[y][x])
        return None

    def _set_cell_value(self, layer, x, y, value):
        if layer == "BG A": self.state.bg_a[y][x] = deepcopy(value)
        elif layer == "BG B": self.state.bg_b[y][x] = deepcopy(value)
        elif layer == "COLLISION": self.state.collisions[y][x] = value
        elif layer == "EVENTS": self.state.events[y][x] = deepcopy(value)

    def _record_cell_before(self, layer, x, y):
        if self._sparse_history is None:
            return
        key = (layer, int(x), int(y))
        if key not in self._sparse_history:
            self._sparse_history[key] = self._cell_value(layer, x, y)

    def _commit_sparse_history(self):
        if self._sparse_history is None:
            return
        changes = []
        for (layer, x, y), before in self._sparse_history.items():
            after = self._cell_value(layer, x, y)
            if before != after:
                changes.append((layer, x, y, before, after))
        self._sparse_history = None
        if not changes:
            return
        self.undo_stack.append({"kind":"cells", "changes":changes})
        self._trim_history(self.undo_stack)
        self.redo_stack.clear()

    def _apply_history_entry(self, entry, direction):
        kind = entry.get("kind") if isinstance(entry, dict) else "snapshot"
        if kind == "snapshot":
            current = {"kind":"snapshot", "state":self.snapshot_state()}
            self.state = deepcopy(entry["state"])
            return current
        if kind == "cells":
            use_before = direction == "undo"
            for layer, x, y, before, after in entry["changes"]:
                self._set_cell_value(layer, x, y, before if use_before else after)
            return entry
        if kind == "objects":
            current = {
                "kind":"objects",
                "objects":deepcopy(self.state.objects),
                "next_object_id":int(self.state.next_object_id),
            }
            self.state.objects = deepcopy(entry["objects"])
            self.state.next_object_id = int(entry["next_object_id"])
            return current
        return None

    def undo(self):
        if not self.undo_stack:
            self.status.configure(text="Rien à annuler.")
            return
        entry = self.undo_stack.pop()
        inverse = self._apply_history_entry(entry, "undo")
        if inverse is not None:
            self.redo_stack.append(inverse)
            self._trim_history(self.redo_stack)
        self._sync_vars_from_state()
        self.refresh_objects()
        self.refresh_stats()
        self.redraw_map(force=True)
        self.status.configure(text="Undo.")

    def redo(self):
        if not self.redo_stack:
            self.status.configure(text="Rien à refaire.")
            return
        entry = self.redo_stack.pop()
        kind = entry.get("kind") if isinstance(entry, dict) else "snapshot"
        if kind in ("snapshot", "objects"):
            inverse = self._apply_history_entry(entry, "redo")
        else:
            inverse = entry
            self._apply_history_entry(entry, "redo")
        self.undo_stack.append(inverse)
        self._trim_history(self.undo_stack)
        self._sync_vars_from_state()
        self.refresh_objects()
        self.refresh_stats()
        self.redraw_map(force=True)
        self.status.configure(text="Redo.")

    # ------------------------- MAP PROJECT -------------------------

    def new_map_dialog(self):
        d = tk.Toplevel(self)
        d.title("Nouvelle map")
        d.transient(self)
        d.grab_set()
        frm = ttk.Frame(d, padding=16)
        frm.pack()

        name = tk.StringVar(value="DMS_MAP")
        mode = tk.StringVar(value="Mode 0 - STANDARD")
        tile = tk.IntVar(value=8)
        w = tk.IntVar(value=40)
        h = tk.IntVar(value=28)

        for r,(lab,var) in enumerate([("Nom",name),("Largeur",w),("Hauteur",h)]):
            ttk.Label(frm,text=lab).grid(row=r,column=0,sticky="w",pady=4)
            ttk.Entry(frm,textvariable=var).grid(row=r,column=1,padx=8,pady=4)
        ttk.Label(frm,text="Mode").grid(row=3,column=0,sticky="w",pady=4)
        ttk.Combobox(frm,textvariable=mode,values=list(DMS_MODES),state="readonly").grid(
            row=3,column=1,padx=8,pady=4
        )
        ttk.Label(frm,text="Cellule").grid(row=4,column=0,sticky="w",pady=4)
        ttk.Combobox(frm,textvariable=tile,values=[8,16,32],state="readonly").grid(
            row=4,column=1,padx=8,pady=4
        )

        def create():
            try:
                self.state = MapState(
                    name=name.get().strip() or "DMS_MAP",
                    width=max(1,int(w.get())), height=max(1,int(h.get())),
                    tile_size=int(tile.get()), mode=mode.get()
                )
                self.state.init_grids()
                self.undo_stack.clear()
                self.redo_stack.clear()
                self.project_path = None
                self.clear_map_selection(silent=True)
                self.tile_clipboard = None
                self.paste_mode = False
                self._sync_vars_from_state()
                self._sync_mode_ui()
                self.refresh_objects()
                self.refresh_stats()
                self.redraw_map()
                d.destroy()
            except Exception as e:
                messagebox.showerror("Nouvelle map", str(e), parent=d)

        ttk.Button(frm,text="Créer",command=create,
                   style="Accent.TButton").grid(row=5,column=0,columnspan=2,sticky="ew",pady=(10,0))

    def _sync_vars_from_state(self):
        self.name_var.set(self.state.name)
        self.mode_var.set(self.state.mode)
        self.map_w_var.set(self.state.width)
        self.map_h_var.set(self.state.height)
        self.tile_size_var.set(self.state.tile_size)
        self._sync_mode_ui()
        self._update_priority_ui()

    def apply_dimensions(self):
        try:
            nw = max(1, int(self.map_w_var.get()))
            nh = max(1, int(self.map_h_var.get()))
            nts = int(self.tile_size_var.get())
        except Exception:
            messagebox.showerror("Map", "Dimensions invalides.")
            return

        if nw * nh > 262144:
            if not messagebox.askyesno(
                "Grande map",
                f"{nw}×{nh} = {nw*nh} cellules.\n"
                "C'est une très grande map pour l'éditeur. Continuer ?"
            ):
                return

        self.push_undo()
        old = self.snapshot_state()
        self.state.width, self.state.height, self.state.tile_size = nw, nh, nts
        self.state.bg_a = [[Cell(priority_code=0) for _ in range(nw)] for __ in range(nh)]
        self.state.bg_b = [[Cell(priority_code=2) for _ in range(nw)] for __ in range(nh)]
        self.state.collisions = [["NONE" for _ in range(nw)] for __ in range(nh)]
        self.state.events = [[EventDef() for _ in range(nw)] for __ in range(nh)]

        for y in range(min(old.height, nh)):
            for x in range(min(old.width, nw)):
                self.state.bg_a[y][x] = old.bg_a[y][x]
                self.state.bg_b[y][x] = old.bg_b[y][x]
                self.state.collisions[y][x] = old.collisions[y][x]
                self.state.events[y][x] = old.events[y][x]

        self.state.objects = [o for o in old.objects if 0 <= o.x < nw and 0 <= o.y < nh]
        self.clear_map_selection(silent=True)
        self.paste_mode = False
        self.refresh_objects()
        self.refresh_stats()
        self.redraw_map()

    def _refresh_tileset_source_ui(self, select_rec=None):
        vals=self.tileset.source_filter_values()
        if hasattr(self,"tileset_source_cb"):
            self.tileset_source_cb["values"]=vals
        if select_rec is not None:
            self.tileset_source_var.set(self.tileset._source_label(select_rec))
        elif self.tileset_source_var.get() not in vals:
            self.tileset_source_var.set("Tous les tilesets")

    def _palette_limit(self):
        return int(DMS_MODES[self.mode_var.get()]["palettes"])

    def import_tileset(self):
        path=filedialog.askopenfilename(title="PNG brut - remplacer la bibliothèque",filetypes=[("PNG","*.png")])
        if not path: return
        if self.tileset.sources:
            if not messagebox.askyesno(
                "Remplacer la bibliothèque",
                "Le PNG brut est conservé comme workflow mono-source.\n\n"
                "Cette action retire la bibliothèque de tilesets de l'éditeur (la map n'est pas effacée).\n"
                "Pour un projet multi-tilesets, convertis plutôt le PNG en .dimg puis utilise Ajouter tileset.\n\nContinuer ?"
            ):
                return
        try:
            n=self.tileset.load(path,int(self.ts_tile_var.get()),int(self.ts_margin_var.get()),int(self.ts_spacing_var.get()))
            self._refresh_tileset_source_ui()
            self.selected_tile_id=0 if n else -1
            self.tile_info.configure(text=f"{Path(path).name} • {n} tiles • PNG brut")
            self.redraw_palette_ui(); self.redraw_tileset(force=True); self.redraw_selected_tile_preview(); self.redraw_map(force=True)
            self.status.configure(text=f"PNG chargé : {n} tiles. Pour ajouter d'autres sources, convertis-les en DIMG.")
        except Exception as e:
            messagebox.showerror("Tileset",str(e))

    def import_dimg(self):
        paths=filedialog.askopenfilenames(title="Ajouter tileset(s) DMS",filetypes=[("DMS Image","*.dimg"),("Tous les fichiers","*.*")])
        if not paths: return
        added=[]; all_notes=[]
        try:
            # Un ancien PNG brut ne peut pas être fusionné sans quantification implicite.
            if self.tileset.sources and any(r["kind"]!="DIMG" for r in self.tileset.sources):
                raise ValueError("Le projet utilise encore un PNG brut. Convertis-le d'abord en .dimg avant d'ajouter une seconde source.")
            for path in paths:
                # Prévisualisation du plan palette avant modification réelle.
                probe=PNGTileset(self); probe.load_dimg(path)
                plan,notes=self.tileset._plan_palette_map(probe,self._palette_limit())
                if notes:
                    msg=(f"{Path(path).name} utilise des numéros de palettes déjà occupés avec d'autres couleurs.\n\n"
                         +"\n".join("• "+x for x in notes)+
                         "\n\nLe Map Builder peut remapper cette source sans modifier les tilesets déjà chargés. Continuer ?")
                    if not messagebox.askyesno("Compatibilité palettes",msg):
                        continue
                rec,notes=self.tileset.add_dimg(path,self._palette_limit(),plan)
                added.append(rec); all_notes.extend(notes)
            if not added: return
            self.ts_tile_var.set(self.tileset.tile_size)
            self._refresh_tileset_source_ui(select_rec=added[-1])
            gids=added[-1]["tile_ids"]
            self.selected_tile_id=gids[0] if gids else -1
            usage=sorted(self.tileset.tile_palette_usage.get(self.selected_tile_id,[])) if self.selected_tile_id>=0 else []
            if usage: self.palette_var.set(usage[0])
            self.redraw_palette_ui(); self.redraw_tileset(force=True); self.redraw_selected_tile_preview(); self.redraw_map(force=True)
            banks=", ".join(f"P{x}" for x in self.tileset.palette_ids) or "aucune"
            self.tile_info.configure(text=f"Bibliothèque : {len(self.tileset.sources)} source(s) • {self.tileset.next_tile_id} IDs • {banks}")
            note=(" • "+" ; ".join(all_notes)) if all_notes else ""
            self.status.configure(text=f"{len(added)} tileset(s) ajouté(s) • palettes projet {banks}{note}")
        except Exception as e:
            messagebox.showerror("Ajout tileset",str(e))

    def _source_cells_count(self, rec):
        ids=set(rec.get("tile_ids",[])); n=0
        for grid in (self.state.bg_a,self.state.bg_b):
            for row in grid:
                n += sum(1 for c in row if c.tile_id in ids)
        n += sum(1 for o in self.state.objects if o.tile_id in ids)
        return n

    def _remap_source_cells_palettes(self, rec, old_map, new_map):
        # Quand une source rechargée doit déménager de banque, seules ses propres
        # tiles déjà posées sont remappées. Les autres sources restent intactes.
        ids=set(rec.get("tile_ids",[]))
        reverse={int(old):int(new_map.get(int(spid),old)) for spid,old in old_map.items() if int(spid) in new_map}
        changed=0
        for grid in (self.state.bg_a,self.state.bg_b):
            for row in grid:
                for c in row:
                    if c.tile_id in ids and c.palette in reverse and reverse[c.palette] != c.palette:
                        c.palette=reverse[c.palette]; changed+=1
        for o in self.state.objects:
            if o.tile_id in ids and o.palette in reverse and reverse[o.palette] != o.palette:
                o.palette=reverse[o.palette]; changed+=1
        return changed

    def reload_tileset_source(self):
        rec=self.tileset.source_from_filter(self.tileset_source_var.get())
        if rec is None:
            if len(self.tileset.sources)==1: rec=self.tileset.sources[0]
            else:
                messagebox.showinfo("Recharger source","Choisis une source précise dans la liste Source.")
                return
        try:
            # Charge à blanc pour calculer le futur contrat palette avant de toucher la bibliothèque.
            probe=PNGTileset(self)
            if rec["kind"]=="DIMG": probe.load_dimg(rec["path"])
            else: probe.load(rec["path"],rec.get("tile_size",8),rec.get("margin",0),rec.get("spacing",0))
            if len(probe.tiles_base) < len(rec["tile_ids"]):
                removed=set(rec["tile_ids"][len(probe.tiles_base):])
                used=0
                for grid in (self.state.bg_a,self.state.bg_b):
                    for row in grid:
                        used += sum(1 for c in row if c.tile_id in removed)
                used += sum(1 for o in self.state.objects if o.tile_id in removed)
                if used:
                    messagebox.showerror(
                        "Rechargement refusé",
                        f"La nouvelle source contient {len(rec['tile_ids'])-len(probe.tiles_base)} tile(s) de moins, "
                        f"mais {used} placement(s) de la map utilisent encore ces IDs.\n\n"
                        "Restaure ces tiles dans le tileset ou retire leurs placements avant de recharger."
                    )
                    return
            if rec["kind"]=="DIMG":
                newmap,notes=self.tileset._plan_palette_map(probe,self._palette_limit(),rec.get("palette_map",{}),excluding_uid=rec["uid"])
            else: newmap,notes={},[]
            oldmap=dict(rec.get("palette_map",{}))
            moving=any(int(newmap.get(int(k),v))!=int(v) for k,v in oldmap.items())
            if moving:
                used=self._source_cells_count(rec)
                detail="\n".join(f"• P{k} source : P{v} → P{newmap.get(int(k),v)}" for k,v in oldmap.items() if newmap.get(int(k),v)!=v)
                if not messagebox.askyesno("Palette modifiée",
                    f"La palette de {rec['name']} a changé et entre maintenant en conflit avec une autre source.\n\n{detail}\n\n"
                    f"Le Map Builder peut déplacer uniquement cette source et mettre à jour ses {used} cellule(s)/objet(s) déjà placés. Continuer ?"):
                    return
                self.push_undo()
            old_count=len(rec["tile_ids"])
            oldmap,newmap,notes=self.tileset.reload_source(rec,self._palette_limit())
            changed=self._remap_source_cells_palettes(rec,oldmap,newmap) if moving else 0
            self._refresh_tileset_source_ui(select_rec=rec)
            if self.selected_tile_id not in self.tileset.global_to_local:
                self.selected_tile_id=rec["tile_ids"][0] if rec["tile_ids"] else (min(self.tileset.global_to_local) if self.tileset.global_to_local else -1)
            self.redraw_palette_ui(); self.redraw_tileset(force=True); self.redraw_selected_tile_preview(); self.redraw_map(force=True)
            delta=len(rec["tile_ids"])-old_count
            extra=f" • +{delta} nouvelle(s) tile(s)" if delta>0 else (f" • {delta} tile(s)" if delta<0 else "")
            self.status.configure(text=f"Source rechargée : {rec['name']}{extra} • IDs existants conservés" + (f" • {changed} palettes de cellules remappées" if changed else ""))
        except Exception as e:
            messagebox.showerror("Recharger source",str(e))

    def show_tileset_library_info(self):
        if not self.tileset.sources:
            messagebox.showinfo("Bibliothèque tilesets","Aucune source chargée."); return
        lines=[f"Bibliothèque : {len(self.tileset.sources)} source(s) • {self.tileset.next_tile_id}/1024 IDs", ""]
        for rec in self.tileset.sources:
            maps=", ".join(f"P{k}→P{v}" if int(k)!=int(v) else f"P{k}" for k,v in sorted(rec.get("palette_map",{}).items())) or "palette inconnue"
            used=self._source_cells_count(rec)
            lines.append(f"{rec['name']} - {len(rec['tile_ids'])} tiles - {maps} - {used} placement(s)")
        lines += ["", "Règle : aucune banque déjà utilisée n'est écrasée. Un conflit est remappé vers une banque libre, sinon l'ajout est refusé."]
        messagebox.showinfo("Bibliothèque tilesets","\n".join(lines))

    # ------------------------- MODE / PRIORITY -------------------------

    def on_mode_change(self):
        new_mode=self.mode_var.get()
        limit=int(DMS_MODES[new_mode]["palettes"])
        invalid=[pid for pid in self.tileset.palette_ids if int(pid)>=limit]
        if invalid:
            previous=self.state.mode if self.state.mode in DMS_MODES else "Mode 0 - STANDARD"
            self.mode_var.set(previous)
            messagebox.showerror(
                "Mode incompatible",
                f"Impossible de passer en {new_mode} : la bibliothèque utilise " + ", ".join(f"P{x}" for x in invalid) +
                f" alors que ce mode s'arrête à P{limit-1}.\n\nRecharge/remappe les sources avant de changer de mode."
            )
            return
        self.state.mode = new_mode
        self._sync_mode_ui()
        self.redraw_map()

    def _sync_mode_ui(self):
        m = DMS_MODES[self.mode_var.get()]
        self.mode_info.configure(text=m["description"])
        self.palette_cb["values"] = list(range(m["palettes"]))
        if self.palette_var.get() >= m["palettes"]:
            self.palette_var.set(0)
        if not m["has_bg_b"] and self.active_layer.get() == "BG B":
            self.active_layer.set("BG A")
        if hasattr(self, "bg_b_visibility_check") and self.bg_b_visibility_check is not None:
            if m["has_bg_b"]:
                self.bg_b_visibility_check.state(["!disabled"])
            else:
                self.show_bg_b_var.set(False)
                self.bg_b_visibility_check.state(["disabled"])
        self._update_priority_ui()
        if hasattr(self, "palette_panel_canvas"):
            self.redraw_palette_ui()

    def on_layer_change(self):
        if self.active_layer.get() == "BG B" and not DMS_MODES[self.mode_var.get()]["has_bg_b"]:
            messagebox.showwarning("BG B", "Ce mode DMS-1 ne possède pas BG B.")
            self.active_layer.set("BG A")
        if self.map_selection and self.map_selection_layer != self.active_layer.get():
            self.clear_map_selection(silent=True)
        self.paste_mode = False
        self._update_priority_ui()
        self.refresh_brush_preview()
        self._update_view_badge()

    def on_depth_change(self):
        self.front_var.set(self.depth_cb.get() == "Devant sprites")
        self._update_priority_ui()

    def _update_priority_ui(self):
        layer = self.active_layer.get()
        if layer in ("BG A","BG B"):
            front = self.depth_cb.get() == "Devant sprites"
            code = expected_priority_code(layer, front)
            self.priority_code_var.set(str(code))
            self.code_badge.configure(text=f"code {code}")
            self.depth_cb.configure(state="readonly")
        else:
            self.code_badge.configure(text="-")
            self.depth_cb.configure(state="disabled")

    def on_palette_change(self):
        self.redraw_palette_ui()
        self.redraw_tileset(force=True)
        self.refresh_brush_preview()
        self.redraw_selected_tile_preview()
        pid = int(self.palette_var.get())
        if self.tileset.has_palette_data():
            self.status.configure(text=f"Palette active : P{pid} - aperçu RGB333 exact du .dimg.")
        else:
            self.status.configure(text=f"Palette active : P{pid} - PNG brut, couleurs de banque non connues.")

    def redraw_palette_ui(self):
        """Affiche la banque active près du pinceau et toutes les banques dans l'onglet Palettes."""
        pid = int(self.palette_var.get())
        if hasattr(self, "active_palette_canvas"):
            c = self.active_palette_canvas
            c.delete("all")
            colors = self.tileset.palette_colors_hex(pid) if self.tileset.has_palette_data() else []
            if colors:
                w = max(1, int(c.cget("width")))
                sw = max(5, w // 16)
                for i, col in enumerate(colors[:16]):
                    x0, x1 = i*sw, min(w, (i+1)*sw)
                    if i == 0:
                        # Slot matériel transparent : damier + T au lieu d'un faux noir opaque.
                        mid = max(x0+1, (x0+x1)//2)
                        c.create_rectangle(x0, 0, x1, 18, fill="#5d636c", outline="")
                        c.create_rectangle(x0, 0, mid, 9, fill="#2f343b", outline="")
                        c.create_rectangle(mid, 9, x1, 18, fill="#2f343b", outline="")
                        if sw >= 7:
                            c.create_text((x0+x1)/2, 9, text="T", fill="#ffffff", font=("Segoe UI", 6, "bold"))
                    else:
                        c.create_rectangle(x0, 0, x1, 18, fill=col, outline="")
            else:
                c.create_text(66, 9, text=f"P{pid} • couleurs ?", fill="#bfc5cc", font=("Segoe UI", 7))

        if not hasattr(self, "palette_panel_canvas"):
            return
        c = self.palette_panel_canvas
        c.delete("all")
        mode_count = int(DMS_MODES[self.mode_var.get()]["palettes"])
        bank_ids = list(range(mode_count))
        if self.tileset.has_palette_data():
            if len(self.tileset.sources) > 1:
                self.palette_source_var.set(
                    f"Bibliothèque • {len(self.tileset.sources)} sources • banques projet : " +
                    ", ".join(f"P{x}" for x in self.tileset.palette_ids)
                )
            else:
                self.palette_source_var.set(
                    f"{Path(self.tileset.path).name} • DIMG • banques présentes : " +
                    ", ".join(f"P{x}" for x in self.tileset.palette_ids)
                )
        elif self.tileset.source_kind == "PNG":
            self.palette_source_var.set(
                f"{Path(self.tileset.path).name} • PNG brut • banques RGB333 absentes du fichier."
            )
        else:
            self.palette_source_var.set("Aucune ressource palette chargée.")

        y = 8
        for bank in bank_ids:
            active = bank == pid
            c.create_rectangle(5, y, 350, y+25, fill="#2b3037" if active else "#25292f",
                               outline="#f2c94c" if active else "#4d545e", width=2 if active else 1,
                               tags=(f"pal_{bank}", "palrow"))
            c.create_text(13, y+12, text=f"P{bank}", anchor="w", fill="#ffffff" if active else "#c9cfd6",
                          font=("Segoe UI", 9, "bold"), tags=(f"pal_{bank}", "palrow"))
            colors = self.tileset.palette_colors_hex(bank) if self.tileset.has_palette_data() else []
            x0 = 48
            if colors:
                sw = 17
                for i, col in enumerate(colors[:16]):
                    sx0, sx1 = x0+i*sw, x0+(i+1)*sw-1
                    if i == 0:
                        # Première case = index 0 transparent sur DMS/VDP, quelle que soit sa valeur RGB333.
                        midx = (sx0+sx1)//2; midy = y+12
                        c.create_rectangle(sx0, y+4, sx1, y+21, fill="#5d636c", outline="#111318",
                                           tags=(f"pal_{bank}", "palrow"))
                        c.create_rectangle(sx0+1, y+5, midx, midy, fill="#2f343b", outline="",
                                           tags=(f"pal_{bank}", "palrow"))
                        c.create_rectangle(midx, midy, sx1-1, y+20, fill="#2f343b", outline="",
                                           tags=(f"pal_{bank}", "palrow"))
                        c.create_text((sx0+sx1)/2, y+12, text="T", fill="#ffffff", font=("Segoe UI", 7, "bold"),
                                      tags=(f"pal_{bank}", "palrow"))
                    else:
                        c.create_rectangle(sx0, y+4, sx1, y+21, fill=col, outline="#111318",
                                           tags=(f"pal_{bank}", "palrow"))
            else:
                c.create_text(x0, y+12, text="couleurs non disponibles", anchor="w", fill="#7f8994",
                              font=("Segoe UI", 8), tags=(f"pal_{bank}", "palrow"))
            y += 31
        c.configure(scrollregion=(0,0,360,max(260,y+5)))

    def palette_panel_click(self, event):
        items = self.palette_panel_canvas.find_overlapping(event.x, event.y, event.x, event.y)
        for item in reversed(items):
            for tag in self.palette_panel_canvas.gettags(item):
                if tag.startswith("pal_") and tag[4:].isdigit():
                    self.palette_var.set(int(tag[4:]))
                    self.on_palette_change()
                    return

    def on_visibility_change(self):
        """Toutes les cases de visibilité ont un effet immédiat et explicite."""
        self.redraw_map(force=True)
        visible = []
        if self.show_bg_b_var.get() and DMS_MODES[self.mode_var.get()]["has_bg_b"]: visible.append("BG B")
        if self.show_bg_a_var.get(): visible.append("BG A")
        if self.show_objects_var.get(): visible.append("Objets")
        if self.show_collision_var.get(): visible.append("Collisions")
        if self.show_events_var.get(): visible.append("Événements")
        self.status.configure(text="Visibilité : " + (", ".join(visible) if visible else "aucun calque visible"))

    def _event_form_changed(self):
        if self.selected_cell:
            self.status.configure(text="Événement modifié dans le formulaire - clique « Appliquer à la cellule » pour l'enregistrer.")
        else:
            self.status.configure(text="Sélectionne d'abord une cellule à inspecter avant d'appliquer l'événement.")

    def solo_layer(self, layer):
        """Mode inspection : révèle un fond même s'il est normalement couvert par l'autre."""
        if layer == "BG B" and not DMS_MODES[self.mode_var.get()]["has_bg_b"]:
            messagebox.showinfo("BG B", "Ce mode vidéo ne possède pas de BG B.")
            return
        self.show_bg_a_var.set(layer == "BG A")
        self.show_bg_b_var.set(layer == "BG B")
        self.show_objects_var.set(False)
        self.redraw_map(force=True)
        self.status.configure(text=f"Inspection : {layer} seul. Bouton « Tout » pour revenir au rendu complet.")

    def show_all_layers(self):
        self.show_bg_a_var.set(True)
        self.show_bg_b_var.set(bool(DMS_MODES[self.mode_var.get()]["has_bg_b"]))
        self.show_objects_var.set(True)
        self.redraw_map(force=True)
        self.status.configure(text="Rendu complet : BG B + BG A + objets selon les priorités.")

    # ------------------------- TILE BROWSER -------------------------

    def _tileset_scroll_y(self,*args):
        self.tileset_canvas.yview(*args); self.request_tileset_redraw()

    def _tileset_yscroll_changed(self,first,last):
        if hasattr(self,"_tileset_sb"): self._tileset_sb.set(first,last)
        self.request_tileset_redraw()

    def _thumb_target_px(self):
        try:
            return max(24, min(128, int(str(self.tileset_thumb_var.get()).split()[0])))
        except Exception:
            return 64

    def _set_thumb_target_px(self, px):
        choices = [32, 48, 64, 80, 96]
        nearest = min(choices, key=lambda v: abs(v-int(px)))
        self.tileset_thumb_var.set(f"{nearest} px")
        self.redraw_tileset(force=True)

    def _tileset_zoom_wheel(self, event):
        if not event.delta:
            return "break"
        choices = [32, 48, 64, 80, 96]
        cur = self._thumb_target_px()
        idx = min(range(len(choices)), key=lambda i: abs(choices[i]-cur))
        idx = max(0, min(len(choices)-1, idx + (1 if event.delta > 0 else -1)))
        self.tileset_thumb_var.set(f"{choices[idx]} px")
        self.redraw_tileset(force=True)
        self.status.configure(text=f"Taille des vignettes : {choices[idx]} px.")
        return "break"

    def _tileset_mousewheel(self,event):
        if event.delta:
            self.tileset_canvas.yview_scroll(-1 if event.delta>0 else 1,"units")
            self.request_tileset_redraw()
        return "break"

    def request_tileset_redraw(self):
        if self._tileset_after_id is None:
            self._tileset_after_id=self.after_idle(self._run_tileset_redraw)

    def _run_tileset_redraw(self):
        self._tileset_after_id=None
        self.redraw_tileset(force=False)

    def redraw_tileset(self,force=True):
        # Ne jamais vider les références PhotoImage avant de savoir qu'un redraw aura lieu.
        visible_ids = self.tileset.visible_tile_ids(self.tileset_source_var.get()) if self.tileset.tiles_base else []
        if not visible_ids:
            sr=(0,0,300,200)
            if sr!=self._tileset_scrollregion_cache:
                self.tileset_canvas.configure(scrollregion=sr)
                self._tileset_scrollregion_cache=sr
            key=("empty",self.tileset_source_var.get())
            if not force and self._tileset_view_last==key and self.tileset_canvas.find_withtag("tile_view"):
                return
            self._tileset_view_last=key
            self.tileset_img_keepalive=[]
            self.tileset_canvas.delete("tile_view")
            msg="Ajoute un tileset DMS .dimg" if not self.tileset.sources else "Aucune tile dans cette source"
            self.tileset_canvas.create_text(150,80,text=msg,fill="#8f98a3",tags=("tile_view",))
            return

        cw=max(180,self.tileset_canvas.winfo_width())
        ch=max(100,self.tileset_canvas.winfo_height())
        ts=self.tileset.tile_size
        target_px=self._thumb_target_px()
        scale=max(1,min(12,int(round(target_px/max(1,ts)))))
        shown_px=ts*scale
        cell=shown_px+18
        cols=max(1,(cw-10)//cell)
        rows=(len(visible_ids)+cols-1)//cols
        total_h=rows*cell+12
        sr=(0,0,cw,total_h)
        if sr!=self._tileset_scrollregion_cache:
            self.tileset_canvas.configure(scrollregion=sr); self._tileset_scrollregion_cache=sr
        top=self.tileset_canvas.canvasy(0); bottom=self.tileset_canvas.canvasy(ch)
        r0=max(0,int(top//cell)-2); r1=min(rows,int(bottom//cell)+3)
        key=(cols,r0,r1,cw,ch,self.selected_tile_id,self._thumb_target_px(),int(self.palette_var.get()),
             self.tileset_source_var.get(),tuple(visible_ids[max(0,r0*cols):min(len(visible_ids),r1*cols)]))
        if not force and key==self._tileset_view_last and self.tileset_canvas.find_withtag("tile_view"):
            return

        self._tileset_view_last=key
        new_keepalive=[]
        self.tileset_canvas.delete("tile_view")
        first=r0*cols; last=min(len(visible_ids),r1*cols)
        for pos in range(first,last):
            gid=visible_ids[pos]
            r,c=divmod(pos,cols)
            x=5+c*cell; y=5+r*cell
            dimg=self.tileset.display_tile(gid,False,False,scale,int(self.palette_var.get()),cache_group="ui")
            if dimg is None: continue
            new_keepalive.append(dimg)
            self.tileset_canvas.create_image(x,y,image=dimg,anchor="nw",tags=("tile_view",f"tile_{gid}"))
            self.tileset_canvas.create_rectangle(
                x-1,y-1,x+shown_px+1,y+shown_px+1,
                outline="#ffd166" if gid==self.selected_tile_id else "#4d545e",
                width=2 if gid==self.selected_tile_id else 1,
                tags=("tile_view",f"tile_{gid}")
            )
            src=self.tileset.source_for_tile(gid)
            label=str(gid)
            if src and len(self.tileset.sources)>1:
                label=f"{gid} · {src['name'][:9]}"
            self.tileset_canvas.create_text(x+2,y+shown_px+3,text=label,anchor="nw",
                                            fill="#bfc5cc",font=("Segoe UI",7),tags=("tile_view",f"tile_{gid}"))
        self.tileset_img_keepalive=new_keepalive

    def tileset_click(self,event):
        x=self.tileset_canvas.canvasx(event.x)
        y=self.tileset_canvas.canvasy(event.y)
        items=self.tileset_canvas.find_overlapping(x,y,x,y)
        for item in reversed(items):
            for tag in self.tileset_canvas.gettags(item):
                # Le tag technique "tile_view" partage le préfixe tile_.
                # Ne considérer comme sélection que les tags tile_<entier>.
                if tag.startswith("tile_"):
                    suffix = tag[5:]
                    if not suffix.isdigit():
                        continue
                    self.selected_tile_id = int(suffix)
                    self.current_tool.set("BRUSH")
                    usage = sorted(self.tileset.tile_palette_usage.get(self.selected_tile_id, []))
                    # Une tile DIMG connaît généralement sa banque d'origine. Si elle n'en
                    # utilise qu'une, la sélectionner doit aussi sélectionner la bonne palette :
                    # on évite de peindre accidentellement une lanterne P2 avec P0.
                    if usage and int(self.palette_var.get()) not in usage:
                        self.palette_var.set(usage[0])
                        self.redraw_palette_ui()
                    src=self.tileset.source_for_tile(self.selected_tile_id)
                    srcname=f" • {src['name']}" if src else ""
                    extra = (" • palette " + ", ".join(f"P{x}" for x in usage)) if usage else ""
                    self.tile_info.configure(text=f"Tile #{self.selected_tile_id}{srcname} • pinceau • P{int(self.palette_var.get())}{extra}")
                    self.redraw_tileset(force=True)
                    self.refresh_brush_preview()
                    self.redraw_selected_tile_preview()
                    return

    def tileset_double_click(self, event):
        self.tileset_click(event)
        self.open_tile_preview_window()
        return "break"

    def toggle_flip_x(self):
        self.flip_x_var.set(not bool(self.flip_x_var.get()))

    def toggle_flip_y(self):
        self.flip_y_var.set(not bool(self.flip_y_var.get()))

    def reset_flips(self):
        self.flip_x_var.set(False)
        self.flip_y_var.set(False)

    def _on_flip_var_changed(self, *_args):
        self._update_flip_buttons()
        self.refresh_brush_preview()
        self.redraw_selected_tile_preview()

    def _update_flip_buttons(self):
        fx = bool(self.flip_x_var.get())
        fy = bool(self.flip_y_var.get())
        self.flip_x_text_var.set(f"↔ Flip horizontal : {'OUI' if fx else 'NON'}")
        self.flip_y_text_var.set(f"↕ Flip vertical : {'OUI' if fy else 'NON'}")
        for name, active in (("flip_x_button", fx), ("flip_y_button", fy)):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.configure(bg="#b07a00" if active else "#2f333a",
                              fg="#ffffff" if active else "#d9d9d9",
                              activebackground="#d89c16" if active else "#444a53",
                              activeforeground="#ffffff")

    def _draw_preview_checker(self, canvas, x, y, size, cell=8):
        """Fond damier : la transparence devient visible au lieu de sembler noire."""
        c1, c2 = "#31363d", "#4a5058"
        step=max(4,int(cell))
        yy=0
        while yy < size:
            xx=0
            while xx < size:
                col=c1 if ((xx//step)+(yy//step))%2==0 else c2
                canvas.create_rectangle(x+xx,y+yy,min(x+xx+step,x+size),min(y+yy+step,y+size),
                                        fill=col,outline="")
                xx += step
            yy += step

    def _draw_tile_preview_to_canvas(self, canvas, large=False):
        canvas.delete("all")
        self.selected_preview_keepalive = []
        cw = max(240, canvas.winfo_width())
        ch = max(100, canvas.winfo_height())
        if self.selected_tile_id < 0 or not self.tileset.tiles_base:
            canvas.create_text(cw/2, ch/2, text="Sélectionne une tile", fill="#8f98a3", font=("Segoe UI", 10))
            return
        ts = max(1, self.tileset.tile_size)
        target = 128 if large else 72
        scale = max(1, min(16, target // ts))
        pal = int(self.palette_var.get())
        original = self.tileset.display_tile(self.selected_tile_id, False, False, scale, pal)
        result = self.tileset.display_tile(self.selected_tile_id, bool(self.flip_x_var.get()), bool(self.flip_y_var.get()), scale, pal)
        if original is None or result is None:
            canvas.create_text(cw/2, ch/2, text="Aperçu indisponible", fill="#ff8b8b")
            return
        self.selected_preview_keepalive = [original, result]
        size = ts * scale
        gap = 54 if large else 34
        total = size*2 + gap
        x0 = max(8, (cw-total)//2)
        y0 = max(25, (ch-size)//2 + 6)
        x1 = x0 + size + gap
        canvas.create_text(x0+size/2, 12, text="ORIGINAL", fill="#bfc5cc", font=("Segoe UI",8,"bold"))
        canvas.create_text(x1+size/2, 12, text="RÉSULTAT", fill="#ffd166", font=("Segoe UI",8,"bold"))
        canvas.create_rectangle(x0-2,y0-2,x0+size+2,y0+size+2,outline="#4d545e",width=2)
        self._draw_preview_checker(canvas,x0,y0,size,max(4,scale*2))
        canvas.create_image(x0,y0,image=original,anchor="nw")
        canvas.create_text(x0+size+gap/2, y0+size/2, text="→", fill="#00d4ff", font=("Segoe UI",18,"bold"))
        canvas.create_rectangle(x1-3,y0-3,x1+size+3,y0+size+3,outline="#ffd166",width=3)
        self._draw_preview_checker(canvas,x1,y0,size,max(4,scale*2))
        canvas.create_image(x1,y0,image=result,anchor="nw")
        state = []
        if self.flip_x_var.get(): state.append("H")
        if self.flip_y_var.get(): state.append("V")
        canvas.create_text(cw/2, ch-9, text=f"Tile #{self.selected_tile_id} • P{pal} • Flip {'+'.join(state) if state else 'aucun'}",
                           fill="#d9d9d9", font=("Segoe UI",8))
        # Le popup utilise une référence séparée pour éviter le GC Tk si l'aperçu intégré est redessiné ensuite.
        canvas._dms_preview_keepalive = [original, result]

    def redraw_selected_tile_preview(self):
        if hasattr(self, "tile_preview_canvas"):
            self._draw_tile_preview_to_canvas(self.tile_preview_canvas, large=False)
        if self.tile_preview_window is not None:
            try:
                if self.tile_preview_window.winfo_exists() and self.tile_preview_popup_canvas is not None:
                    self._draw_tile_preview_to_canvas(self.tile_preview_popup_canvas, large=True)
                else:
                    self.tile_preview_window = None
                    self.tile_preview_popup_canvas = None
            except tk.TclError:
                self.tile_preview_window = None
                self.tile_preview_popup_canvas = None

    def open_tile_preview_window(self):
        if self.selected_tile_id < 0:
            self.status.configure(text="Sélectionne d'abord une tile à agrandir.")
            return
        if self.tile_preview_window is not None:
            try:
                if self.tile_preview_window.winfo_exists():
                    self.tile_preview_window.deiconify(); self.tile_preview_window.lift(); self.redraw_selected_tile_preview(); return
            except tk.TclError:
                pass
        win = tk.Toplevel(self)
        self.tile_preview_window = win
        win.title("Tile sélectionnée - aperçu des flips")
        win.geometry("520x330")
        win.transient(self)
        ttk.Label(win, text="Manipulation visible en direct", font=("Segoe UI",11,"bold")).pack(anchor="w", padx=12, pady=(10,2))
        ttk.Label(win, text="Les boutons Flip du Map Builder mettent immédiatement à jour le résultat.", style="Sub.TLabel").pack(anchor="w", padx=12)
        self.tile_preview_popup_canvas = tk.Canvas(win, bg="#202329", highlightthickness=0, height=230)
        self.tile_preview_popup_canvas.pack(fill="both", expand=True, padx=12, pady=10)
        self.tile_preview_popup_canvas.bind("<Configure>", lambda e: self.redraw_selected_tile_preview())
        bottom = ttk.Frame(win, padding=(12,0,12,10)); bottom.pack(fill="x")
        ttk.Button(bottom, textvariable=self.flip_x_text_var, command=self.toggle_flip_x).pack(side="left", padx=(0,4))
        ttk.Button(bottom, textvariable=self.flip_y_text_var, command=self.toggle_flip_y).pack(side="left", padx=4)
        ttk.Button(bottom, text="Reset", command=self.reset_flips).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", self._close_tile_preview_window)
        self.redraw_selected_tile_preview()

    def _close_tile_preview_window(self):
        if self.tile_preview_window is not None:
            try: self.tile_preview_window.destroy()
            except tk.TclError: pass
        self.tile_preview_window = None
        self.tile_preview_popup_canvas = None

    # ------------------------- MAP RENDER / LARGE MAP VIEWPORT -------------------------

    def _scroll_x(self, *args):
        self.map_canvas.xview(*args)
        # En mode bandes pleine largeur, Tk sait déplacer l'image immédiatement :
        # ne pas programmer un redraw inutile à chaque pixel de scrollbar.
        if not self._fast_horizontal_mode:
            self.request_viewport_redraw()

    def _scroll_y(self, *args):
        self.map_canvas.yview(*args)
        self.request_viewport_redraw()

    def _xscroll_changed(self, first, last):
        if hasattr(self, "_map_hsb"):
            self._map_hsb.set(first, last)
        if self._fast_horizontal_mode and self._render_bounds:
            visible = self._viewport_cells(margin=0)
            if self._bounds_cover_visible(self._render_bounds, visible):
                self._viewport_last = visible
                self._update_view_badge(visible)
                return
        self.request_viewport_redraw()

    def _yscroll_changed(self, first, last):
        if hasattr(self, "_map_vsb"):
            self._map_vsb.set(first, last)
        self.request_viewport_redraw()

    def request_viewport_redraw(self):
        """Fusionne les événements de scroll en une frame UI (~60 Hz max)."""
        self._update_view_badge()
        if self._viewport_after_id is not None:
            return
        self._viewport_after_id = self.after(16, self._run_viewport_redraw)

    def _run_viewport_redraw(self):
        self._viewport_after_id = None
        self.redraw_map(force=False)

    def _mousewheel(self, event):
        """Molette = zoom centré sous le pointeur, sans reconstruire à chaque cran."""
        if not event.delta:
            return "break"
        step = 1 if event.delta > 0 else -1
        base = self._zoom_target if self._zoom_after_id is not None else self.zoom
        target = max(1, min(8, int(base) + step))
        self._zoom_target = target
        self._zoom_anchor_xy = (int(event.x), int(event.y))
        self.zoom_var.set(f"{target}×")
        if self._zoom_after_id is not None:
            try:
                self.after_cancel(self._zoom_after_id)
            except Exception:
                pass
        self._zoom_after_id = self.after(self.zoom_debounce_ms, self._apply_pending_zoom)
        self.status.configure(text=f"Zoom demandé : {target}×…")
        return "break"

    def _apply_pending_zoom(self):
        self._zoom_after_id = None
        target = max(1, min(8, int(self._zoom_target)))
        anchor_xy = self._zoom_anchor_xy
        self._zoom_anchor_xy = None
        self._apply_zoom_now(target, anchor_xy=anchor_xy)

    def _vertical_mousewheel(self, event):
        if not event.delta:
            return "break"
        self.map_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        self.request_viewport_redraw()
        return "break"

    def _shift_mousewheel(self, event):
        if not event.delta:
            return "break"
        self.map_canvas.xview_scroll(-1 if event.delta > 0 else 1, "units")
        if not self._fast_horizontal_mode:
            self.request_viewport_redraw()
        return "break"

    def _pan_start(self, event):
        self._pan_active = True
        self.map_canvas.scan_mark(event.x, event.y)
        self.map_canvas.configure(cursor="fleur")
        return "break"

    def _pan_drag(self, event):
        if self._pan_active:
            self.map_canvas.scan_dragto(event.x, event.y, gain=1)
            self.request_viewport_redraw()
        return "break"

    def _pan_end(self, event):
        self._pan_active = False
        self.map_canvas.configure(cursor="")
        self.request_viewport_redraw()
        return "break"

    def _viewport_cells(self, margin=None):
        ts = max(1, self.state.tile_size * self.zoom)
        margin = self.viewport_margin_tiles if margin is None else max(0, int(margin))
        cw = max(2, self.map_canvas.winfo_width())
        ch = max(2, self.map_canvas.winfo_height())
        left = self.map_canvas.canvasx(0)
        top = self.map_canvas.canvasy(0)
        right = self.map_canvas.canvasx(cw)
        bottom = self.map_canvas.canvasy(ch)
        # Avant le premier mapping Tk, winfo peut valoir 1 pixel : rendre une petite zone utile.
        if cw <= 2:
            right = left + 900
        if ch <= 2:
            bottom = top + 650
        x0 = max(0, int(left // ts) - margin)
        y0 = max(0, int(top // ts) - margin)
        x1 = min(self.state.width, int(right // ts) + margin + 2)
        y1 = min(self.state.height, int(bottom // ts) + margin + 2)
        return x0, y0, max(x0, x1), max(y0, y1)

    def _visible_world_rect(self):
        x0, y0, x1, y1 = self._viewport_cells(margin=0)
        return (x0 * self.state.tile_size, y0 * self.state.tile_size,
                x1 * self.state.tile_size, y1 * self.state.tile_size)

    def set_zoom(self):
        self._set_zoom(int(self.zoom_var.get().replace("×", "")))

    def _set_zoom(self, new_zoom, anchor_event=None):
        """Zoom immédiat pour boutons/combobox/raccourcis clavier.

        La molette utilise _apply_pending_zoom afin de fusionner les rafales.
        """
        if self._zoom_after_id is not None:
            try:
                self.after_cancel(self._zoom_after_id)
            except Exception:
                pass
            self._zoom_after_id = None
        self._zoom_target = max(1, min(8, int(new_zoom)))
        anchor_xy = None
        if anchor_event is not None:
            anchor_xy = (int(anchor_event.x), int(anchor_event.y))
        self._apply_zoom_now(self._zoom_target, anchor_xy=anchor_xy)

    def _apply_zoom_now(self, new_zoom, anchor_xy=None):
        new_zoom = max(1, min(8, int(new_zoom)))
        old_zoom = max(1, self.zoom)
        if new_zoom == old_zoom:
            self.zoom_var.set(f"{new_zoom}×")
            return
        cw = max(1, self.map_canvas.winfo_width())
        ch = max(1, self.map_canvas.winfo_height())
        if anchor_xy is not None:
            screen_x = max(0, min(cw, int(anchor_xy[0])))
            screen_y = max(0, min(ch, int(anchor_xy[1])))
        else:
            screen_x, screen_y = cw/2, ch/2
        world_x = self.map_canvas.canvasx(screen_x) / old_zoom
        world_y = self.map_canvas.canvasy(screen_y) / old_zoom

        # Libère les variantes agrandies de l'ancien niveau avant d'en créer d'autres.
        # Sans cela, une séquence 2x->3x->...->8x garde plusieurs centaines de
        # PhotoImage géantes en mémoire et peut faire tomber Tk sous Windows.
        self.map_canvas.delete("brush_preview")
        self._cursor_img = None
        self._paste_preview_keepalive = []
        self.tileset.clear_map_display_cache()

        self.zoom = new_zoom
        self._zoom_target = new_zoom
        self.zoom_var.set(f"{new_zoom}×")
        wpx = max(1, self.state.width * self.state.tile_size * new_zoom)
        hpx = max(1, self.state.height * self.state.tile_size * new_zoom)
        scrollregion = (0, 0, wpx, hpx)
        self.map_canvas.configure(scrollregion=scrollregion)
        self._scrollregion_cache = scrollregion
        target_x = world_x * new_zoom - screen_x
        target_y = world_y * new_zoom - screen_y
        max_x_frac = max(0.0, (wpx-cw)/wpx)
        max_y_frac = max(0.0, (hpx-ch)/hpx)
        self.map_canvas.xview_moveto(max(0.0, min(max_x_frac, target_x / max(1, wpx))))
        self.map_canvas.yview_moveto(max(0.0, min(max_y_frac, target_y / max(1, hpx))))

        # Le changement d'échelle invalide forcément les anciennes bandes composées.
        self._render_bounds = None
        self.redraw_map(force=True)
        self.status.configure(text=f"Zoom map : {new_zoom}× - rendu optimisé.")

    def view_100(self):
        self._set_zoom(1)

    def goto_tile(self):
        try:
            x = max(0, min(self.state.width - 1, int(self.goto_x_var.get())))
            y = max(0, min(self.state.height - 1, int(self.goto_y_var.get())))
        except Exception:
            return
        ts = self.state.tile_size * self.zoom
        wpx = max(1, self.state.width * ts)
        hpx = max(1, self.state.height * ts)
        cw = max(1, self.map_canvas.winfo_width())
        ch = max(1, self.map_canvas.winfo_height())
        self.map_canvas.xview_moveto(max(0.0, min(1.0, (x * ts - cw / 2) / wpx)))
        self.map_canvas.yview_moveto(max(0.0, min(1.0, (y * ts - ch / 2) / hpx)))
        self.redraw_map(force=True)
        self.status.configure(text=f"Vue centrée vers tile x{x} y{y}.")

    def show_overview(self):
        """Aperçu global volontairement léger : pas de duplication du tileset."""
        win = tk.Toplevel(self)
        win.title("Voir toute la map")
        win.geometry("1000x260")
        win.transient(self)
        ttk.Label(win, text=(f"{self.state.name} - {self.state.width}×{self.state.height} tiles - "
                            f"{self.state.width*self.state.tile_size}×{self.state.height*self.state.tile_size} px\n"
                            "Clique dans l'aperçu pour déplacer la caméra."),
                  justify="left").pack(fill="x", padx=10, pady=8)
        c = tk.Canvas(win, bg="#1e2126", highlightthickness=0)
        c.pack(fill="both", expand=True, padx=10, pady=(0,10))

        def draw(_event=None):
            c.delete("all")
            cw = max(100, c.winfo_width())
            ch = max(80, c.winfo_height())
            pad = 12
            sx = (cw - pad*2) / max(1, self.state.width)
            sy = (ch - pad*2) / max(1, self.state.height)
            scale = min(sx, sy)
            ow = self.state.width * scale
            oh = self.state.height * scale
            ox = (cw - ow) / 2
            oy = (ch - oh) / 2
            c.create_rectangle(ox, oy, ox+ow, oy+oh, fill="#2a2f36", outline="#7d8792")
            # Une barre par colonne d'aperçu au maximum : coût indépendant de la longueur réelle.
            samples = min(self.state.width, max(1, int(ow)))
            for i in range(samples):
                a = int(i * self.state.width / samples)
                b = max(a+1, int((i+1) * self.state.width / samples))
                occupied_a = False
                occupied_b = False
                for yy in range(self.state.height):
                    row_a = self.state.bg_a[yy]
                    row_b = self.state.bg_b[yy]
                    end = min(b, self.state.width)
                    if not occupied_a and any(row_a[xx].tile_id >= 0 for xx in range(a, end)):
                        occupied_a = True
                    if not occupied_b and any(row_b[xx].tile_id >= 0 for xx in range(a, end)):
                        occupied_b = True
                    if occupied_a and occupied_b:
                        break
                xx = ox + i * ow / samples
                if occupied_b:
                    c.create_line(xx, oy+1, xx, oy+oh-1, fill="#31566f")
                if occupied_a:
                    c.create_line(xx, oy+1, xx, oy+oh-1, fill="#66727e")
            vx0, vy0, vx1, vy1 = self._viewport_cells(margin=0)
            c.create_rectangle(ox+vx0*scale, oy+vy0*scale, ox+vx1*scale, oy+vy1*scale,
                               outline="#f2c94c", width=2)
            c._dms_geom = (ox, oy, scale, ow, oh)

        def click(event):
            geom = getattr(c, "_dms_geom", None)
            if not geom:
                return
            ox, oy, scale, ow, oh = geom
            if scale <= 0:
                return
            x = int((event.x-ox)/scale)
            y = int((event.y-oy)/scale)
            self.goto_x_var.set(max(0, min(self.state.width-1, x)))
            self.goto_y_var.set(max(0, min(self.state.height-1, y)))
            self.goto_tile()
            draw()

        c.bind("<Configure>", draw)
        c.bind("<Button-1>", click)
        draw()

    def _choose_render_bounds(self, visible, ts, wpx):
        """Choisit un rendu adapté au scroll.

        Cas courant : on compose chaque ligne visible sur toute la largeur de la map.
        Le Canvas peut alors défiler horizontalement sans demander la moindre
        reconstruction d'image. C'est beaucoup plus fluide sous Tk/Windows.

        Cas extrême : si une ligne devient gigantesque ou si le tampon dépasserait
        un budget mémoire raisonnable, on retombe automatiquement sur le viewport
        partiel historique.
        """
        vx0, vy0, vx1, vy1 = visible
        y_margin = max(0, int(self.fast_scroll_y_margin_tiles))
        fy0 = max(0, vy0 - y_margin)
        fy1 = min(self.state.height, vy1 + y_margin)
        strip_h_px = max(1, fy1 - fy0) * max(1, ts)
        estimated_pixels = max(1, wpx) * strip_h_px
        can_full_width = (
            self.zoom <= int(self.fast_scroll_max_zoom) and
            wpx <= int(self.fast_scroll_max_row_px) and
            estimated_pixels <= int(self.fast_scroll_pixel_budget)
        )
        if can_full_width:
            self._fast_horizontal_mode = True
            return (0, fy0, self.state.width, fy1)
        self._fast_horizontal_mode = False
        # À fort zoom, 10 tiles de marge représentent énormément de pixels.
        # Une petite marge suffit et évite les pics mémoire au zoom 4x..8x.
        margin = int(self.viewport_margin_tiles)
        if self.zoom >= 3:
            margin = max(2, min(margin, 10 // max(2, self.zoom) + 1))
        return self._viewport_cells(margin=margin)

    def _bounds_cover_visible(self, rendered, visible):
        """Évite tout redraw tant que la caméra reste dans le tampon pré-rendu."""
        if not rendered:
            return False
        rx0, ry0, rx1, ry1 = rendered
        vx0, vy0, vx1, vy1 = visible
        g = max(0, int(self.viewport_guard_tiles))
        left_ok = vx0 >= rx0 + g or rx0 == 0
        top_ok = vy0 >= ry0 + g or ry0 == 0
        right_ok = vx1 <= rx1 - g or rx1 == self.state.width
        bottom_ok = vy1 <= ry1 - g or ry1 == self.state.height
        return left_ok and top_ok and right_ok and bottom_ok

    def _update_view_badge(self, visible=None):
        if not hasattr(self, "viewport_badge"):
            return
        if visible is None:
            try:
                visible = self._viewport_cells(margin=0)
            except Exception:
                visible = (0,0,0,0)
        vx0, vy0, vx1, vy1 = visible
        rendered = max(0, vx1-vx0) * max(0, vy1-vy0)
        src = self.tileset.source_kind if self.tileset.source_kind != "NONE" else "sans tileset"
        scroll_mode = " • scroll direct" if self._fast_horizontal_mode else ""
        self.viewport_badge.configure(
            text=f"{self.active_layer.get()} • {self.current_tool.get()} • P{int(self.palette_var.get())} • "
                 f"vue {rendered} tiles • {src}{scroll_mode}"
        )

    def _copy_tile_to_row(self, row_img, tile_id, flip_x, flip_y, palette_id, dx):
        img = self.tileset.display_tile(tile_id, flip_x, flip_y, self.zoom, palette_id, cache_group="map")
        if img is None:
            return
        row_img.tk.call(row_img, "copy", img, "-to", int(dx), 0, "-compositingrule", "overlay")

    def _rebuild_visual_row(self, y):
        """Compose une ligne entière en une seule PhotoImage Tk.

        Cela remplace des centaines d'objets Canvas individuels par ~28 images de lignes
        pour une map de hauteur 28, ce qui est nettement plus fluide sous Tk/Windows.
        """
        if not self._render_bounds:
            return
        x0, y0, x1, y1 = self._render_bounds
        if not (y0 <= y < y1):
            return
        ts = self.state.tile_size * self.zoom
        width = max(1, (x1-x0) * ts)
        row_img = tk.PhotoImage(master=self, width=width, height=ts)
        m = DMS_MODES[self.mode_var.get()]

        if self.tileset.tiles_base:
            for x in range(x0, x1):
                dx = (x-x0) * ts
                if self.show_bg_b_var.get() and m["has_bg_b"]:
                    c = self.state.bg_b[y][x]
                    if c.tile_id >= 0 and c.priority_code == 2:
                        self._copy_tile_to_row(row_img, c.tile_id, c.flip_x, c.flip_y, c.palette, dx)
                if self.show_bg_a_var.get():
                    c = self.state.bg_a[y][x]
                    if c.tile_id >= 0 and c.priority_code == 0:
                        self._copy_tile_to_row(row_img, c.tile_id, c.flip_x, c.flip_y, c.palette, dx)

                if self.show_objects_var.get():
                    for o in self._objects_by_cell.get((x,y), []):
                        if o.tile_id >= 0:
                            self._copy_tile_to_row(row_img, o.tile_id, o.flip_x, o.flip_y, o.palette, dx)

                if self.show_bg_b_var.get() and m["has_bg_b"]:
                    c = self.state.bg_b[y][x]
                    if c.tile_id >= 0 and c.priority_code == 3:
                        self._copy_tile_to_row(row_img, c.tile_id, c.flip_x, c.flip_y, c.palette, dx)
                if self.show_bg_a_var.get():
                    c = self.state.bg_a[y][x]
                    if c.tile_id >= 0 and c.priority_code == 1:
                        self._copy_tile_to_row(row_img, c.tile_id, c.flip_x, c.flip_y, c.palette, dx)

        self._row_images[y] = row_img
        item = self._row_items.get(y)
        if item and self.map_canvas.type(item):
            self.map_canvas.itemconfigure(item, image=row_img)
        else:
            item = self.map_canvas.create_image(x0*ts, y*ts, image=row_img, anchor="nw",
                                                tags=("viewport", "visual_row", f"row_{y}"))
            self._row_items[y] = item
        self.map_img_keepalive = list(self._row_images.values())

    def _draw_cell_overlays(self, x, y, ts):
        tag = f"overlay_cell_{x}_{y}"
        tags = ("viewport", "editor_overlay", tag)

        if self.show_collision_var.get():
            typ = self.state.collisions[y][x]
            if typ != "NONE":
                colors={"SOLID":"#ff595e","ONE_WAY":"#ffca3a","HAZARD":"#ff006e",
                        "LADDER":"#8ac926","WATER":"#1982c4","SLOW":"#6a4c93","CUSTOM":"#ffffff"}
                col = colors.get(typ, "#ffffff")
                self.map_canvas.create_rectangle(x*ts+2, y*ts+2, (x+1)*ts-2, (y+1)*ts-2,
                                                 outline=col, width=2, tags=tags)

        # Repère événement toujours au-dessus du rendu du jeu.
        ev = self.state.events[y][x]
        if self.show_events_var.get() and ev.enabled:
            cx = (x+1)*ts - max(5, ts//4)
            cy = y*ts + max(5, ts//4)
            r = max(4, min(8, ts//4))
            self.map_canvas.create_polygon(cx,cy-r, cx+r,cy, cx,cy+r, cx-r,cy,
                                           fill="#00d4ff", outline="#071014", width=1, tags=tags)
            if self.show_editor_guides_var.get() and ts >= 16:
                self.map_canvas.create_text(cx, cy, text="E", fill="#071014", font=("Segoe UI",7,"bold"), tags=tags)

        objs = self._objects_by_cell.get((x,y), [])
        if self.show_objects_var.get() and self.show_editor_guides_var.get() and objs:
            self.map_canvas.create_rectangle(x*ts+1, y*ts+1, (x+1)*ts-1, (y+1)*ts-1,
                                             outline="#56e39f", width=2, tags=tags)
            if ts >= 16:
                label = "O" + ",".join(str(o.object_id) for o in objs[:2])
                self.map_canvas.create_rectangle(x*ts+2, y*ts+2, x*ts+min(ts-2, 36), y*ts+13,
                                                 fill="#10291e", outline="", tags=tags)
                self.map_canvas.create_text(x*ts+4, y*ts+7, text=label, anchor="w", fill="#8ff0ba",
                                            font=("Segoe UI",6,"bold"), tags=tags)
            else:
                self.map_canvas.create_rectangle(x*ts+2, y*ts+2, x*ts+6, y*ts+6,
                                                 fill="#56e39f", outline="", tags=tags)

        if self.tech_overlay_var.get():
            layer = self.active_layer.get()
            if layer in ("BG A","BG B"):
                c = (self.state.bg_a if layer == "BG A" else self.state.bg_b)[y][x]
                if c.tile_id >= 0:
                    self.map_canvas.create_rectangle(x*ts+1, y*ts+1, (x+1)*ts-1, (y+1)*ts-1,
                                                     outline="#ffd166" if c.priority_code in (1,3) else "#7e8a99",
                                                     width=1, tags=tags)
                    if ts >= 20:
                        self.map_canvas.create_text(x*ts+2, (y+1)*ts-2,
                                                    text=f"#{c.tile_id} P{c.palette}/{c.priority_code}",
                                                    anchor="sw", fill="#ffffff", font=("Segoe UI",6), tags=tags)

        if self.selected_cell == (x,y):
            self.map_canvas.create_rectangle(x*ts+1, y*ts+1, (x+1)*ts-1, (y+1)*ts-1,
                                             outline="#ff66d8", width=2, dash=(4,2), tags=tags)

    def redraw_map(self, force=True):
        ts = self.state.tile_size * self.zoom
        step = max(4, ts // 2)
        self.map_canvas.configure(xscrollincrement=step, yscrollincrement=step)
        wpx = max(1, self.state.width * ts)
        hpx = max(1, self.state.height * ts)
        scrollregion = (0, 0, wpx, hpx)
        if scrollregion != self._scrollregion_cache:
            self.map_canvas.configure(scrollregion=scrollregion)
            self._scrollregion_cache = scrollregion

        visible = self._viewport_cells(margin=0)
        if not force and self._render_bounds and self._bounds_cover_visible(self._render_bounds, visible):
            self._viewport_last = visible
            self._update_view_badge(visible)
            self.refresh_brush_preview()
            return

        bounds = self._choose_render_bounds(visible, ts, wpx)
        self._viewport_last = visible
        self._render_bounds = bounds
        self.map_canvas.delete("viewport")
        self._row_images = {}
        self._row_items = {}
        self.map_img_keepalive = []
        x0, y0, x1, y1 = bounds
        if x1 <= x0 or y1 <= y0:
            self._update_view_badge(visible)
            return
        left, top, right, bottom = x0*ts, y0*ts, x1*ts, y1*ts
        self.map_canvas.create_rectangle(left, top, right, bottom, fill="#242830", outline="",
                                         tags=("viewport", "viewport_bg"))

        # Une image composite par ligne, au lieu d'un Canvas item par tile et par layer.
        for y in range(y0, y1):
            self._rebuild_visual_row(y)

        # Les overlays d'édition restent vectoriels et légers.
        for y in range(y0, y1):
            for x in range(x0, x1):
                self._draw_cell_overlays(x, y, ts)

        if self.grid_var.get():
            for x in range(x0, x1 + 1):
                xx = x * ts
                self.map_canvas.create_line(xx, top, xx, bottom, fill="#444a53", tags=("viewport", "grid"))
            for y in range(y0, y1 + 1):
                yy = y * ts
                self.map_canvas.create_line(left, yy, right, yy, fill="#444a53", tags=("viewport", "grid"))

        if self.camera_var.get():
            m = DMS_MODES[self.mode_var.get()]
            sw = m["screen_w"] * self.zoom
            sh = m["screen_h"] * self.zoom
            if not (sw < left or sh < top or 0 > right or 0 > bottom):
                self.map_canvas.create_rectangle(0, 0, min(sw, wpx), min(sh, hpx),
                                                 outline="#f2c94c", width=2, dash=(6,4),
                                                 tags=("viewport", "camera"))

        self._draw_selection_overlay()
        self.map_canvas.tag_raise("editor_overlay")
        self.map_canvas.tag_raise("grid")
        self.map_canvas.tag_raise("camera")
        self.map_canvas.tag_raise("selection_overlay")
        self._update_view_badge(visible)
        self.refresh_brush_preview()

    def _redraw_cell(self, x, y):
        if not self._render_bounds:
            return
        x0, y0, x1, y1 = self._render_bounds
        if not (x0 <= x < x1 and y0 <= y < y1):
            return
        self._rebuild_visual_row(y)
        tag = f"overlay_cell_{x}_{y}"
        self.map_canvas.delete(tag)
        self._draw_cell_overlays(x, y, self.state.tile_size * self.zoom)
        self.map_canvas.tag_raise("editor_overlay")
        self.map_canvas.tag_raise("grid")
        self.map_canvas.tag_raise("camera")
        self.map_canvas.tag_raise("selection_overlay")
        self.map_canvas.tag_raise("brush_preview")

    # ------------------------- MAP INPUT / TOOLS -------------------------

    def canvas_cell(self,event):
        ts=self.state.tile_size*self.zoom
        x=int(self.map_canvas.canvasx(event.x)//ts)
        y=int(self.map_canvas.canvasy(event.y)//ts)
        return (x,y) if 0<=x<self.state.width and 0<=y<self.state.height else None

    def map_hover(self,event):
        p=self.canvas_cell(event)
        self._cursor_cell = p
        self.cursor_info.configure(text=f"x{p[0]} y{p[1]}" if p else "x- y-")
        self.refresh_brush_preview()

    def map_leave(self, event=None):
        self._cursor_cell = None
        self.map_canvas.delete("brush_preview")
        self._cursor_img = None
        self.cursor_info.configure(text="x- y-")

    def refresh_brush_preview(self):
        if not hasattr(self, "map_canvas"):
            return
        self.map_canvas.delete("brush_preview")
        self._cursor_img = None
        self._paste_preview_keepalive = []
        p = self._cursor_cell
        if not p:
            return
        if self.selection_dragging:
            return
        x, y = p
        ts = self.state.tile_size * self.zoom
        tool = self.current_tool.get()
        layer = self.active_layer.get()

        if self.paste_mode and self.tile_clipboard:
            w = int(self.tile_clipboard["width"])
            h = int(self.tile_clipboard["height"])
            valid = x + w <= self.state.width and y + h <= self.state.height
            # Pour les blocs raisonnables, on voit réellement les tiles qui vont être collées.
            if valid and w * h <= 256:
                for dy, row in enumerate(self.tile_clipboard["cells"]):
                    for dx, cell in enumerate(row):
                        if cell.tile_id < 0:
                            continue
                        img = self.tileset.display_tile(
                            cell.tile_id, cell.flip_x, cell.flip_y, self.zoom, cell.palette, cache_group="map"
                        )
                        if img is not None:
                            self._paste_preview_keepalive.append(img)
                            self.map_canvas.create_image(
                                (x + dx) * ts, (y + dy) * ts, image=img, anchor="nw",
                                tags=("brush_preview",)
                            )
            outline = "#7cff9b" if valid else "#ff6b6b"
            self.map_canvas.create_rectangle(
                x*ts+1, y*ts+1, (x+w)*ts-1, (y+h)*ts-1,
                outline=outline, width=3, dash=(6,3), tags=("brush_preview",)
            )
            label = f"COLLER {w}×{h}" if valid else f"HORS MAP {w}×{h}"
            self.map_canvas.create_rectangle(
                x*ts+4, y*ts+4, x*ts+4+max(72, len(label)*6), y*ts+21,
                fill="#15341f" if valid else "#401919", outline=outline, tags=("brush_preview",)
            )
            self.map_canvas.create_text(
                x*ts+9, y*ts+12, text=label, anchor="w", fill="#ffffff",
                font=("Segoe UI",7,"bold"), tags=("brush_preview",)
            )
            self.map_canvas.tag_raise("brush_preview")
            return

        if tool == "BRUSH" and layer in ("BG A", "BG B", "OBJECTS") and self.selected_tile_id >= 0:
            img = self.tileset.display_tile(
                self.selected_tile_id,
                bool(self.flip_x_var.get()), bool(self.flip_y_var.get()),
                self.zoom, int(self.palette_var.get()), cache_group="map"
            )
            if img is not None:
                self._cursor_img = img
                self.map_canvas.create_image(x*ts, y*ts, image=img, anchor="nw", tags=("brush_preview",))
            self.map_canvas.create_rectangle(x*ts+1, y*ts+1, (x+1)*ts-1, (y+1)*ts-1,
                                             outline="#00e5ff", width=2, dash=(4,2), tags=("brush_preview",))
            if ts >= 16:
                label = f"#{self.selected_tile_id} P{int(self.palette_var.get())}"
                self.map_canvas.create_rectangle(x*ts+2, y*ts+2, x*ts+min(ts-2, 48), y*ts+13,
                                                 fill="#08262b", outline="", tags=("brush_preview",))
                self.map_canvas.create_text(x*ts+4, y*ts+7, text=label, anchor="w", fill="#b7fbff",
                                            font=("Segoe UI",6,"bold"), tags=("brush_preview",))
        elif tool == "ERASE" and layer in ("BG A", "BG B", "OBJECTS", "COLLISION"):
            self.map_canvas.create_rectangle(x*ts+1, y*ts+1, (x+1)*ts-1, (y+1)*ts-1,
                                             outline="#ff6b6b", width=2, tags=("brush_preview",))
            self.map_canvas.create_line(x*ts+3, y*ts+3, (x+1)*ts-3, (y+1)*ts-3,
                                        fill="#ff6b6b", width=2, tags=("brush_preview",))
            self.map_canvas.create_line((x+1)*ts-3, y*ts+3, x*ts+3, (y+1)*ts-3,
                                        fill="#ff6b6b", width=2, tags=("brush_preview",))
        elif tool == "BRUSH" and layer == "COLLISION":
            self.map_canvas.create_rectangle(x*ts+1, y*ts+1, (x+1)*ts-1, (y+1)*ts-1,
                                             outline="#ff595e", width=2, dash=(3,2), tags=("brush_preview",))
        self.map_canvas.tag_raise("brush_preview")

    @staticmethod
    def _event_shift_down(event):
        # Tk utilise le bit 0 pour Shift sur Windows/Linux.
        return bool(getattr(event, "state", 0) & 0x0001)

    def _begin_click_tool_gesture(self, p):
        """Démarre l'ancien geste outil pour un clic ou Maj+glissé."""
        layer = self.active_layer.get()
        tool = self.current_tool.get()
        self.painting = True
        self.last_paint = None
        self.rect_start = p if tool == "RECT" else None
        self._gesture_snapshot_taken = False

        if layer in ("BG A", "BG B", "COLLISION") and tool in ("BRUSH", "ERASE", "FILL", "RECT"):
            self._begin_sparse_history()
            self._gesture_snapshot_taken = True
        elif layer == "OBJECTS" and tool in ("BRUSH", "ERASE"):
            self.push_objects_undo()
            self._gesture_snapshot_taken = True

        if tool != "RECT":
            self._apply_tool(*p)

    def map_mouse_down(self,event):
        p = self.canvas_cell(event)
        if not p:
            return

        # Ctrl+V : un clic pose le bloc fantôme.
        if self.paste_mode and self.tile_clipboard:
            self.paste_clipboard_at(*p)
            return

        layer = self.active_layer.get()
        tool = self.current_tool.get()

        # Sur les plans graphiques, le geste naturel devient celui d'un bureau :
        # clic = outil courant ; clic-glissé = sélection. Le Rectangle garde son
        # propre glissé explicite. Maj+glissé conserve le pinceau/gomme continu.
        if layer in ("BG A", "BG B") and tool in ("BRUSH", "ERASE", "PICK", "FILL"):
            self.painting = False
            self.last_paint = None
            self.rect_start = None
            self.selection_anchor = p
            self.selection_dragging = False
            self._direct_drag_candidate = True
            self._direct_drag_start_xy = (int(event.x), int(event.y))
            if self._event_shift_down(event) and tool in ("BRUSH", "ERASE"):
                self._direct_drag_candidate = False
                self.selection_anchor = None
                self._begin_click_tool_gesture(p)
            return

        # Les autres calques et l'outil Rectangle conservent leur comportement normal.
        self._begin_click_tool_gesture(p)

    def map_mouse_drag(self,event):
        p = self.canvas_cell(event)
        self._cursor_cell = p

        # Maj+glissé sur BG A/B = peinture/gomme continue.
        if self.painting:
            if p and self.current_tool.get() in ("BRUSH", "ERASE"):
                self._apply_tool(*p)
            self.refresh_brush_preview()
            return

        # Sans mode à choisir : dès qu'un clic devient réellement un glissé,
        # il se transforme en sélection rectangulaire façon bureau.
        if self._direct_drag_candidate and self.selection_anchor:
            sx, sy = self._direct_drag_start_xy or (int(event.x), int(event.y))
            moved_px = max(abs(int(event.x) - sx), abs(int(event.y) - sy))
            if p and (p != self.selection_anchor or moved_px >= self._direct_drag_threshold_px):
                self._direct_drag_candidate = False
                self.selection_dragging = True
                self.map_selection = self._normalized_selection(self.selection_anchor, p)
                self.map_selection_layer = self.active_layer.get()
                self._draw_selection_overlay()
                w, h = self._selection_size()
                self.status.configure(text=f"Sélection {w}×{h} • {w*h} tiles - relâche puis Ctrl+C.")
                self.refresh_brush_preview()
            return

        if self.selection_dragging and self.selection_anchor:
            if p:
                self.map_selection = self._normalized_selection(self.selection_anchor, p)
                self._draw_selection_overlay()
                w, h = self._selection_size()
                self.status.configure(text=f"Sélection {w}×{h} • {w*h} tiles - relâche puis Ctrl+C.")
            self.refresh_brush_preview()

    def map_mouse_up(self,event):
        p = self.canvas_cell(event)

        if self.selection_dragging:
            if p and self.selection_anchor:
                self.map_selection = self._normalized_selection(self.selection_anchor, p)
            self.selection_dragging = False
            self.selection_anchor = None
            self._direct_drag_candidate = False
            self._direct_drag_start_xy = None
            self._draw_selection_overlay()
            w, h = self._selection_size()
            if w and h:
                self.status.configure(text=f"Sélection prête : {w}×{h} • {w*h} tiles - Ctrl+C puis Ctrl+V.")
            self.refresh_brush_preview()
            return

        # Aucun glissé : c'était un clic normal avec l'outil courant.
        if self._direct_drag_candidate:
            anchor = self.selection_anchor
            self._direct_drag_candidate = False
            self._direct_drag_start_xy = None
            self.selection_anchor = None
            if anchor:
                self.clear_map_selection(silent=True)
                self._begin_click_tool_gesture(anchor)
                self.painting = False
                self.last_paint = None
                self.rect_start = None
                self._commit_sparse_history()
                self.request_stats_refresh()
                self.refresh_brush_preview()
            return

        if self.painting and self.current_tool.get() == "RECT" and self.rect_start:
            if p:
                self.paint_rectangle(self.rect_start, p)
        self.painting = False
        self.last_paint = None
        self.rect_start = None
        self._commit_sparse_history()
        self.request_stats_refresh()

    def map_right_click(self,event):
        p=self.canvas_cell(event)
        if p:
            self.inspect_cell(*p)

    def brush_cell(self):
        layer=self.active_layer.get()
        code=expected_priority_code(layer,self.depth_cb.get()=="Devant sprites")
        return Cell(
            tile_id=self.selected_tile_id,
            palette=int(self.palette_var.get()),
            flip_x=bool(self.flip_x_var.get()),
            flip_y=bool(self.flip_y_var.get()),
            priority_code=code
        )

    def _apply_tool(self,x,y):
        tool=self.current_tool.get()
        layer=self.active_layer.get()

        if tool=="SELECT":
            self.inspect_cell(x,y)
            return

        if layer=="OBJECTS":
            if tool=="BRUSH":
                self.place_object(x,y)
            elif tool=="ERASE":
                self.remove_object_at(x,y)
            self._redraw_cell(x,y)
            return

        if layer=="COLLISION":
            if tool in ("ERASE", "BRUSH", "FILL"):
                self._record_cell_before(layer, x, y)
            if tool=="ERASE":
                self.state.collisions[y][x]="NONE"
            elif tool in ("BRUSH","FILL"):
                self.state.collisions[y][x]=self.cell_collision_var.get() if self.cell_collision_var.get()!="NONE" else "SOLID"
            elif tool=="PICK":
                self.cell_collision_var.set(self.state.collisions[y][x])
            self._redraw_cell(x,y)
            return

        if layer=="EVENTS":
            self.inspect_cell(x,y)
            return

        if layer not in ("BG A","BG B"):
            return

        grid=self.state.bg_a if layer=="BG A" else self.state.bg_b

        if self.last_paint==(x,y) and tool in ("BRUSH","ERASE"):
            return
        self.last_paint=(x,y)

        if tool=="BRUSH":
            if self.selected_tile_id<0:
                self.status.configure(text="Sélectionne une tile.")
                return
            self._record_cell_before(layer, x, y)
            grid[y][x]=self.brush_cell()

        elif tool=="ERASE":
            self._record_cell_before(layer, x, y)
            code=0 if layer=="BG A" else 2
            grid[y][x]=Cell(priority_code=code)

        elif tool=="PICK":
            c=grid[y][x]
            if c.tile_id>=0:
                self.selected_tile_id=c.tile_id
                self.palette_var.set(c.palette)
                self.flip_x_var.set(c.flip_x)
                self.flip_y_var.set(c.flip_y)
                front=c.priority_code in (1,3)
                self.depth_cb.set("Devant sprites" if front else "Derrière sprites")
                self._update_priority_ui()
                self.current_tool.set("BRUSH")
                self.redraw_tileset()
                self.redraw_selected_tile_preview()

        elif tool=="FILL":
            if self.selected_tile_id<0:
                return
            self.status.configure(text="Remplissage…")
            self.update_idletasks()
            changed=flood_fill_cells(
                grid, x, y, self.brush_cell(),
                before_change=lambda cx, cy: self._record_cell_before(layer, cx, cy)
            )
            self.status.configure(text=f"Remplissage terminé : {changed} cellules.")
            self.painting=False
            self.redraw_map(force=True)
            return

        if tool in ("BRUSH", "ERASE"):
            self._redraw_cell(x,y)

    def paint_rectangle(self,a,b):
        layer=self.active_layer.get()
        if layer not in ("BG A","BG B") or self.selected_tile_id<0:
            return
        grid=self.state.bg_a if layer=="BG A" else self.state.bg_b
        x0,x1=sorted((a[0],b[0]))
        y0,y1=sorted((a[1],b[1]))
        c=self.brush_cell()
        for y in range(y0,y1+1):
            for x in range(x0,x1+1):
                self._record_cell_before(layer, x, y)
                grid[y][x]=deepcopy(c)
        self.redraw_map(force=True)
        self.status.configure(text=f"Rectangle : {(x1-x0+1)*(y1-y0+1)} cellules.")

    # ------------------------- INSPECT / COLLISION / EVENTS -------------------------

    def inspect_cell(self,x,y):
        self.selected_cell=(x,y)
        layer=self.active_layer.get()
        self.cell_pos_var.set(f"{layer} • x{x} y{y}")

        if layer in ("BG A","BG B"):
            c=(self.state.bg_a if layer=="BG A" else self.state.bg_b)[y][x]
            text=(
                f"Tile : {c.tile_id}\n"
                f"Palette : P{c.palette}\n"
                f"Flip X : {c.flip_x}\n"
                f"Flip Y : {c.flip_y}\n"
                f"Priorité code : {c.priority_code}\n"
                f"Signification : {PRIORITY_CODES.get(c.priority_code,'?')}\n\n"
                f"Collision : {self.state.collisions[y][x]}\n"
                f"Event : {self.state.events[y][x].enabled}"
            )
        else:
            text=f"Collision : {self.state.collisions[y][x]}\nEvent : {asdict(self.state.events[y][x])}"
        self._set_text(self.cell_detail,text)
        self.cell_collision_var.set(self.state.collisions[y][x])

        e=self.state.events[y][x]
        self.event_enabled_var.set(e.enabled)
        self.event_trigger_var.set(e.trigger if e.trigger in TRIGGER_TYPES else "CUSTOM")
        self.event_action_var.set(e.action if e.action in ACTION_TYPES else "CUSTOM")
        self.event_a_var.set(e.param_a)
        self.event_b_var.set(e.param_b)
        self.event_once_var.set(e.once)
        self.event_note_var.set(e.note)

    def apply_collision_to_selected(self):
        if not self.selected_cell:
            return
        x,y=self.selected_cell
        self._begin_sparse_history(); self._record_cell_before("COLLISION",x,y)
        self.state.collisions[y][x]=self.cell_collision_var.get()
        self._commit_sparse_history()
        self.inspect_cell(x,y); self.refresh_stats(); self._redraw_cell(x,y)

    def apply_event_to_selected(self):
        if not self.selected_cell:
            messagebox.showinfo("Event","Inspecte d'abord une cellule.")
            return
        x,y=self.selected_cell
        self._begin_sparse_history(); self._record_cell_before("EVENTS",x,y)
        self.state.events[y][x]=EventDef(
            enabled=bool(self.event_enabled_var.get()),
            trigger=self.event_trigger_var.get(),
            action=self.event_action_var.get(),
            param_a=self.event_a_var.get().strip(),
            param_b=self.event_b_var.get().strip(),
            once=bool(self.event_once_var.get()),
            note=self.event_note_var.get().strip()
        )
        self._commit_sparse_history()
        self.inspect_cell(x,y); self.refresh_stats(); self._redraw_cell(x,y)

    def clear_event_selected(self):
        if not self.selected_cell:
            return
        x,y=self.selected_cell
        self._begin_sparse_history(); self._record_cell_before("EVENTS",x,y)
        self.state.events[y][x]=EventDef()
        self._commit_sparse_history()
        self.inspect_cell(x,y); self.refresh_stats(); self._redraw_cell(x,y)

    # ------------------------- OBJECTS -------------------------

    def place_object_mode(self):
        if self.selected_tile_id<0:
            messagebox.showinfo("Objet","Sélectionne une tile.")
            return
        self.active_layer.set("OBJECTS")
        self.current_tool.set("BRUSH")
        self._update_priority_ui()

    def place_object(self,x,y):
        # Un objet max par cellule et par geste pour éviter le drag-spam.
        if self._objects_by_cell.get((x,y)):
            return
        o=ObjectDef(
            object_id=self.state.next_object_id,
            name=f"Object_{self.state.next_object_id}",
            tile_id=self.selected_tile_id,
            x=x,y=y,palette=int(self.palette_var.get()),
            flip_x=bool(self.flip_x_var.get()),flip_y=bool(self.flip_y_var.get())
        )
        self.state.objects.append(o)
        self.state.next_object_id+=1
        self._objects_by_cell.setdefault((x,y), []).append(o)
        if hasattr(self, "object_tree"):
            self.object_tree.insert(
                "","end",iid=str(o.object_id),text=o.name,
                values=(f"{o.x},{o.y}",o.tile_id,f"P{o.palette}")
            )

    def remove_object_at(self,x,y):
        removed = list(self._objects_by_cell.get((x,y), []))
        if not removed:
            return
        ids = {o.object_id for o in removed}
        self.state.objects=[o for o in self.state.objects if o.object_id not in ids]
        self._objects_by_cell.pop((x,y), None)
        if hasattr(self, "object_tree"):
            for oid in ids:
                if self.object_tree.exists(str(oid)):
                    self.object_tree.delete(str(oid))

    def refresh_objects(self):
        self._objects_by_cell = {}
        for o in self.state.objects:
            self._objects_by_cell.setdefault((o.x,o.y), []).append(o)
        if not hasattr(self, "object_tree"):
            return
        for item in self.object_tree.get_children():
            self.object_tree.delete(item)
        for o in self.state.objects:
            self.object_tree.insert(
                "","end",iid=str(o.object_id),text=o.name,
                values=(f"{o.x},{o.y}",o.tile_id,f"P{o.palette}")
            )

    def selected_object(self):
        sel=self.object_tree.selection()
        if not sel:
            return None
        oid=int(sel[0])
        return next((o for o in self.state.objects if o.object_id==oid),None)

    def on_object_select(self):
        o=self.selected_object()
        if not o:
            return
        old = self.selected_cell
        self.selected_object_id = o.object_id
        self.selected_cell = (o.x, o.y)
        self.goto_x_var.set(o.x); self.goto_y_var.set(o.y)
        self._set_text(self.cell_detail,json.dumps(asdict(o),indent=2,ensure_ascii=False))
        if old:
            self._redraw_cell(*old)
        self._redraw_cell(o.x, o.y)
        self.status.configure(text=f"Objet #{o.object_id} • {o.name} • x{o.x} y{o.y} • tile #{o.tile_id} • P{o.palette}")

    def edit_selected_object(self):
        o=self.selected_object()
        if not o:
            return
        d=tk.Toplevel(self)
        d.title(f"Objet #{o.object_id}")
        d.transient(self)
        d.grab_set()
        frm=ttk.Frame(d,padding=14)
        frm.pack()

        name=tk.StringVar(value=o.name)
        pal=tk.IntVar(value=o.palette)
        fx=tk.BooleanVar(value=o.flip_x)
        fy=tk.BooleanVar(value=o.flip_y)
        ena=tk.BooleanVar(value=o.event.enabled)
        trig=tk.StringVar(value=o.event.trigger)
        act=tk.StringVar(value=o.event.action)
        pa=tk.StringVar(value=o.event.param_a)
        pb=tk.StringVar(value=o.event.param_b)
        once=tk.BooleanVar(value=o.event.once)
        note=tk.StringVar(value=o.note)

        rows=[
            ("Nom",ttk.Entry(frm,textvariable=name)),
            ("Palette",ttk.Combobox(frm,textvariable=pal,
                values=list(range(DMS_MODES[self.mode_var.get()]["palettes"])),state="readonly")),
        ]
        for r,(lab,wid) in enumerate(rows):
            ttk.Label(frm,text=lab).grid(row=r,column=0,sticky="w",pady=4)
            wid.grid(row=r,column=1,sticky="ew",pady=4)
        ttk.Checkbutton(frm,text="Flip X",variable=fx).grid(row=2,column=0,sticky="w")
        ttk.Checkbutton(frm,text="Flip Y",variable=fy).grid(row=2,column=1,sticky="w")
        ttk.Checkbutton(frm,text="Event actif",variable=ena).grid(row=3,column=0,columnspan=2,sticky="w",pady=(8,0))
        ttk.Label(frm,text="Trigger").grid(row=4,column=0,sticky="w")
        ttk.Combobox(frm,textvariable=trig,values=TRIGGER_TYPES,state="readonly").grid(row=4,column=1,sticky="ew")
        ttk.Label(frm,text="Action").grid(row=5,column=0,sticky="w")
        ttk.Combobox(frm,textvariable=act,values=ACTION_TYPES,state="readonly").grid(row=5,column=1,sticky="ew")
        ttk.Label(frm,text="Param A").grid(row=6,column=0,sticky="w")
        ttk.Entry(frm,textvariable=pa).grid(row=6,column=1,sticky="ew")
        ttk.Label(frm,text="Param B").grid(row=7,column=0,sticky="w")
        ttk.Entry(frm,textvariable=pb).grid(row=7,column=1,sticky="ew")
        ttk.Checkbutton(frm,text="Once",variable=once).grid(row=8,column=0,columnspan=2,sticky="w")
        ttk.Label(frm,text="Note").grid(row=9,column=0,sticky="w")
        ttk.Entry(frm,textvariable=note).grid(row=9,column=1,sticky="ew")

        def apply():
            self.push_objects_undo()
            o.name=name.get().strip() or o.name
            o.palette=int(pal.get())
            o.flip_x=bool(fx.get())
            o.flip_y=bool(fy.get())
            o.event=EventDef(
                enabled=bool(ena.get()),trigger=trig.get(),action=act.get(),
                param_a=pa.get().strip(),param_b=pb.get().strip(),
                once=bool(once.get())
            )
            o.note=note.get().strip()
            d.destroy()
            self.refresh_objects()
            self.refresh_stats()
            self.redraw_map()

        ttk.Button(frm,text="Appliquer",command=apply,
                   style="Accent.TButton").grid(row=10,column=0,columnspan=2,sticky="ew",pady=(10,0))
        frm.columnconfigure(1,weight=1)

    def delete_selected_object(self):
        o=self.selected_object()
        if not o:
            return
        self.push_objects_undo()
        self.state.objects=[x for x in self.state.objects if x.object_id!=o.object_id]
        self.refresh_objects()
        self.refresh_stats()
        self.redraw_map()

    # ------------------------- STATS -------------------------

    def request_stats_refresh(self):
        if self._stats_after_id is not None:
            try:
                self.after_cancel(self._stats_after_id)
            except Exception:
                pass
        self._stats_after_id = self.after(180, self._run_stats_refresh)

    def _run_stats_refresh(self):
        self._stats_after_id = None
        self.refresh_stats()

    def refresh_stats(self):
        a=sum(1 for row in self.state.bg_a for c in row if c.tile_id>=0)
        b=sum(1 for row in self.state.bg_b for c in row if c.tile_id>=0)
        af=sum(1 for row in self.state.bg_a for c in row if c.tile_id>=0 and c.priority_code==1)
        bf=sum(1 for row in self.state.bg_b for c in row if c.tile_id>=0 and c.priority_code==3)
        col=sum(1 for row in self.state.collisions for c in row if c!="NONE")
        ev=sum(1 for row in self.state.events for e in row if e.enabled)
        self.stats_var.set(
            f"BG A : {a} cells ({af} devant sprites)\n"
            f"BG B : {b} cells ({bf} devant sprites)\n"
            f"Objects : {len(self.state.objects)}\n"
            f"Collisions : {col}\nEvents : {ev}"
        )

    # ------------------------- SAVE / LOAD / EXPORT -------------------------

    def tileset_info(self,project_path=None):
        p=self.tileset.path
        if p and project_path:
            try: p=os.path.relpath(p,Path(project_path).parent)
            except Exception: pass
        return {
            "path":p,"tile_size":self.tileset.tile_size,
            "margin":self.tileset.margin,"spacing":self.tileset.spacing,
            "tile_count":self.tileset.next_tile_id,
            "source_kind":self.tileset.source_kind,
            "palette_ids":list(self.tileset.palette_ids),
            "library_version":1,
            "sources":self.tileset.source_metadata(project_path),
        }

    def save_project(self):
        self.state.name=self.name_var.get().strip() or "DMS_MAP"
        self.state.mode=self.mode_var.get()
        path=self.project_path
        if not path:
            path=filedialog.asksaveasfilename(
                title="Sauver projet",defaultextension=".dmapproj",
                initialfile=self.state.name+".dmapproj",
                filetypes=[("DMS Map Project","*.dmapproj")]
            )
        if not path: return
        try:
            target=Path(path); tmp=target.with_name(target.name+".tmp")
            tmp.write_text(json.dumps(map_to_project_dict(self.state,self.tileset_info(path)),indent=2,ensure_ascii=False),encoding="utf-8")
            os.replace(tmp,target)
            self.project_path=path
            self.status.configure(text=f"Projet sauvé : {Path(path).name} • {len(self.tileset.sources)} source(s) de tiles")
        except Exception as e:
            messagebox.showerror("Sauvegarde",str(e))

    def _resolve_source_path(self, ref, base_path):
        pp=Path(ref)
        if not pp.is_absolute(): pp=Path(base_path).parent/pp
        return pp

    def _load_tileset_reference(self, ref, base_path, info=None):
        info=info or {}
        if not ref: return False
        pp=self._resolve_source_path(ref,base_path)
        if not pp.exists(): return False
        if pp.suffix.lower()==".dimg":
            self.tileset.load_dimg(str(pp),self._palette_limit())
            self.ts_tile_var.set(self.tileset.tile_size)
        else:
            self.ts_tile_var.set(int(info.get("tile_size",self.state.tile_size)))
            self.ts_margin_var.set(int(info.get("margin",0))); self.ts_spacing_var.set(int(info.get("spacing",0)))
            self.tileset.load(str(pp),int(info.get("tile_size",self.state.tile_size)),int(info.get("margin",0)),int(info.get("spacing",0)))
        self._refresh_tileset_source_ui()
        return True

    def _load_tileset_info(self, info, base_path):
        """Charge V0.6 multi-source ou l'ancien champ tileset mono-source."""
        info=info or {}
        sources=info.get("sources") or []
        if not sources:
            ref=info.get("path","")
            return self._load_tileset_reference(ref,base_path,info) if ref else False
        self.tileset.clear()
        # Tous les IDs sauvés sont réservés avant de relire les fichiers. Si une
        # source a grandi depuis la dernière sauvegarde, ses nouvelles tiles sont
        # allouées APRES les IDs des autres sources au lieu de les écraser.
        saved_ids=[int(x) for sd in sources for x in (sd.get("tile_ids") or [])]
        if saved_ids:
            self.tileset.next_tile_id=max(saved_ids)+1
        missing=[]; palette_notes=[]
        for sd in sources:
            ref=sd.get("path","")
            if not ref: continue
            pp=self._resolve_source_path(ref,base_path)
            if not pp.exists():
                missing.append(str(pp)); continue
            kind=str(sd.get("source_kind",pp.suffix.lower().lstrip("."))).upper()
            if kind=="DIMG" or pp.suffix.lower()==".dimg":
                saved_source_ids=[int(x) for x in (sd.get("tile_ids") or [])]
                probe=PNGTileset(self); probe.load_dimg(str(pp))
                if len(probe.tiles_base) < len(saved_source_ids):
                    removed=set(saved_source_ids[len(probe.tiles_base):])
                    used=0
                    for grid in (self.state.bg_a,self.state.bg_b):
                        for row in grid:
                            used += sum(1 for c in row if c.tile_id in removed)
                    used += sum(1 for o in self.state.objects if o.tile_id in removed)
                    if used:
                        raise ValueError(
                            f"Tileset raccourci : {pp.name} a perdu {len(saved_source_ids)-len(probe.tiles_base)} tile(s), "
                            f"mais {used} placement(s) utilisent encore leurs IDs. Ouverture refusée pour éviter une disparition silencieuse."
                        )
                rec,notes=self.tileset.add_dimg_with_ids(
                    str(pp),saved_source_ids,sd.get("palette_map",{}),self._palette_limit(),
                    uid=sd.get("uid"),name=sd.get("name")
                )
                palette_notes.extend(notes)
            else:
                if self.tileset.sources:
                    raise ValueError("Un ancien projet mélange PNG brut et plusieurs sources. Convertis le PNG en DIMG.")
                self.tileset.load(str(pp),int(sd.get("tile_size",8)),int(sd.get("margin",0)),int(sd.get("spacing",0)))
        self._refresh_tileset_source_ui()
        if missing:
            messagebox.showwarning("Tilesets manquants","Certaines sources du projet sont introuvables :\n\n"+"\n".join(missing))
        if palette_notes:
            messagebox.showwarning("Palettes remappées","Des sources ont dû être remappées à l'ouverture :\n"+"\n".join(palette_notes))
        return bool(self.tileset.sources)

    def _prepare_export_tileset(self, dmap_path):
        used=set()
        for grid in (self.state.bg_a,self.state.bg_b):
            for row in grid:
                used.update(c.tile_id for c in row if c.tile_id >= 0)
        used.update(o.tile_id for o in self.state.objects if o.tile_id >= 0)
        missing=sorted(tid for tid in used if tid not in self.tileset.global_to_local)
        if missing:
            sample=", ".join(map(str,missing[:16]))
            raise ValueError(f"Export refusé : {len(missing)} ID(s) de tile placé(s) n'ont plus de source graphique ({sample}).")
        if used and not self.tileset.sources:
            raise ValueError("Export refusé : la map contient des tiles mais aucun tileset n'est chargé.")
        info=self.tileset_info(dmap_path)
        if len(self.tileset.sources) <= 1:
            return info,None
        # Le runtime actuel consomme un seul DIMG pour le TILESET d'une map :
        # la bibliothèque de travail est donc consolidée automatiquement sans
        # modifier les IDs utilisés par les cellules.
        out=Path(dmap_path).with_name(Path(dmap_path).stem+"_tiles.dimg")
        self.tileset.export_consolidated_dimg(out,self.mode_var.get())
        info["path"]=out.name
        info["source_kind"]="DIMG"
        info["consolidated_from_library"]=True
        info["consolidated_file"]=out.name
        return info,str(out)

    def _open_runtime_dmap(self, path):
        with zipfile.ZipFile(path, "r") as z:
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
            if manifest.get("format") != "DMAP":
                raise ValueError("Le fichier n'est pas un DMAP.")
            md = manifest.get("map", {})
            layers = manifest.get("layers", {})
            width = max(1, int(md.get("width_cells", md.get("width", 40))))
            height = max(1, int(md.get("height_cells", md.get("height", 28))))
            mode = md.get("mode", "Mode 0 - STANDARD")
            if mode not in DMS_MODES:
                mode = "Mode 0 - STANDARD"
            st = MapState(
                name=md.get("name", Path(path).stem.upper()),
                width=width, height=height,
                tile_size=int(md.get("tile_size", 8)),
                mode=mode, note=md.get("note", "")
            )

            raw_a = layers.get("BG_A", layers.get("BG A", []))
            raw_b = layers.get("BG_B", layers.get("BG B", []))
            st.bg_a = [[dict_to_cell(c, "BG A") for c in row] for row in raw_a] if raw_a else []
            st.bg_b = [[dict_to_cell(c, "BG B") for c in row] for row in raw_b] if raw_b else []
            st.collisions = layers.get("COLLISION", [])
            raw_events = layers.get("EVENTS", [])
            st.events = [[EventDef(**e) for e in row] for row in raw_events] if raw_events else []

            objects = manifest.get("objects")
            if objects is None and "objects.json" in z.namelist():
                objects = json.loads(z.read("objects.json").decode("utf-8"))
            st.objects = []
            for od in objects or []:
                ev = EventDef(**od.get("event", {}))
                st.objects.append(ObjectDef(
                    object_id=int(od.get("object_id", 0)), name=od.get("name", "Object"),
                    tile_id=int(od.get("tile_id", -1)), x=int(od.get("x", 0)), y=int(od.get("y", 0)),
                    palette=int(od.get("palette", 0)), flip_x=bool(od.get("flip_x", False)),
                    flip_y=bool(od.get("flip_y", False)), event=ev, note=od.get("note", "")
                ))
            st.next_object_id = max([o.object_id for o in st.objects] + [0]) + 1
            st.init_grids()

            # Réparation bornée identique aux projets, utile pour les DMAP plus anciens/partiels.
            def repair_cells(grid, default_code):
                out = [[Cell(priority_code=default_code) for _ in range(width)] for __ in range(height)]
                for yy in range(min(height, len(grid))):
                    for xx in range(min(width, len(grid[yy]))):
                        out[yy][xx] = grid[yy][xx]
                return out
            st.bg_a = repair_cells(st.bg_a, 0)
            st.bg_b = repair_cells(st.bg_b, 2)

            if not st.collisions:
                st.collisions = [["NONE" for _ in range(width)] for __ in range(height)]
                if "collision.bin" in z.namelist():
                    raw = z.read("collision.bin")
                    for i, v in enumerate(raw[:width*height]):
                        yy, xx = divmod(i, width)
                        st.collisions[yy][xx] = COLLISION_TYPES[v] if v < len(COLLISION_TYPES) else "CUSTOM"
            else:
                col = [["NONE" for _ in range(width)] for __ in range(height)]
                for yy in range(min(height, len(st.collisions))):
                    for xx in range(min(width, len(st.collisions[yy]))):
                        col[yy][xx] = st.collisions[yy][xx]
                st.collisions = col

            if not st.events:
                st.events = [[EventDef() for _ in range(width)] for __ in range(height)]
                if "events.json" in z.namelist():
                    sparse = json.loads(z.read("events.json").decode("utf-8"))
                    for e in sparse:
                        xx, yy = int(e.get("x", -1)), int(e.get("y", -1))
                        if 0 <= xx < width and 0 <= yy < height:
                            vals = {k:v for k,v in e.items() if k not in ("x","y")}
                            st.events[yy][xx] = EventDef(**vals)
            else:
                evg = [[EventDef() for _ in range(width)] for __ in range(height)]
                for yy in range(min(height, len(st.events))):
                    for xx in range(min(width, len(st.events[yy]))):
                        evg[yy][xx] = st.events[yy][xx]
                st.events = evg

            self.state = st
            ti = manifest.get("tileset", {})
            ref = ti.get("path") or ti.get("source") or ""
            loaded = self._load_tileset_reference(ref, path, ti)

        self.project_path = None  # Runtime ouvert : un Save créera un projet éditable.
        self.undo_stack.clear(); self.redo_stack.clear()
        self.clear_map_selection(silent=True)
        self.tile_clipboard = None
        self.paste_mode = False
        self._sync_vars_from_state()
        self.refresh_objects(); self.refresh_stats()
        self.selected_tile_id = min(self.tileset.global_to_local) if self.tileset.global_to_local else -1
        self.redraw_palette_ui(); self.redraw_tileset(force=True); self.redraw_selected_tile_preview(); self.redraw_map(force=True)
        if loaded:
            self.tile_info.configure(text=f"{Path(self.tileset.path).name} • {len(self.tileset.tiles_base)} tiles • {self.tileset.source_kind}")
        else:
            self.tile_info.configure(text="DMAP ouvert • ressource graphique associée introuvable")
        self.status.configure(text=f"DMAP runtime ouvert : {Path(path).name}. Sauve en .dmapproj pour continuer l'édition.")

    def open_project(self):
        path=filedialog.askopenfilename(
            title="Ouvrir map DMS",
            filetypes=[("Projet / runtime DMS","*.dmapproj *.dmap"), ("DMS Map Project","*.dmapproj"),
                       ("DMS Runtime Map","*.dmap"), ("JSON","*.json")]
        )
        if not path:
            return
        try:
            if Path(path).suffix.lower() == ".dmap":
                self._open_runtime_dmap(path)
                return
            data=json.loads(Path(path).read_text(encoding="utf-8"))
            if data.get("format")!="DMS_MAP_PROJECT":
                raise ValueError("Format de projet invalide.")
            self.state=project_dict_to_state(data)
            ti=data.get("tileset",{})
            loaded=self._load_tileset_info(ti,path)
            self.project_path=path
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.clear_map_selection(silent=True)
            self.tile_clipboard = None
            self.paste_mode = False
            self._sync_vars_from_state()
            self.refresh_objects()
            self.refresh_stats()
            self.selected_tile_id = min(self.tileset.global_to_local) if self.tileset.global_to_local else -1
            self.redraw_palette_ui()
            self.redraw_tileset(force=True)
            self.redraw_selected_tile_preview()
            self.redraw_map(force=True)
            if loaded:
                self.tile_info.configure(text=f"{Path(self.tileset.path).name} • {len(self.tileset.tiles_base)} tiles • {self.tileset.source_kind}")
            self.status.configure(text=f"Projet ouvert : {Path(path).name}")
        except Exception as e:
            messagebox.showerror("Ouverture",str(e))

    def export_map(self):
        self.state.name=self.name_var.get().strip() or "DMS_MAP"
        self.state.mode=self.mode_var.get()
        path=filedialog.asksaveasfilename(
            title="Exporter DMAP V2",defaultextension=".dmap",
            initialfile=self.state.name+".dmap",filetypes=[("DMS Map","*.dmap")]
        )
        if not path: return None
        try:
            ti,generated=self._prepare_export_tileset(path)
            export_dmap(path,self.state,ti)
            extra=f" + {Path(generated).name}" if generated else ""
            self.status.configure(text=f"DMAP V2 exporté : {Path(path).name}{extra}")
            messagebox.showinfo(
                "Export DMAP",
                "DMAP V2 exporté." + (f"\nTileset consolidé : {Path(generated).name}" if generated else "") +
                "\n\nPriorités 0/1/2/3 explicites pour le GDK."
            )
            return path
        except Exception as e:
            messagebox.showerror("Export",str(e)); return None

    def export_bundle(self):
        folder=filedialog.askdirectory(title="Bundle DMS-GDK")
        if not folder: return
        self.state.name=self.name_var.get().strip() or "DMS_MAP"
        self.state.mode=self.mode_var.get()
        safe="".join(c if c.isalnum() else "_" for c in self.state.name.upper()).strip("_") or "DMS_MAP"
        final_dmap=Path(folder)/f"{safe.lower()}.dmap"
        temp=Path(folder)/"_temp.dmap"
        try:
            ti=self.tileset_info(str(final_dmap)); generated=None
            if len(self.tileset.sources)>1:
                tiles_path=Path(folder)/f"{safe.lower()}_tiles.dimg"
                self.tileset.export_consolidated_dimg(tiles_path,self.mode_var.get())
                ti["path"]=tiles_path.name; ti["source_kind"]="DIMG"
                ti["consolidated_from_library"]=True; ti["consolidated_file"]=tiles_path.name
                generated=str(tiles_path)
            export_dmap(str(temp),self.state,ti)
            export_gdk_bundle(folder,temp,self.state)
            try: temp.unlink()
            except Exception: pass
            self.status.configure(text="Bundle DMS-GDK exporté" + (f" • {Path(generated).name}" if generated else ""))
            messagebox.showinfo("Bundle","Exporté : .dmap + .h + rapport" + (" + tileset consolidé .dimg" if generated else "") + ".")
        except Exception as e:
            messagebox.showerror("Bundle",str(e))

    def _set_text(self,widget,text):
        widget.configure(state="normal")
        widget.delete("1.0","end")
        widget.insert("1.0",text)
        widget.configure(state="disabled")


if __name__ == "__main__":
    DMSMapBuilder().mainloop()
