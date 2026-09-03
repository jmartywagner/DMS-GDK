from __future__ import annotations

import json
import math
import os
import struct
import zlib
import zipfile
import threading
import queue
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


APP_NAME = "DMS Image Converter"
APP_VERSION = "0.2.4"

DMS_MODES = {
    "Mode 0 - STANDARD": {
        "id": 0, "w": 320, "h": 224, "palettes": 4, "bg_a": True, "bg_b": True,
        "description": "320×224 • 4 palettes ×16 • BG A + BG B"
    },
    "Mode 1 - HIGH COLOR": {
        "id": 1, "w": 320, "h": 224, "palettes": 8, "bg_a": True, "bg_b": False,
        "description": "320×224 • 8 palettes ×16 • BG A"
    },
    "Mode 2 - SCROLL": {
        "id": 2, "w": 320, "h": 224, "palettes": 4, "bg_a": True, "bg_b": True,
        "description": "320×224 • 4 palettes ×16 • BG A + BG B / line-scroll"
    },
    "Mode 3 - SPRITE": {
        "id": 3, "w": 320, "h": 224, "palettes": 4, "bg_a": True, "bg_b": False,
        "description": "320×224 • 4 palettes ×16 • BG A"
    },
    "Mode 4 - LOW RES": {
        "id": 4, "w": 256, "h": 224, "palettes": 8, "bg_a": True, "bg_b": True,
        "description": "256×224 natif • 8 palettes ×16 • BG A + BG B"
    },
}

TILESET_RAW_MODE = "TILESET RAW / NONE"
RESIZE_MODES = ["CROP", "FIT", "STRETCH", TILESET_RAW_MODE]
FILTER_MODES = ["BOX / PHOTO", "NEAREST / PIXEL"]
DITHER_MODES = ["OFF", "ORDERED 2×2", "ORDERED 4×4"]
TILE_SIZES = [8, 16]


# ---------------------------------------------------------------------------
# PIXEL / COLOR
# ---------------------------------------------------------------------------

def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def rgb888_to_rgb333(rgb):
    r, g, b = rgb
    return (
        int(round(r * 7 / 255)),
        int(round(g * 7 / 255)),
        int(round(b * 7 / 255)),
    )


def rgb333_to_rgb888(c):
    return tuple(int(round(v * 255 / 7)) for v in c)


def rgb333_word(c):
    r, g, b = c
    return ((r & 7) << 6) | ((g & 7) << 3) | (b & 7)


def rgb333_hex(c):
    return f"{rgb333_word(c):03X}"


def dist_sq(a, b):
    return (a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2


def nearest_color(c, palette):
    if not palette:
        return (0, 0, 0)
    return min(palette, key=lambda p: dist_sq(c, p))


def luminance(c):
    return 0.2126*c[0] + 0.7152*c[1] + 0.0722*c[2]


# ---------------------------------------------------------------------------
# SIMPLE IMAGE IO
# ---------------------------------------------------------------------------

@dataclass
class ImageData:
    width: int
    height: int
    pixels: list
    source: str = ""
    alpha: list | None = None


def write_png_rgb(path, image: ImageData):
    """Pure stdlib PNG writer (RGB8, no alpha)."""
    w, h = image.width, image.height
    raw = bytearray()
    for row in image.pixels:
        raw.append(0)  # filter type 0
        for r, g, b in row:
            raw.extend((r & 255, g & 255, b & 255))

    def chunk(name, data):
        return (
            struct.pack(">I", len(data)) +
            name + data +
            struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
        )

    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    Path(path).write_bytes(bytes(png))


def _png_paeth(a,b,c):
    p=a+b-c; pa=abs(p-a); pb=abs(p-b); pc=abs(p-c)
    return a if pa<=pb and pa<=pc else (b if pb<=pc else c)


def _png_unfilter(dec,height,stride,bpp):
    rows=[]; curpos=0; prev=bytearray(stride)
    if len(dec) < height*(stride+1):
        raise ValueError("PNG tronqué")
    for _ in range(height):
        ft=dec[curpos]; curpos+=1
        cur=bytearray(dec[curpos:curpos+stride]); curpos+=stride
        for x in range(stride):
            a=cur[x-bpp] if x>=bpp else 0; b=prev[x]; c=prev[x-bpp] if x>=bpp else 0
            if ft==1: cur[x]=(cur[x]+a)&255
            elif ft==2: cur[x]=(cur[x]+b)&255
            elif ft==3: cur[x]=(cur[x]+((a+b)//2))&255
            elif ft==4: cur[x]=(cur[x]+_png_paeth(a,b,c))&255
            elif ft!=0: raise ValueError(f"Filtre PNG {ft} non supporté")
        rows.append(cur); prev=cur
    return rows


def _png_unpack_indices(row,width,bitdepth):
    if bitdepth==8: return list(row[:width])
    out=[]; mask=(1<<bitdepth)-1; per=8//bitdepth
    for byte in row:
        for slot in range(per):
            out.append((byte>>(8-bitdepth*(slot+1)))&mask)
            if len(out)>=width: return out
    return out


def read_png_image_data(path):
    data=Path(path).read_bytes(); sig=b"\x89PNG\r\n\x1a\n"
    if not data.startswith(sig): raise ValueError("Signature PNG invalide")
    pos=8; width=height=bitdepth=ctype=None; raw=bytearray(); plte=None; trns=None
    while pos+12<=len(data):
        n=struct.unpack_from(">I",data,pos)[0]; kind=data[pos+4:pos+8]; payload=data[pos+8:pos+8+n]; pos+=12+n
        if kind==b"IHDR":
            width,height,bitdepth,ctype,comp,filt,interlace=struct.unpack(">IIBBBBB",payload)
            if comp or filt or interlace: raise ValueError("PNG entrelacé non supporté")
            if ctype in (2,6) and bitdepth!=8: raise ValueError("PNG RGB/RGBA 8-bit requis")
            if ctype==3 and bitdepth not in (1,2,4,8): raise ValueError("PNG indexé 1/2/4/8-bit requis")
            if ctype not in (2,3,6): raise ValueError("PNG supporté : RGB, RGBA ou indexé")
        elif kind==b"PLTE": plte=[tuple(payload[i:i+3]) for i in range(0,len(payload),3)]
        elif kind==b"tRNS": trns=bytes(payload)
        elif kind==b"IDAT": raw.extend(payload)
        elif kind==b"IEND": break
    if width is None or not raw: raise ValueError("PNG incomplet")
    dec=zlib.decompress(bytes(raw)); rgb=[]; al=[]
    if ctype in (2,6):
        ch=3 if ctype==2 else 4; rows=_png_unfilter(dec,height,width*ch,ch)
        for row in rows:
            rr=[]; aa=[]
            for x in range(width):
                i=x*ch; rr.append(tuple(row[i:i+3])); aa.append(row[i+3] if ch==4 else 255)
            rgb.append(rr); al.append(aa)
    else:
        if not plte: raise ValueError("PNG indexé sans PLTE")
        rows=_png_unfilter(dec,height,(width*bitdepth+7)//8,1); atr=list(trns or b"")
        # PNG standard: without tRNS every palette entry is opaque. The optional
        # DMS index-0 colorkey is applied later, only by TILESET RAW.
        for row in rows:
            rr=[]; aa=[]
            for idx in _png_unpack_indices(row,width,bitdepth):
                if idx>=len(plte): raise ValueError("Index PNG hors palette")
                rr.append(plte[idx]); aa.append(atr[idx] if idx<len(atr) else 255)
            rgb.append(rr); al.append(aa)
    return ImageData(width,height,rgb,str(path),al)


class ImageLoader:
    """
    Loader PNG natif + Pillow optionnel.
    Les chemins PNG/Pillow sont sûrs hors thread Tk ; le fallback PhotoImage
    reste réservé au thread UI pour GIF/PPM/PGM quand Pillow est absent.
    """
    PILLOW_EXTS = {".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff", ".gif", ".ppm", ".pgm"}

    def __init__(self, master):
        self.master = master
        self.keepalive = []
        self.pillow = None
        try:
            from PIL import Image
            self.pillow = Image
        except Exception:
            self.pillow = None

    def can_load_offthread(self, path):
        ext = Path(path).suffix.lower()
        return ext == ".png" or (self.pillow is not None and ext in self.PILLOW_EXTS)

    def load(self, path, allow_tk=True):
        ext = Path(path).suffix.lower()
        if ext == ".png":
            return read_png_image_data(path)
        if self.pillow is not None and ext in self.PILLOW_EXTS:
            with self.pillow.open(path) as src:
                im = src.convert("RGBA")
                w, h = im.size
                raw = list(im.getdata())
            px = []
            al = []
            any_alpha = False
            for y in range(h):
                rr = []
                aa = []
                for r, g, b, a in raw[y*w:(y+1)*w]:
                    rr.append((r, g, b))
                    aa.append(a)
                    if a < 255:
                        any_alpha = True
                px.append(rr)
                al.append(aa)
            return ImageData(w, h, px, str(path), al if any_alpha else None)

        if not allow_tk:
            raise ValueError(f"Chargement hors thread Tk indisponible pour {ext or 'ce format'} sans Pillow.")

        img = tk.PhotoImage(master=self.master, file=str(path))
        self.keepalive.append(img)
        w, h = img.width(), img.height()
        px = []
        for y in range(h):
            row = []
            for x in range(w):
                c = img.get(x, y)
                if isinstance(c, str):
                    if c.startswith("#") and len(c) >= 7:
                        rgb = tuple(int(c[i:i+2], 16) for i in (1,3,5))
                    else:
                        vals = [int(v) for v in c.split()]
                        rgb = tuple(vals[:3])
                else:
                    rgb = tuple(int(v) for v in c[:3])
                row.append(rgb)
            px.append(row)
        return ImageData(w, h, px, str(path))


# ---------------------------------------------------------------------------
# RESIZE / CROP
# ---------------------------------------------------------------------------

def sample_nearest(image, sx, sy):
    x = clamp(int(round(sx)), 0, image.width-1)
    y = clamp(int(round(sy)), 0, image.height-1)
    return image.pixels[y][x]


def sample_nearest_alpha(image, sx, sy):
    if image.alpha is None:
        return 255
    x = clamp(int(round(sx)), 0, image.width-1)
    y = clamp(int(round(sy)), 0, image.height-1)
    return image.alpha[y][x]


def sample_box(image, x0, y0, x1, y1):
    # Fast-ish area average for reduction; caps sample density for very large sources.
    ix0 = clamp(int(math.floor(x0)), 0, image.width-1)
    iy0 = clamp(int(math.floor(y0)), 0, image.height-1)
    ix1 = clamp(int(math.ceil(x1)), ix0+1, image.width)
    iy1 = clamp(int(math.ceil(y1)), iy0+1, image.height)

    step_x = max(1, (ix1 - ix0) // 8)
    step_y = max(1, (iy1 - iy0) // 8)
    sr = sg = sb = n = 0
    for y in range(iy0, iy1, step_y):
        row = image.pixels[y]
        for x in range(ix0, ix1, step_x):
            r,g,b = row[x]
            sr += r; sg += g; sb += b; n += 1
    if not n:
        return sample_nearest(image, x0, y0)
    return (sr//n, sg//n, sb//n)


def sample_box_alpha(image, x0, y0, x1, y1):
    if image.alpha is None:
        return 255
    ix0 = clamp(int(math.floor(x0)), 0, image.width-1)
    iy0 = clamp(int(math.floor(y0)), 0, image.height-1)
    ix1 = clamp(int(math.ceil(x1)), ix0+1, image.width)
    iy1 = clamp(int(math.ceil(y1)), iy0+1, image.height)
    step_x = max(1, (ix1 - ix0) // 8)
    step_y = max(1, (iy1 - iy0) // 8)
    total = n = 0
    for y in range(iy0, iy1, step_y):
        row = image.alpha[y]
        for x in range(ix0, ix1, step_x):
            total += row[x]
            n += 1
    return total // n if n else sample_nearest_alpha(image, x0, y0)


def resize_image(image, tw, th, mode="CROP", filter_mode="BOX / PHOTO", matte=(0,0,0)):
    sw, sh = image.width, image.height
    has_alpha = image.alpha is not None

    def pick_alpha(sx0, sy0, sx1, sy1):
        if not has_alpha:
            return 255
        if filter_mode.startswith("NEAREST"):
            return sample_nearest_alpha(image, (sx0+sx1)/2, (sy0+sy1)/2)
        return sample_box_alpha(image, sx0, sy0, sx1, sy1)

    if mode == "STRETCH":
        scale_x = sw / tw
        scale_y = sh / th
        out = []
        out_alpha = [] if has_alpha else None
        for y in range(th):
            row = []
            arow = [] if has_alpha else None
            sy0 = y*scale_y
            sy1 = (y+1)*scale_y
            for x in range(tw):
                sx0 = x*scale_x
                sx1 = (x+1)*scale_x
                if filter_mode.startswith("NEAREST"):
                    row.append(sample_nearest(image, (sx0+sx1)/2, (sy0+sy1)/2))
                else:
                    row.append(sample_box(image, sx0, sy0, sx1, sy1))
                if has_alpha:
                    arow.append(pick_alpha(sx0, sy0, sx1, sy1))
            out.append(row)
            if has_alpha:
                out_alpha.append(arow)
        return ImageData(tw, th, out, image.source, out_alpha)

    if mode == "CROP":
        scale = max(tw/sw, th/sh)
    else:  # FIT
        scale = min(tw/sw, th/sh)

    rw = max(1, int(round(sw * scale)))
    rh = max(1, int(round(sh * scale)))

    resized = []
    resized_alpha = [] if has_alpha else None
    scale_x = sw / rw
    scale_y = sh / rh
    for y in range(rh):
        row = []
        arow = [] if has_alpha else None
        sy0 = y*scale_y
        sy1 = (y+1)*scale_y
        for x in range(rw):
            sx0 = x*scale_x
            sx1 = (x+1)*scale_x
            if filter_mode.startswith("NEAREST"):
                row.append(sample_nearest(image, (sx0+sx1)/2, (sy0+sy1)/2))
            else:
                row.append(sample_box(image, sx0, sy0, sx1, sy1))
            if has_alpha:
                arow.append(pick_alpha(sx0, sy0, sx1, sy1))
        resized.append(row)
        if has_alpha:
            resized_alpha.append(arow)

    if mode == "CROP":
        ox = max(0, (rw - tw)//2)
        oy = max(0, (rh - th)//2)
        out = [row[ox:ox+tw] for row in resized[oy:oy+th]]
        out_alpha = None
        if has_alpha:
            out_alpha = [row[ox:ox+tw] for row in resized_alpha[oy:oy+th]]
        return ImageData(tw, th, out, image.source, out_alpha)

    # FIT + matte : les bandes de matte restent opaques ; l'alpha de l'image est conservé.
    out = [[matte for _ in range(tw)] for __ in range(th)]
    out_alpha = [[255 for _ in range(tw)] for __ in range(th)] if has_alpha else None
    ox = (tw-rw)//2
    oy = (th-rh)//2
    for y in range(rh):
        ty = oy+y
        if 0 <= ty < th:
            for x in range(rw):
                tx = ox+x
                if 0 <= tx < tw:
                    out[ty][tx] = resized[y][x]
                    if has_alpha:
                        out_alpha[ty][tx] = resized_alpha[y][x]
    return ImageData(tw, th, out, image.source, out_alpha)


def clone_source_raw(image):
    if image.width % 8 or image.height % 8:
        raise ValueError("TILESET RAW : les dimensions source doivent être des multiples de 8.")
    pixels=[list(row) for row in image.pixels]
    alpha=[list(row) for row in image.alpha] if image.alpha is not None else None
    return ImageData(image.width,image.height,pixels,image.source,alpha)


def apply_dms_raw_colorkey(image, enabled=True):
    """Applique la convention ergonomique DMS aux tilesets opaques.

    - Une vraie transparence alpha reste prioritaire.
    - Un PNG indexé sans tRNS arrive déjà avec son index 0 transparent.
    - Pour un TILESET RAW RGB/RGBA totalement opaque, la couleur du pixel
      supérieur gauche devient la couleur-clé transparente. C'est l'équivalent
      pratique du slot palette 0 des machines à tiles.

    Cette règle ne s'applique qu'au workflow TILESET RAW, jamais aux images
    plein écran redimensionnées.
    """
    if not enabled or image.width <= 0 or image.height <= 0:
        return image
    if image.alpha is not None and any(a < 128 for row in image.alpha for a in row):
        return image
    key = tuple(image.pixels[0][0])
    alpha = []
    hit = 0
    for row in image.pixels:
        arow = []
        for px in row:
            is_key = tuple(px) == key
            arow.append(0 if is_key else 255)
            hit += 1 if is_key else 0
        alpha.append(arow)
    # Si le pixel de coin est accidentellement unique, on évite de créer un
    # trou isolé. Une vraie couleur de fond de tileset est normalement répétée.
    if hit < 2:
        return image
    return ImageData(image.width, image.height, [list(r) for r in image.pixels], image.source, alpha)


# ---------------------------------------------------------------------------
# QUANTIZATION
# ---------------------------------------------------------------------------

def weighted_kmeans_rgb333(freq: Counter, k: int, iterations=12):
    """
    Weighted k-means over at most 512 RGB333 points.
    Deterministic farthest-first initialization.
    """
    if not freq:
        return [(0,0,0)]
    pts = list(freq.keys())
    if len(pts) <= k:
        return sorted(pts, key=lambda c:(luminance(c), c))

    # Most frequent first, then farthest from existing centers.
    first = max(pts, key=lambda c: freq[c])
    centers = [first]

    while len(centers) < k:
        def score(c):
            return min(dist_sq(c, p) for p in centers) * max(1, freq[c])
        nxt = max(pts, key=score)
        if nxt in centers:
            break
        centers.append(nxt)

    for _ in range(iterations):
        groups = [[] for _ in centers]
        for p in pts:
            idx = min(range(len(centers)), key=lambda i: dist_sq(p, centers[i]))
            groups[idx].append(p)

        newc = []
        for i, grp in enumerate(groups):
            if not grp:
                newc.append(centers[i])
                continue
            total = sum(freq[p] for p in grp)
            cr = sum(p[0]*freq[p] for p in grp) / total
            cg = sum(p[1]*freq[p] for p in grp) / total
            cb = sum(p[2]*freq[p] for p in grp) / total
            newc.append((
                clamp(int(round(cr)),0,7),
                clamp(int(round(cg)),0,7),
                clamp(int(round(cb)),0,7)
            ))
        # Deduplicate centers; refill if needed.
        dedup = []
        for c in newc:
            if c not in dedup:
                dedup.append(c)
        centers = dedup
        if len(centers) < k:
            remaining = [p for p in pts if p not in centers]
            remaining.sort(key=lambda p: freq[p], reverse=True)
            for p in remaining:
                if p not in centers:
                    centers.append(p)
                if len(centers) >= k:
                    break

    return centers[:k]


def quantize_union(colors_counter, capacity=16):
    if len(colors_counter) <= capacity:
        return sorted(colors_counter.keys(), key=lambda c:(luminance(c), c))
    return weighted_kmeans_rgb333(colors_counter, capacity, iterations=8)


def palette_error(tile_counter, palette):
    err = 0
    for c, n in tile_counter.items():
        nc = nearest_color(c, palette)
        err += dist_sq(c, nc) * n
    return err


@dataclass
class TileStat:
    tx: int
    ty: int
    colors: Counter
    palette_id: int = 0
    unique_before: int = 0
    error: int = 0


@dataclass
class ConversionResult:
    image: ImageData
    palettes: list
    palette_ids: list
    tile_stats: list
    tile_size: int
    tilemap: list
    unique_tiles: list
    total_tiles: int
    duplicate_tiles: int
    flip_reused: int
    warnings: list
    source_rgb333_colors: int
    final_colors: int
    vram_bytes: int
    quality_mse: float
    palette_usage: dict
    colors_per_bank: int
    tile_budget: int


def extract_tile_counters(image333, tile_size):
    h = len(image333)
    w = len(image333[0]) if h else 0
    stats = []
    for ty in range(0,h,tile_size):
        for tx in range(0,w,tile_size):
            cnt = Counter()
            for y in range(ty,min(ty+tile_size,h)):
                for x in range(tx,min(tx+tile_size,w)):
                    c=image333[y][x]
                    if c is not None:
                        cnt[c] += 1
            stats.append(TileStat(tx,ty,cnt,unique_before=len(cnt)))
    return stats


def build_palette_banks(tile_stats, bank_count, capacity=16, fixed_palettes=None):
    """
    Greedy seed + iterative reassignment/refinement.
    fixed_palettes: {local_bank_index: [RGB333,...]}
    Locked banks are NEVER modified.
    """
    fixed_palettes = fixed_palettes or {}
    if bank_count <= 0:
        raise ValueError("Au moins une palette doit être sélectionnée.")
    if not tile_stats:
        return [[(0,0,0)] for _ in range(bank_count)]

    ordered = sorted(
        tile_stats,
        key=lambda t:(t.unique_before, sum(t.colors.values())),
        reverse=True
    )

    palettes = [None] * bank_count
    for idx, pal in fixed_palettes.items():
        if 0 <= idx < bank_count and pal:
            palettes[idx] = [tuple(c) for c in pal[:capacity]]

    def candidate_novelty(pal):
        existing = [p for p in palettes if p]
        if not existing:
            return 10**9
        return min(sum(min(dist_sq(c, p) for p in ep) for c in pal) for ep in existing)

    for i in range(bank_count):
        if palettes[i] is not None:
            continue
        best = None
        best_score = -1
        for t in ordered[:max(32, min(len(ordered), 256))]:
            pal = quantize_union(t.colors, capacity)
            sc = candidate_novelty(pal) + t.unique_before * 4
            if sc > best_score:
                best = pal
                best_score = sc
        palettes[i] = best or [(0,0,0)]

    for _ in range(8):
        groups = [[] for _ in range(bank_count)]
        for t in tile_stats:
            pid = min(range(bank_count), key=lambda i: palette_error(t.colors, palettes[i]))
            t.palette_id = pid
            groups[pid].append(t)

        new_palettes = []
        for i, grp in enumerate(groups):
            if i in fixed_palettes:
                new_palettes.append(palettes[i])
                continue
            if not grp:
                new_palettes.append(palettes[i])
                continue
            union = Counter()
            for t in grp:
                union.update(t.colors)
            new_palettes.append(quantize_union(union, capacity))
        palettes = new_palettes

    for t in tile_stats:
        t.error = palette_error(t.colors, palettes[t.palette_id])

    return palettes


BAYER_2 = [
    [0,2],
    [3,1],
]
BAYER_4 = [
    [0,8,2,10],
    [12,4,14,6],
    [3,11,1,9],
    [15,7,13,5],
]


def map_pixel_dither(c, palette, x, y, mode, strength=1.15):
    if mode == "OFF" or len(palette) <= 1 or strength <= 0:
        return nearest_color(c, palette)

    matrix = BAYER_2 if "2×2" in mode else BAYER_4
    n = len(matrix)
    threshold = (matrix[y % n][x % n] + 0.5) / (n*n) - 0.5
    cc = tuple(clamp(int(round(v + threshold*strength)),0,7) for v in c)
    return nearest_color(cc, palette)


def tile_pattern(indices, tile_size):
    return tuple(tuple(row) for row in indices)


def flip_pattern(pat, fx=False, fy=False):
    rows = pat[::-1] if fy else pat
    if fx:
        rows = tuple(tuple(reversed(r)) for r in rows)
    return rows


def convert_image(
    prepared: ImageData,
    palette_ids,
    tile_size=8,
    dither="OFF",
    colors_per_bank=16,
    locked_palettes=None,
    dither_strength=1.15,
    tile_budget=1024,
    preserve_tile_order=False
):
    warnings = []
    palette_ids = [int(x) for x in palette_ids]
    if not palette_ids:
        raise ValueError("Sélectionne au moins une banque de palette.")
    if len(set(palette_ids)) != len(palette_ids):
        raise ValueError("Liste de banques de palette invalide.")
    colors_per_bank = clamp(int(colors_per_bank), 2, 16)
    tile_budget = max(1, int(tile_budget))
    locked_palettes = locked_palettes or {}

    has_transparency = prepared.alpha is not None and any(a < 128 for row in prepared.alpha for a in row)
    opaque_capacity = colors_per_bank - (1 if has_transparency else 0)
    if opaque_capacity < 1:
        raise ValueError("Palette insuffisante pour réserver l'index 0 transparent.")

    image333 = []
    source_freq = Counter()
    for y,row in enumerate(prepared.pixels):
        qrow = []
        for x,p in enumerate(row):
            if has_transparency and prepared.alpha[y][x] < 128:
                qrow.append(None)
                continue
            q = rgb888_to_rgb333(p)
            qrow.append(q)
            source_freq[q] += 1
        image333.append(qrow)

    tile_stats = extract_tile_counters(image333, tile_size)

    over_limit = sum(1 for t in tile_stats if t.unique_before > opaque_capacity)
    if over_limit:
        warnings.append(
            f"{over_limit} tiles dépassaient {opaque_capacity} couleurs opaques RGB333 avant réduction locale."
        )

    fixed_local = {}
    for local_idx, physical_id in enumerate(palette_ids):
        if physical_id in locked_palettes and locked_palettes[physical_id]:
            vals=[tuple(c) for c in locked_palettes[physical_id]]
            if has_transparency and vals:
                vals=vals[1:]  # index 0 is reserved for transparency
            fixed_local[local_idx] = vals[:opaque_capacity]

    work_palettes = build_palette_banks(
        tile_stats, len(palette_ids), opaque_capacity, fixed_local
    )
    palettes = ([[(0,0,0)] + p[:opaque_capacity] for p in work_palettes]
                if has_transparency else work_palettes)

    h, w = prepared.height, prepared.width
    final333 = [[(0,0,0) for _ in range(w)] for __ in range(h)]
    all_patterns = []
    tile_palette_ids = []

    for t in tile_stats:
        pal = work_palettes[t.palette_id]
        index_offset = 1 if has_transparency else 0
        pindex = {c:i+index_offset for i,c in enumerate(pal)}
        rows = []
        for y in range(t.ty, min(t.ty+tile_size,h)):
            rr = []
            for x in range(t.tx, min(t.tx+tile_size,w)):
                src=image333[y][x]
                if src is None:
                    final333[y][x] = (0,0,0)
                    rr.append(0)
                    continue
                mapped = map_pixel_dither(
                    src, pal, x, y, dither, dither_strength
                )
                final333[y][x] = mapped
                rr.append(pindex.get(mapped, index_offset))
            while len(rr) < tile_size:
                rr.append(0)
            rows.append(rr)
        while len(rows) < tile_size:
            rows.append([0]*tile_size)
        all_patterns.append(tile_pattern(rows, tile_size))
        tile_palette_ids.append(palette_ids[t.palette_id])

    canonical = {}
    unique_tiles = []
    duplicate = 0
    flip_reused = 0
    mapped_tiles = []

    if preserve_tile_order:
        # TILESET RAW contract: source cell N is DIMG tile N. No ID-changing dedup.
        unique_tiles=list(all_patterns)
        mapped_tiles=[(i,pid,False,False) for i,pid in enumerate(tile_palette_ids)]
    else:
        for pat, physical_pid in zip(all_patterns, tile_palette_ids):
            variants = [
                (pat, False, False),
                (flip_pattern(pat, True, False), True, False),
                (flip_pattern(pat, False, True), False, True),
                (flip_pattern(pat, True, True), True, True),
            ]
            hit = None
            for var, fx, fy in variants:
                if var in canonical:
                    hit = (canonical[var], fx, fy)
                    break
            if hit is None:
                uid = len(unique_tiles)
                unique_tiles.append(pat)
                canonical[pat] = uid
                canonical[flip_pattern(pat,True,False)] = uid
                canonical[flip_pattern(pat,False,True)] = uid
                canonical[flip_pattern(pat,True,True)] = uid
                mapped_tiles.append((uid,physical_pid,False,False))
            else:
                duplicate += 1
                if hit[1] or hit[2]:
                    flip_reused += 1
                mapped_tiles.append((hit[0],physical_pid,hit[1],hit[2]))

    final_pixels = [
        [rgb333_to_rgb888(final333[y][x]) for x in range(w)]
        for y in range(h)
    ]
    final_colors = len({c for row in final333 for c in row})
    bytes_per_tile = tile_size*tile_size//2
    vram = len(unique_tiles)*bytes_per_tile

    if len(unique_tiles) > tile_budget:
        warnings.append(
            f"{len(unique_tiles)} tiles uniques dépassent le budget d'analyse réglé à {tile_budget}. "
            "Ce budget est un garde-fou de l'outil, pas une nouvelle spécification VDP."
        )

    total_err = 0
    npx = max(1, w*h)
    opaque_n=0
    for y in range(h):
        for x in range(w):
            if image333[y][x] is None:
                continue
            total_err += dist_sq(image333[y][x], final333[y][x]); opaque_n+=1
    mse = total_err / max(1,opaque_n)

    usage = {pid:0 for pid in palette_ids}
    for _, pid, _, _ in mapped_tiles:
        usage[pid] = usage.get(pid,0) + 1

    return ConversionResult(
        image=ImageData(w,h,final_pixels,prepared.source),
        palettes=palettes,
        palette_ids=palette_ids,
        tile_stats=tile_stats,
        tile_size=tile_size,
        tilemap=mapped_tiles,
        unique_tiles=unique_tiles,
        total_tiles=len(all_patterns),
        duplicate_tiles=duplicate,
        flip_reused=flip_reused,
        warnings=warnings,
        source_rgb333_colors=len(source_freq),
        final_colors=final_colors,
        vram_bytes=vram,
        quality_mse=mse,
        palette_usage=usage,
        colors_per_bank=colors_per_bank,
        tile_budget=tile_budget
    )


PALETTE_MAP_UI_COLORS = [
    (70,130,180), (220,80,80), (80,180,110), (220,170,60),
    (150,100,210), (70,190,200), (220,110,180), (180,180,180)
]


def build_palette_map_image(result: ConversionResult):
    w,h = result.image.width, result.image.height
    out = [[(20,20,20) for _ in range(w)] for __ in range(h)]
    ts = result.tile_size
    for stat in result.tile_stats:
        physical = result.palette_ids[stat.palette_id]
        color = PALETTE_MAP_UI_COLORS[physical % len(PALETTE_MAP_UI_COLORS)]
        for y in range(stat.ty, min(stat.ty+ts,h)):
            for x in range(stat.tx, min(stat.tx+ts,w)):
                out[y][x] = color
    return ImageData(w,h,out,result.image.source)


def build_error_map_image(result: ConversionResult):
    w,h = result.image.width, result.image.height
    out = [[(0,0,0) for _ in range(w)] for __ in range(h)]
    ts = result.tile_size
    max_err = max([t.error for t in result.tile_stats] or [1])
    for stat in result.tile_stats:
        level = 0 if max_err <= 0 else stat.error / max_err
        r = int(255 * level)
        g = int(180 * (1-level))
        b = 20
        color = (r,g,b)
        for y in range(stat.ty, min(stat.ty+ts,h)):
            for x in range(stat.tx, min(stat.tx+ts,w)):
                out[y][x] = color
    return ImageData(w,h,out,result.image.source)


# ---------------------------------------------------------------------------
# EXPORT DIMG / DPAL
# ---------------------------------------------------------------------------

def pack_4bpp(pat):
    vals = [v & 0xF for row in pat for v in row]
    out = bytearray()
    for i in range(0,len(vals),2):
        a = vals[i]
        b = vals[i+1] if i+1 < len(vals) else 0
        out.append((a<<4)|b)
    return bytes(out)


def _png_bytes(image):
    import tempfile
    fd, name = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    p = Path(name)
    try:
        write_png_rgb(p, image)
        return p.read_bytes()
    finally:
        try:
            p.unlink()
        except Exception:
            pass


def export_dimg(path, result: ConversionResult, mode_name, settings, source_name):
    palette_map_image = build_palette_map_image(result)
    error_map_image = build_error_map_image(result)

    manifest = {
        "format": "DIMG",
        "format_version": 2,
        "generator": f"{APP_NAME} {APP_VERSION}",
        "source": source_name,
        "mode": {
            "name": mode_name,
            **DMS_MODES[mode_name],
        },
        "settings": settings,
        "rgb_format": "RGB333",
        "bpp": 4,
        "selected_palette_ids": result.palette_ids,
        "palette_count": len(result.palettes),
        "colors_per_bank": result.colors_per_bank,
        "palettes": [
            {
                "physical_id": pid,
                "colors_rgb333": [list(c) for c in pal],
                "words_hex": [rgb333_hex(c) for c in pal],
                "count": len(pal),
                "tile_usage": result.palette_usage.get(pid,0),
            }
            for pid,pal in zip(result.palette_ids,result.palettes)
        ],
        "tiles": {
            "tile_size": result.tile_size,
            "preserve_source_cell_order": bool(settings.get("preserve_tile_order", False)),
            "total": result.total_tiles,
            "unique": len(result.unique_tiles),
            "duplicates": result.duplicate_tiles,
            "flip_reused": result.flip_reused,
            "vram_bytes_estimate": result.vram_bytes,
            "analysis_tile_budget": result.tile_budget,
        },
        "quality": {
            "rgb333_mse": result.quality_mse,
            "source_rgb333_colors": result.source_rgb333_colors,
            "final_colors": result.final_colors,
        },
        "tilemap": [
            {"tile":tid, "palette":pid, "flip_x":fx, "flip_y":fy}
            for tid,pid,fx,fy in result.tilemap
        ],
        "warnings": result.warnings,
    }

    palette_bin = bytearray()
    palette_ids_bin = bytearray()
    for pid,pal in zip(result.palette_ids,result.palettes):
        palette_ids_bin.append(pid & 0xFF)
        vals = [rgb333_word(c) for c in pal[:16]]
        vals.extend([0]*(16-len(vals)))
        for w in vals:
            palette_bin += struct.pack(">H", w)

    tiles_bin = b"".join(pack_4bpp(t) for t in result.unique_tiles)

    tilemap_bin = bytearray()
    palette_map_bin = bytearray()
    for tid,pid,fx,fy in result.tilemap:
        word = (
            (tid & 0x03FF) |
            ((pid & 0x07) << 10) |
            ((1 if fx else 0) << 13) |
            ((1 if fy else 0) << 14)
        )
        tilemap_bin += struct.pack(">H", word)
        palette_map_bin.append(pid & 0xFF)

    report = (
        "DMS IMAGE CONVERTER - REPORT\n"
        "============================\n"
        f"Mode : {mode_name}\n"
        f"Output : {result.image.width}×{result.image.height}\n"
        f"Banques utilisées : {', '.join('P'+str(x) for x in result.palette_ids)}\n"
        f"Couleurs max / banque : {result.colors_per_bank}\n"
        f"RGB333 source : {result.source_rgb333_colors}\n"
        f"Final colors : {result.final_colors}\n"
        f"RGB333 MSE : {result.quality_mse:.3f}\n"
        f"Tiles : {result.total_tiles}\n"
        f"Unique tiles : {len(result.unique_tiles)}\n"
        f"Duplicates : {result.duplicate_tiles}\n"
        f"Flip reuse : {result.flip_reused}\n"
        f"VRAM estimate : {result.vram_bytes} bytes\n"
        f"Analysis tile budget : {result.tile_budget}\n\n"
        "PALETTE USAGE\n"
        "-------------\n" +
        "\n".join(f"P{pid}: {result.palette_usage.get(pid,0)} tiles" for pid in result.palette_ids) +
        "\n\nWARNINGS\n--------\n" +
        ("\n".join("* "+w for w in result.warnings) if result.warnings else "None")
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        z.writestr("palette_ids.bin", bytes(palette_ids_bin))
        z.writestr("palettes.bin", bytes(palette_bin))
        z.writestr("tiles.bin", tiles_bin)
        z.writestr("tilemap.bin", bytes(tilemap_bin))
        z.writestr("palette_map.bin", bytes(palette_map_bin))
        z.writestr("preview.png", _png_bytes(result.image))
        z.writestr("palette_map_preview.png", _png_bytes(palette_map_image))
        z.writestr("error_map_preview.png", _png_bytes(error_map_image))
        z.writestr("report.txt", report)
        z.writestr(
            "README.txt",
            "DIMG V2 - DMS-1 converted image resource.\n"
            "Only selected physical palette banks are used.\n"
            "Each tile references exactly one selected palette bank.\n"
            "tilemap.bin remains a logical GDK handoff representation.\n"
        )


def convert_tileset_raw_file(source_path, out_path, mode_name="Mode 0 - STANDARD", palette_ids=(0,), colors_per_bank=16, tile_budget=1024):
    """Headless official TILESET RAW path used by Map Builder / GDK integrations.

    It preserves source 8x8 cell order exactly and writes the normal DIMG V2
    container; no alternate format is introduced.
    """
    source=read_png_image_data(source_path)
    prepared=apply_dms_raw_colorkey(clone_source_raw(source), True)
    ids=[int(x) for x in palette_ids]
    result=convert_image(
        prepared, ids, 8, "OFF", int(colors_per_bank), {}, 1.0, int(tile_budget),
        preserve_tile_order=True
    )
    settings={
        "resize_mode":TILESET_RAW_MODE,
        "filter":"NEAREST / PIXEL",
        "dither":"OFF",
        "tile_size":8,
        "colors_per_bank":int(colors_per_bank),
        "tile_budget":int(tile_budget),
        "preserve_tile_order":True,
        "raw_colorkey":True,
        "headless_export":True,
    }
    export_dimg(out_path,result,mode_name,settings,Path(source_path).name)
    return result


def export_dpal(path, palettes, palette_ids=None):
    if palette_ids is None:
        palette_ids = list(range(len(palettes)))
    data = {
        "format":"DPAL",
        "version":2,
        "rgb_format":"RGB333",
        "palettes":[
            {
                "id":int(pid),
                "physical_id":int(pid),
                "rgb333":[list(c) for c in p],
                "rgb888":[list(rgb333_to_rgb888(c)) for c in p],
                "hex_words":[rgb333_hex(c) for c in p],
            } for pid,p in zip(palette_ids,palettes)
        ]
    }
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_dpal(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != "DPAL":
        raise ValueError("Ce fichier n'est pas une palette DPAL.")
    out = {}
    for i,p in enumerate(data.get("palettes",[])):
        pid = int(p.get("physical_id", p.get("id", i)))
        colors = p.get("rgb333", [])
        out[pid] = [tuple(int(v) for v in c[:3]) for c in colors]
    if not out:
        raise ValueError("Aucune palette dans le fichier.")
    return out


# ---------------------------------------------------------------------------
# APP
# ---------------------------------------------------------------------------

class DMSImageConverter(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} - {APP_VERSION}")
        self.geometry("1600x940")
        self.minsize(1240,760)
        self.configure(bg="#17191d")

        self.loader = ImageLoader(self)
        self.source = None
        self.prepared = None
        self.result = None
        self.palette_map_image = None
        self.error_map_image = None

        self.source_preview_img = None
        self.result_preview_img = None
        self.palette_map_preview_img = None
        self.error_map_preview_img = None
        self.compare_left = None
        self.compare_right = None

        self.mode_var = tk.StringVar(value="Mode 0 - STANDARD")
        self.resize_var = tk.StringVar(value="CROP")
        self.filter_var = tk.StringVar(value="BOX / PHOTO")
        self.dither_var = tk.StringVar(value="OFF")
        self.tile_var = tk.IntVar(value=8)
        self.matte_var = tk.StringVar(value="#000000")
        self.colors_per_bank_var = tk.IntVar(value=16)
        self.dither_strength_var = tk.DoubleVar(value=1.15)
        self.tile_budget_var = tk.IntVar(value=1024)
        self.raw_colorkey_var = tk.BooleanVar(value=True)

        self.bank_vars = [tk.BooleanVar(value=(i<4)) for i in range(8)]
        self.bank_checks = []
        self.bank_label_var = tk.StringVar(value="4 banques : P0 P1 P2 P3")

        self.locked_palettes = {}
        self.lock_status_var = tk.StringVar(value="Aucune palette verrouillée")

        self.converting = False
        self.loading_image = False
        self.setup_path = None
        self._closing = False
        self._ui_queue = queue.Queue()
        self._ui_poll_id = None
        self._render_after_ids = {}

        self._style()
        self._build_ui()
        self._sync_mode_banks()
        self._update_mode_info()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._ui_poll_id = self.after(25, self._poll_ui_queue)

    # ---------------- STYLE ----------------

    def _style(self):
        s=ttk.Style(self)
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure(".",font=("Segoe UI",9))
        s.configure("TFrame",background="#17191d")
        s.configure("TLabelframe",background="#17191d",foreground="#ececec")
        s.configure("TLabelframe.Label",background="#17191d",foreground="#ececec",
                    font=("Segoe UI",9,"bold"))
        s.configure("TLabel",background="#17191d",foreground="#d9d9d9")
        s.configure("Title.TLabel",background="#17191d",foreground="#f5f5f5",
                    font=("Segoe UI",17,"bold"))
        s.configure("Sub.TLabel",background="#17191d",foreground="#9aa3ad")
        s.configure("TCheckbutton",background="#17191d",foreground="#d9d9d9")
        s.configure("Accent.TButton",font=("Segoe UI",9,"bold"))

    # ---------------- UI ----------------

    def _build_ui(self):
        top=ttk.Frame(self,padding=(12,8))
        top.pack(fill="x")
        ttk.Label(top,text=APP_NAME,style="Title.TLabel").pack(side="left")
        ttk.Label(
            top,
            text="DIMG V2 • RGB333 • 4bpp",
            style="Sub.TLabel"
        ).pack(side="left",padx=14)

        ttk.Button(top,text="Exporter .dimg",command=self.export_dimg_action,
                   style="Accent.TButton").pack(side="right",padx=4)
        ttk.Button(top,text="Sauver setup",command=self.save_setup).pack(side="right",padx=4)
        ttk.Button(top,text="Ouvrir setup",command=self.open_setup).pack(side="right",padx=4)
        self.open_button=ttk.Button(top,text="Ouvrir image",command=self.open_image,
                   style="Accent.TButton")
        self.open_button.pack(side="right",padx=10)

        body=ttk.Panedwindow(self,orient="horizontal")
        body.pack(fill="both",expand=True,padx=10,pady=(0,8))
        left=ttk.Frame(body,padding=6)
        center=ttk.Frame(body,padding=6)
        right=ttk.Frame(body,padding=6)
        body.add(left,weight=3)
        body.add(center,weight=7)
        body.add(right,weight=4)

        self._build_left(left)
        self._build_center(center)
        self._build_right(right)

        bottom=ttk.Frame(self,padding=(12,0,12,10))
        bottom.pack(fill="x")
        self.status=ttk.Label(bottom,text="Prêt - charge une image.")
        self.status.pack(side="left")
        ttk.Label(
            bottom,
            text="RGB333 • 4bpp • 1 palette de 16 max par tile • aucune API",
            style="Sub.TLabel"
        ).pack(side="right")

    def _build_left(self,parent):
        cfg=ttk.LabelFrame(parent,text="Cible DMS-1",padding=8)
        cfg.pack(fill="x")

        ttk.Label(cfg,text="Mode vidéo").grid(row=0,column=0,sticky="w")
        cb=ttk.Combobox(cfg,textvariable=self.mode_var,values=list(DMS_MODES),state="readonly")
        cb.grid(row=0,column=1,sticky="ew",padx=(8,0))
        cb.bind("<<ComboboxSelected>>",lambda e:self._mode_changed())

        cfg.columnconfigure(1,weight=1)
        self.mode_info=ttk.Label(cfg,text="",wraplength=350,style="Sub.TLabel")
        self.mode_info.grid(row=1,column=0,columnspan=2,sticky="w",pady=(7,0))

        banks=ttk.LabelFrame(parent,text="Banques de palettes autorisées",padding=8)
        banks.pack(fill="x",pady=(8,0))

        line=ttk.Frame(banks)
        line.pack(fill="x")
        self.bank_checks=[]
        for i in range(8):
            chk=ttk.Checkbutton(
                line,text=f"P{i}",variable=self.bank_vars[i],
                command=self._banks_changed
            )
            chk.pack(side="left",padx=2)
            self.bank_checks.append(chk)

        quick=ttk.Frame(banks)
        quick.pack(fill="x",pady=(6,0))
        ttk.Button(quick,text="1 pal",command=lambda:self.select_bank_count(1)).pack(side="left",padx=2)
        ttk.Button(quick,text="2 pal",command=lambda:self.select_bank_count(2)).pack(side="left",padx=2)
        ttk.Button(quick,text="3 pal",command=lambda:self.select_bank_count(3)).pack(side="left",padx=2)
        ttk.Button(quick,text="4 pal",command=lambda:self.select_bank_count(4)).pack(side="left",padx=2)
        ttk.Button(quick,text="Toutes",command=self.select_all_mode_banks).pack(side="right",padx=2)

        ttk.Label(banks,textvariable=self.bank_label_var,style="Sub.TLabel").pack(anchor="w",pady=(6,0))

        adv=ttk.LabelFrame(parent,text="Conversion",padding=8)
        adv.pack(fill="x",pady=(8,0))

        rows=[
            ("Resize",self.resize_var,RESIZE_MODES),
            ("Filtre",self.filter_var,FILTER_MODES),
            ("Dither",self.dither_var,DITHER_MODES),
            ("Couleurs max / pal",self.colors_per_bank_var,[4,6,8,10,12,14,16]),
            ("Cellule analyse/export",self.tile_var,TILE_SIZES),
        ]
        for r,(lab,var,vals) in enumerate(rows):
            ttk.Label(adv,text=lab).grid(row=r,column=0,sticky="w",pady=3)
            ttk.Combobox(adv,textvariable=var,values=vals,state="readonly").grid(
                row=r,column=1,sticky="ew",padx=(8,0),pady=3
            )

        ttk.Label(adv,text="Force dither").grid(row=5,column=0,sticky="w",pady=3)
        strength=ttk.Scale(adv,from_=0.0,to=2.0,variable=self.dither_strength_var)
        strength.grid(row=5,column=1,sticky="ew",padx=(8,0),pady=3)

        ttk.Label(adv,text="Budget analyse tiles").grid(row=6,column=0,sticky="w",pady=3)
        ttk.Checkbutton(
            adv, text="Fond du tileset = transparent", variable=self.raw_colorkey_var
        ).grid(row=8,column=0,columnspan=2,sticky="w",pady=(6,2))
        ttk.Entry(adv,textvariable=self.tile_budget_var,width=10).grid(
            row=6,column=1,sticky="w",padx=(8,0),pady=3
        )

        ttk.Label(adv,text="Matte FIT").grid(row=7,column=0,sticky="w",pady=3)
        ttk.Entry(adv,textvariable=self.matte_var,width=12).grid(
            row=7,column=1,sticky="w",padx=(8,0),pady=3
        )
        adv.columnconfigure(1,weight=1)

        self.convert_button=ttk.Button(
            parent,text="CONVERTIR POUR DMS-1",
            command=self.convert,style="Accent.TButton"
        )
        self.convert_button.pack(fill="x",pady=(8,3))
        self.progress=ttk.Progressbar(parent,mode="indeterminate")
        self.progress.pack(fill="x")

        presets=ttk.LabelFrame(parent,text="Profils rapides",padding=8)
        presets.pack(fill="x",pady=(8,0))
        ttk.Button(presets,text="Photo / peinture - propre",
                   command=lambda:self.set_preset("photo")).pack(fill="x",pady=2)
        ttk.Button(presets,text="Pixel art - nearest",
                   command=lambda:self.set_preset("pixel")).pack(fill="x",pady=2)
        ttk.Button(presets,text="Tileset RAW - dimensions + IDs conservés",
                   command=lambda:self.set_preset("tileset")).pack(fill="x",pady=2)
        ttk.Button(presets,text="Photo - dither doux",
                   command=lambda:self.set_preset("dither")).pack(fill="x",pady=2)
        ttk.Button(presets,text="Économe - 2 pal / 12 col / no dither",
                   command=lambda:self.set_preset("eco")).pack(fill="x",pady=2)

        lockbox=ttk.LabelFrame(parent,text="Palette LOCK / réutilisation",padding=8)
        lockbox.pack(fill="x",pady=(8,0))
        ttk.Button(lockbox,text="LOCK résultat actuel",
                   command=self.lock_current_palettes).pack(fill="x",pady=2)
        ttk.Button(lockbox,text="Importer .dpal comme LOCK",
                   command=self.import_dpal_action).pack(fill="x",pady=2)
        ttk.Button(lockbox,text="Effacer LOCK",
                   command=self.clear_palette_lock).pack(fill="x",pady=2)
        ttk.Label(lockbox,textvariable=self.lock_status_var,
                  style="Sub.TLabel",wraplength=340).pack(anchor="w",pady=(5,0))

        sourcebox=ttk.LabelFrame(parent,text="Source",padding=8)
        sourcebox.pack(fill="both",expand=True,pady=(8,0))
        self.source_text=tk.Text(sourcebox,height=8,bg="#202329",fg="#e8e8e8",
                                 relief="flat",wrap="word")
        self.source_text.pack(fill="both",expand=True)
        self.source_text.configure(state="disabled")

    def _build_center(self,parent):
        self.preview_nb=ttk.Notebook(parent)
        self.preview_nb.pack(fill="both",expand=True)

        tabs={}
        for name in ["Original","DMS-1","Comparaison","Map palettes","Map erreur"]:
            tab=ttk.Frame(self.preview_nb,padding=6)
            self.preview_nb.add(tab,text=name)
            can=tk.Canvas(tab,bg="#1f2227",highlightthickness=0)
            can.pack(fill="both",expand=True)
            tabs[name]=can

        self.source_canvas=tabs["Original"]
        self.result_canvas=tabs["DMS-1"]
        self.compare_canvas=tabs["Comparaison"]
        self.palette_map_canvas=tabs["Map palettes"]
        self.error_map_canvas=tabs["Map erreur"]

        self.source_canvas.bind("<Configure>",lambda e:self._schedule_render("source",self.render_source_preview))
        self.result_canvas.bind("<Configure>",lambda e:self._schedule_render("result",self.render_result_preview))
        self.compare_canvas.bind("<Configure>",lambda e:self._schedule_render("compare",self.render_compare))
        self.palette_map_canvas.bind("<Configure>",lambda e:self._schedule_render("palmap",self.render_palette_map))
        self.error_map_canvas.bind("<Configure>",lambda e:self._schedule_render("errmap",self.render_error_map))

    def _build_right(self,parent):
        nb=ttk.Notebook(parent)
        nb.pack(fill="both",expand=True)
        diag=ttk.Frame(nb,padding=8)
        pal=ttk.Frame(nb,padding=8)
        exp=ttk.Frame(nb,padding=8)
        nb.add(diag,text="Diagnostic")
        nb.add(pal,text="Palettes")
        nb.add(exp,text="Export")

        cards=ttk.Frame(diag)
        cards.pack(fill="x")
        self.card_vars={}
        for i,key in enumerate(["OUTPUT","COLORS","BANKS","UNIQUE","VRAM","ERROR"]):
            box=ttk.LabelFrame(cards,text=key,padding=5)
            box.grid(row=i//2,column=i%2,sticky="nsew",padx=3,pady=3)
            v=tk.StringVar(value="-")
            self.card_vars[key]=v
            ttk.Label(box,textvariable=v,font=("Segoe UI",11,"bold")).pack()
            cards.columnconfigure(i%2,weight=1)

        self.diag_text=tk.Text(diag,bg="#202329",fg="#e8e8e8",
                               relief="flat",wrap="word")
        self.diag_text.pack(fill="both",expand=True,pady=(8,0))
        self.diag_text.configure(state="disabled")

        self.palette_canvas=tk.Canvas(pal,bg="#1f2227",height=380,highlightthickness=0)
        self.palette_canvas.pack(fill="x")
        self.palette_canvas.bind("<Configure>",lambda e:self.draw_palettes())

        self.palette_text=tk.Text(pal,bg="#202329",fg="#e8e8e8",
                                  relief="flat",wrap="word")
        self.palette_text.pack(fill="both",expand=True,pady=(8,0))
        self.palette_text.configure(state="disabled")

        ttk.Button(exp,text="Exporter PNG DMS-1",
                   command=self.export_png,style="Accent.TButton").pack(fill="x",pady=3)
        ttk.Button(exp,text="Exporter .dimg V2",
                   command=self.export_dimg_action,style="Accent.TButton").pack(fill="x",pady=3)
        ttk.Button(exp,text="Exporter palettes .dpal",
                   command=self.export_palette_action).pack(fill="x",pady=3)
        ttk.Button(exp,text="Exporter map palettes PNG",
                   command=self.export_palette_map_png).pack(fill="x",pady=3)
        ttk.Button(exp,text="Exporter map erreur PNG",
                   command=self.export_error_map_png).pack(fill="x",pady=3)

        ttk.Separator(exp).pack(fill="x",pady=12)
        ttk.Button(exp,text="Sauver setup .diconvproj",
                   command=self.save_setup).pack(fill="x",pady=3)
        ttk.Button(exp,text="Ouvrir setup .diconvproj",
                   command=self.open_setup).pack(fill="x",pady=3)

        ttk.Label(
            exp,
            text="DIMG V2 ajoute les IDs physiques de palettes, palette_map.bin, "
                 "les previews de diagnostic et les métriques qualité.",
            wraplength=360,style="Sub.TLabel"
        ).pack(anchor="w",pady=(12,0))

    # ---------------- BANKS / MODE ----------------

    def _mode_changed(self):
        self._sync_mode_banks()
        self._update_mode_info()

    def _sync_mode_banks(self):
        maxp=DMS_MODES[self.mode_var.get()]["palettes"]
        for i,chk in enumerate(self.bank_checks):
            if i < maxp:
                chk.state(["!disabled"])
            else:
                self.bank_vars[i].set(False)
                chk.state(["disabled"])
        if not self.get_selected_palette_ids():
            for i in range(maxp):
                self.bank_vars[i].set(True)
        self._banks_changed()

    def _banks_changed(self):
        ids=self.get_selected_palette_ids()
        txt=f"{len(ids)} banque{'s' if len(ids)!=1 else ''} : " + " ".join(f"P{i}" for i in ids)
        self.bank_label_var.set(txt)

    def get_selected_palette_ids(self):
        maxp=DMS_MODES[self.mode_var.get()]["palettes"]
        return [i for i in range(maxp) if self.bank_vars[i].get()]

    def select_bank_count(self,n):
        maxp=DMS_MODES[self.mode_var.get()]["palettes"]
        n=max(1,min(int(n),maxp))
        for i in range(8):
            self.bank_vars[i].set(i<n and i<maxp)
        self._banks_changed()

    def select_all_mode_banks(self):
        maxp=DMS_MODES[self.mode_var.get()]["palettes"]
        for i in range(8):
            self.bank_vars[i].set(i<maxp)
        self._banks_changed()

    def _update_mode_info(self):
        m=DMS_MODES[self.mode_var.get()]
        self.mode_info.configure(
            text=f"{m['description']}\n"
                 "Tu peux utiliser moins de palettes que le maximum et réserver les autres au jeu."
        )

    # ---------------- PRESETS ----------------

    def set_preset(self,name):
        if name=="photo":
            self.resize_var.set("CROP")
            self.filter_var.set("BOX / PHOTO")
            self.dither_var.set("OFF")
            self.dither_strength_var.set(1.0)
            self.colors_per_bank_var.set(16)
            self.select_all_mode_banks()
        elif name=="pixel":
            self.resize_var.set("CROP")
            self.filter_var.set("NEAREST / PIXEL")
            self.dither_var.set("OFF")
            self.colors_per_bank_var.set(16)
            self.select_all_mode_banks()
        elif name=="tileset":
            self.resize_var.set(TILESET_RAW_MODE)
            self.filter_var.set("NEAREST / PIXEL")
            self.dither_var.set("OFF")
            self.dither_strength_var.set(0.0)
            self.tile_var.set(8)
            self.colors_per_bank_var.set(16)
            self.select_bank_count(1)
        elif name=="dither":
            self.resize_var.set("CROP")
            self.filter_var.set("BOX / PHOTO")
            self.dither_var.set("ORDERED 4×4")
            self.dither_strength_var.set(1.0)
            self.colors_per_bank_var.set(16)
            self.select_all_mode_banks()
        elif name=="eco":
            self.resize_var.set("CROP")
            self.filter_var.set("BOX / PHOTO")
            self.dither_var.set("OFF")
            self.dither_strength_var.set(0.0)
            self.colors_per_bank_var.set(12)
            self.select_bank_count(2)

    # ---------------- THREAD/UI BRIDGE ----------------

    def _schedule_render(self,key,callback,delay=80):
        previous=self._render_after_ids.pop(key,None)
        if previous is not None:
            try:
                self.after_cancel(previous)
            except Exception:
                pass
        def run():
            self._render_after_ids.pop(key,None)
            if not self._closing:
                callback()
        self._render_after_ids[key]=self.after(delay,run)

    def _post_ui(self, callback, *args):
        if not self._closing:
            self._ui_queue.put((callback, args))

    def _poll_ui_queue(self):
        if self._closing:
            return
        processed = 0
        while processed < 32:
            try:
                callback, args = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args)
            except Exception as exc:
                # Toute erreur UI reste sur le thread Tk et ne tue pas silencieusement le polling.
                self.status.configure(text=f"Erreur interface : {exc}")
            processed += 1
        self._ui_poll_id = self.after(25, self._poll_ui_queue)

    def _on_close(self):
        self._closing = True
        for pending in list(self._render_after_ids.values()):
            try:
                self.after_cancel(pending)
            except Exception:
                pass
        self._render_after_ids.clear()
        if self._ui_poll_id is not None:
            try:
                self.after_cancel(self._ui_poll_id)
            except Exception:
                pass
            self._ui_poll_id = None
        self.destroy()

    # ---------------- SOURCE / SETUP ----------------

    def open_image(self,path=None):
        if self.loading_image or self.converting:
            return
        if path is None:
            path=filedialog.askopenfilename(
                title="Ouvrir une image",
                filetypes=[
                    ("Images","*.png *.gif *.ppm *.pgm *.jpg *.jpeg *.bmp *.webp *.tif *.tiff"),
                    ("PNG","*.png"),("Tous","*.*")]
            )
        if not path:
            return
        path=str(path)

        if self.loader.can_load_offthread(path):
            self.loading_image=True
            self.open_button.configure(state="disabled")
            self.convert_button.configure(state="disabled")
            self.progress.start(12)
            self.status.configure(text=f"Chargement image… {Path(path).name}")

            def worker():
                try:
                    image=self.loader.load(path,allow_tk=False)
                    self._post_ui(self._image_load_done,path,image)
                except Exception as exc:
                    self._post_ui(self._image_load_failed,exc)

            threading.Thread(target=worker,daemon=True,name="DMSImageLoad").start()
            return

        # Fallback Tk natif (GIF/PPM/PGM sans Pillow) : nécessairement sur le thread UI.
        try:
            self.status.configure(text=f"Chargement image… {Path(path).name}")
            self.update_idletasks()
            image=self.loader.load(path,allow_tk=True)
            self._image_load_done(path,image)
        except Exception as exc:
            self._image_load_failed(exc)

    def _image_load_done(self,path,image):
        self.loading_image=False
        self.progress.stop()
        self.open_button.configure(state="normal")
        self.convert_button.configure(state="normal")
        self.source=image
        self.result=None
        self.prepared=None
        self.palette_map_image=None
        self.error_map_image=None
        transparency = "oui" if self.source.alpha is not None else "non"
        self._set_text(
            self.source_text,
            f"Fichier : {Path(path).name}\n"
            f"Source : {self.source.width}×{self.source.height}\n"
            f"Format : {Path(path).suffix.lower() or '?'}\n"
            f"Transparence : {transparency}\n\n"
            "Choisis les banques P0…P7 autorisées puis Convertir."
        )
        self.render_source_preview()
        self.render_result_preview()
        self.render_compare()
        self.render_palette_map()
        self.render_error_map()
        self.status.configure(text=f"Image chargée : {Path(path).name}")

    def _image_load_failed(self,error):
        self.loading_image=False
        self.progress.stop()
        self.open_button.configure(state="normal")
        self.convert_button.configure(state="normal")
        self.status.configure(text="Erreur de chargement image.")
        messagebox.showerror("Image",f"Impossible d'ouvrir l'image.\n\n{error}")

    def save_setup(self):
        path=self.setup_path
        if not path:
            path=filedialog.asksaveasfilename(
                title="Sauver setup",
                defaultextension=".diconvproj",
                initialfile="DMS_IMAGE_SETUP.diconvproj",
                filetypes=[("DMS Image Converter Project","*.diconvproj")]
            )
        if not path:
            return
        source_path=self.source.source if self.source else ""
        try:
            if source_path:
                source_path=os.path.relpath(source_path,Path(path).parent)
        except Exception:
            pass
        data={
            "format":"DMS_IMAGE_CONVERTER_PROJECT",
            "version":1,
            "source":source_path,
            "settings":self.current_settings(),
        }
        try:
            target=Path(path); tmp=target.with_name(target.name+".tmp")
            tmp.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding="utf-8")
            os.replace(tmp,target)
            self.setup_path=path
            self.status.configure(text=f"Setup sauvé : {target.name}")
        except Exception as exc:
            try: tmp.unlink(missing_ok=True)
            except Exception: pass
            messagebox.showerror("Setup",f"Impossible de sauver le setup.\n\n{exc}")

    def open_setup(self):
        path=filedialog.askopenfilename(
            title="Ouvrir setup",
            filetypes=[("DMS Image Converter Project","*.diconvproj"),("JSON","*.json")]
        )
        if not path:
            return
        try:
            data=json.loads(Path(path).read_text(encoding="utf-8"))
            if data.get("format")!="DMS_IMAGE_CONVERTER_PROJECT":
                raise ValueError("Format de setup invalide.")
            s=data.get("settings",{})
            self.mode_var.set(s.get("mode",self.mode_var.get()))
            self._sync_mode_banks()
            ids=s.get("palette_ids",[])
            if ids:
                for i in range(8):
                    self.bank_vars[i].set(i in ids)
            self.resize_var.set(s.get("resize_mode","CROP"))
            self.filter_var.set(s.get("filter","BOX / PHOTO"))
            self.dither_var.set(s.get("dither","OFF"))
            self.tile_var.set(int(s.get("tile_size",8)))
            self.colors_per_bank_var.set(int(s.get("colors_per_bank",16)))
            self.dither_strength_var.set(float(s.get("dither_strength",1.15)))
            self.tile_budget_var.set(int(s.get("tile_budget",1024)))
            self.raw_colorkey_var.set(bool(s.get("raw_colorkey", True)))
            self.matte_var.set(s.get("matte","#000000"))
            locks=s.get("locked_palettes",{}) or {}
            self.locked_palettes={int(pid):[tuple(int(v) for v in color) for color in colors] for pid,colors in locks.items()}
            self._banks_changed(); self._update_lock_label()
            self._update_mode_info()

            sp=data.get("source","")
            if sp:
                pp=Path(sp)
                if not pp.is_absolute():
                    pp=Path(path).parent/pp
                if pp.exists():
                    self.open_image(str(pp))
                else:
                    messagebox.showwarning("Setup",f"Source du setup introuvable :\n{pp}")
            self.setup_path=path
            self.status.configure(text=f"Setup ouvert : {Path(path).name}")
        except Exception as e:
            messagebox.showerror("Setup",str(e))

    def current_settings(self):
        return {
            "mode":self.mode_var.get(),
            "palette_ids":self.get_selected_palette_ids(),
            "resize_mode":self.resize_var.get(),
            "preserve_tile_order":self.resize_var.get()==TILESET_RAW_MODE,
            "filter":self.filter_var.get(),
            "dither":self.dither_var.get(),
            "dither_strength":float(self.dither_strength_var.get()),
            "tile_size":int(self.tile_var.get()),
            "colors_per_bank":int(self.colors_per_bank_var.get()),
            "tile_budget":int(self.tile_budget_var.get()),
            "raw_colorkey":bool(self.raw_colorkey_var.get()),
            "matte":self.matte_var.get(),
            "locked_palette_ids":sorted(self.locked_palettes.keys()),
            "locked_palettes":{str(pid):[list(color) for color in pal] for pid,pal in self.locked_palettes.items()},
        }

    def parse_matte(self):
        s=self.matte_var.get().strip().lstrip("#")
        if len(s)!=6:
            raise ValueError("Matte invalide : utilise #RRGGBB.")
        try:
            return tuple(int(s[i:i+2],16) for i in (0,2,4))
        except Exception as exc:
            raise ValueError("Matte invalide : utilise #RRGGBB.") from exc

    # ---------------- PALETTE LOCK ----------------

    def lock_current_palettes(self):
        if self.result is None:
            messagebox.showinfo("Palette LOCK","Convertis d'abord une image.")
            return
        self.locked_palettes={
            pid:list(pal) for pid,pal in zip(self.result.palette_ids,self.result.palettes)
        }
        self._update_lock_label()
        self.status.configure(text="Palettes du résultat verrouillées.")

    def import_dpal_action(self):
        path=filedialog.askopenfilename(
            title="Importer DPAL",
            filetypes=[("DMS Palette","*.dpal"),("JSON","*.json")]
        )
        if not path:
            return
        try:
            self.locked_palettes=load_dpal(path)
            maxp=DMS_MODES[self.mode_var.get()]["palettes"]
            for i in range(8):
                if i < maxp and i in self.locked_palettes:
                    self.bank_vars[i].set(True)
            self._banks_changed()
            self._update_lock_label()
            self.status.configure(text=f"DPAL chargé : {Path(path).name}")
        except Exception as e:
            messagebox.showerror("DPAL",str(e))

    def clear_palette_lock(self):
        self.locked_palettes={}
        self._update_lock_label()
        self.status.configure(text="Palette LOCK effacé.")

    def _update_lock_label(self):
        if not self.locked_palettes:
            self.lock_status_var.set("Aucune palette verrouillée")
        else:
            self.lock_status_var.set(
                "LOCK : " + " ".join(f"P{i}" for i in sorted(self.locked_palettes))
            )

    # ---------------- CONVERSION ASYNC ----------------

    def convert(self):
        if self.source is None:
            messagebox.showinfo("Conversion","Charge d'abord une image.")
            return
        if self.loading_image or self.converting:
            return

        ids=self.get_selected_palette_ids()
        if not ids:
            messagebox.showwarning("Palettes","Sélectionne au moins une banque P0…P7.")
            return

        try:
            colors_per_bank=int(self.colors_per_bank_var.get())
            tile_budget=max(1,int(self.tile_budget_var.get()))
        except Exception:
            messagebox.showerror("Réglages","Valeur numérique invalide.")
            return

        m=DMS_MODES[self.mode_var.get()]
        settings=self.current_settings()
        source=self.source
        locked={pid:list(p) for pid,p in self.locked_palettes.items() if pid in ids}
        try:
            matte=self.parse_matte()
        except Exception as exc:
            messagebox.showerror("Réglages",str(exc)); return

        self.converting=True
        self.open_button.configure(state="disabled")
        self.convert_button.configure(state="disabled")
        self.progress.start(12)
        self.status.configure(
            text=f"Conversion… {len(ids)} palette(s), {colors_per_bank} couleurs max / banque"
        )

        def worker():
            try:
                raw_mode=settings["resize_mode"]==TILESET_RAW_MODE
                if raw_mode:
                    if int(settings["tile_size"]) != 8:
                        raise ValueError("TILESET RAW utilise obligatoirement des cellules 8×8.")
                    prepared=apply_dms_raw_colorkey(
                        clone_source_raw(source), bool(settings.get("raw_colorkey", True))
                    )
                else:
                    prepared=resize_image(
                        source,m["w"],m["h"],
                        settings["resize_mode"],settings["filter"],matte
                    )
                result=convert_image(
                    prepared,
                    ids,
                    int(settings["tile_size"]),
                    settings["dither"],
                    colors_per_bank,
                    locked,
                    float(settings["dither_strength"]),
                    tile_budget,
                    preserve_tile_order=raw_mode
                )
                palmap=build_palette_map_image(result)
                errmap=build_error_map_image(result)
                self._post_ui(self._conversion_done,prepared,result,palmap,errmap)
            except Exception as exc:
                self._post_ui(self._conversion_failed,exc)

        threading.Thread(target=worker,daemon=True).start()

    def _conversion_done(self,prepared,result,palmap,errmap):
        self.prepared=prepared
        self.result=result
        self.palette_map_image=palmap
        self.error_map_image=errmap
        self.converting=False
        self.progress.stop()
        self.open_button.configure(state="normal")
        self.convert_button.configure(state="normal")
        self.render_result_preview()
        self.render_compare()
        self.render_palette_map()
        self.render_error_map()
        self.render_diagnostics()
        self.draw_palettes()
        self.status.configure(
            text=f"Terminé : {len(result.palette_ids)} palette(s), "
                 f"{result.final_colors} couleurs finales, {len(result.unique_tiles)} tiles uniques."
        )

    def _conversion_failed(self,error):
        self.converting=False
        self.progress.stop()
        self.open_button.configure(state="normal")
        self.convert_button.configure(state="normal")
        self.status.configure(text="Erreur de conversion.")
        messagebox.showerror("Conversion",str(error))

    # ---------------- PREVIEWS ----------------

    def image_to_photo(self,image,max_w,max_h):
        if image is None:
            return None
        max_w=max(32,int(max_w))
        max_h=max(32,int(max_h))
        ratio=min(max_w/image.width,max_h/image.height)
        if ratio < 1:
            nw=max(1,int(image.width*ratio))
            nh=max(1,int(image.height*ratio))
            # La preview privilégie la réactivité UI ; le filtre de conversion réel reste inchangé.
            preview=resize_image(image,nw,nh,"STRETCH","NEAREST / PIXEL")
            img=tk.PhotoImage(width=nw,height=nh)
            for y,row in enumerate(preview.pixels):
                img.put("{"+" ".join("#%02x%02x%02x"%tuple(p) for p in row)+"}",to=(0,y))
            return img

        zoom=max(1,min(6,int(ratio)))
        img=tk.PhotoImage(width=image.width,height=image.height)
        for y,row in enumerate(image.pixels):
            img.put("{"+" ".join("#%02x%02x%02x"%tuple(p) for p in row)+"}",to=(0,y))
        return img.zoom(zoom,zoom) if zoom>1 else img

    def _render_single(self,canvas,image,attr_name,empty_text):
        canvas.delete("all")
        cw=max(120,canvas.winfo_width())
        ch=max(120,canvas.winfo_height())
        if image is None:
            canvas.create_text(cw//2,ch//2,text=empty_text,fill="#8f98a3")
            return
        img=self.image_to_photo(image,cw-30,ch-30)
        setattr(self,attr_name,img)
        canvas.create_image(cw//2,ch//2,image=img,anchor="center")

    def render_source_preview(self):
        self._render_single(self.source_canvas,self.source,"source_preview_img","Charge une image")

    def render_result_preview(self):
        self._render_single(self.result_canvas,self.result.image if self.result else None,
                            "result_preview_img","Conversion non lancée")

    def render_palette_map(self):
        self._render_single(self.palette_map_canvas,self.palette_map_image,
                            "palette_map_preview_img","Conversion non lancée")

    def render_error_map(self):
        self._render_single(self.error_map_canvas,self.error_map_image,
                            "error_map_preview_img","Conversion non lancée")

    def render_compare(self):
        self.compare_canvas.delete("all")
        cw=max(200,self.compare_canvas.winfo_width())
        ch=max(120,self.compare_canvas.winfo_height())
        if self.prepared is None or self.result is None:
            self.compare_canvas.create_text(cw//2,ch//2,text="Conversion requise",fill="#8f98a3")
            return
        half=(cw-30)//2
        left=self.image_to_photo(self.prepared,half-20,ch-45)
        right=self.image_to_photo(self.result.image,half-20,ch-45)
        self.compare_left=left
        self.compare_right=right
        self.compare_canvas.create_text(half//2,15,text="SOURCE PRÉPARÉE",fill="#d9d9d9")
        self.compare_canvas.create_text(half+15+half//2,15,text="DMS-1",fill="#d9d9d9")
        self.compare_canvas.create_image(half//2,ch//2+10,image=left,anchor="center")
        self.compare_canvas.create_image(half+15+half//2,ch//2+10,image=right,anchor="center")
        self.compare_canvas.create_line(half+7,0,half+7,ch,fill="#5b626c")

    # ---------------- DIAGNOSTICS ----------------

    def render_diagnostics(self):
        r=self.result
        if r is None:
            return

        self.card_vars["OUTPUT"].set(f"{r.image.width}×{r.image.height}")
        self.card_vars["COLORS"].set(f"{r.final_colors}")
        self.card_vars["BANKS"].set(f"{len(r.palette_ids)}")
        self.card_vars["UNIQUE"].set(f"{len(r.unique_tiles)}")
        self.card_vars["VRAM"].set(f"{r.vram_bytes} B")
        self.card_vars["ERROR"].set(f"{r.quality_mse:.2f}")

        redundancy=100*r.duplicate_tiles/r.total_tiles if r.total_tiles else 0
        capacity=len(r.palette_ids)*r.colors_per_bank

        usage="\n".join(
            f"  P{pid}: {r.palette_usage.get(pid,0)} tiles"
            + ("  [LOCK]" if pid in self.locked_palettes else "")
            for pid in r.palette_ids
        )

        txt=(
            f"Mode : {self.mode_var.get()}\n"
            f"Banques physiques : {', '.join('P'+str(x) for x in r.palette_ids)}\n"
            f"Couleurs max / banque : {r.colors_per_bank}\n"
            f"Capacité nominale choisie : {capacity} entrées\n\n"
            f"Source RGB333 après resize : {r.source_rgb333_colors} couleurs\n"
            f"Couleurs finales réellement utilisées : {r.final_colors}\n"
            f"Erreur moyenne RGB333 (MSE) : {r.quality_mse:.3f}\n\n"
            f"Cellule analyse/export : {r.tile_size}×{r.tile_size}\n"
            f"Ordre source préservé : {'OUI' if self.resize_var.get()==TILESET_RAW_MODE else 'non'}\n"
            f"Tiles totales : {r.total_tiles}\n"
            f"Tiles uniques : {len(r.unique_tiles)}\n"
            f"Réemplois/doublons : {r.duplicate_tiles} ({redundancy:.1f} %)\n"
            f"Réemplois via flip : {r.flip_reused}\n"
            f"VRAM graphique estimée : {r.vram_bytes} octets\n"
            f"Budget analyse tiles : {r.tile_budget}\n\n"
            f"UTILISATION DES PALETTES\n{usage}\n\n"
            "Map palettes : montre quelle banque sert à chaque cellule.\n"
            "Map erreur : vert = faible erreur, rouge = zone difficile à réduire."
        )
        if r.warnings:
            txt+="\n\nWARNINGS\n"+"\n".join("• "+w for w in r.warnings)
        self._set_text(self.diag_text,txt)

        lines=[]
        for pid,p in zip(r.palette_ids,r.palettes):
            lock=" [LOCK]" if pid in self.locked_palettes else ""
            lines.append(
                f"P{pid}{lock} - {len(p)}/{r.colors_per_bank} - "
                + " ".join(rgb333_hex(c) for c in p)
                + f"\nUsage : {r.palette_usage.get(pid,0)} tiles"
            )
        self._set_text(self.palette_text,"\n\n".join(lines))

    def draw_palettes(self):
        self.palette_canvas.delete("all")
        if self.result is None:
            return
        cw=max(400,self.palette_canvas.winfo_width())
        sw=max(18,min(28,(cw-110)//16))
        y=14
        for pid,pal in zip(self.result.palette_ids,self.result.palettes):
            self.palette_canvas.create_text(
                10,y+12,text=f"P{pid}",anchor="w",fill="#e8e8e8",
                font=("Segoe UI",10,"bold")
            )
            if pid in self.locked_palettes:
                self.palette_canvas.create_text(
                    42,y+12,text="LOCK",anchor="w",fill="#ffd166",
                    font=("Segoe UI",7,"bold")
                )
            for j in range(16):
                x=80+j*sw
                if j<len(pal):
                    rgb=rgb333_to_rgb888(pal[j])
                    fill="#%02x%02x%02x"%rgb
                else:
                    fill="#17191d"
                self.palette_canvas.create_rectangle(
                    x,y,x+sw-2,y+24,fill=fill,outline="#545b65"
                )
            y+=40

    # ---------------- EXPORT ----------------

    def export_png(self):
        if self.result is None:
            messagebox.showinfo("Export","Convertis d'abord l'image.")
            return
        path=filedialog.asksaveasfilename(
            title="Exporter PNG DMS-1",defaultextension=".png",
            initialfile=(Path(self.source.source).stem if self.source else "DMS_IMAGE")+"_DMS.png",
            filetypes=[("PNG","*.png")]
        )
        if path:
            try:
                write_png_rgb(path,self.result.image)
                self.status.configure(text=f"PNG exporté : {Path(path).name}")
            except Exception as e:
                messagebox.showerror("PNG",str(e))

    def export_palette_map_png(self):
        if self.palette_map_image is None:
            return
        path=filedialog.asksaveasfilename(
            title="Exporter map palettes",defaultextension=".png",
            initialfile="DMS_PALETTE_MAP.png",filetypes=[("PNG","*.png")]
        )
        if path:
            write_png_rgb(path,self.palette_map_image)

    def export_error_map_png(self):
        if self.error_map_image is None:
            return
        path=filedialog.asksaveasfilename(
            title="Exporter map erreur",defaultextension=".png",
            initialfile="DMS_ERROR_MAP.png",filetypes=[("PNG","*.png")]
        )
        if path:
            write_png_rgb(path,self.error_map_image)

    def export_dimg_action(self):
        if self.result is None:
            messagebox.showinfo("Export","Convertis d'abord l'image.")
            return
        path=filedialog.asksaveasfilename(
            title="Exporter DIMG V2",defaultextension=".dimg",
            initialfile=(Path(self.source.source).stem if self.source else "DMS_IMAGE")+".dimg",
            filetypes=[("DMS Image Resource","*.dimg")]
        )
        if not path:
            return
        try:
            export_dimg(
                path,self.result,self.mode_var.get(),self.current_settings(),
                Path(self.source.source).name if self.source else ""
            )
            self.status.configure(text=f"DIMG V2 exporté : {Path(path).name}")
            messagebox.showinfo(
                "DIMG V2",
                f"Export terminé.\n\n"
                f"Banques : {' '.join('P'+str(x) for x in self.result.palette_ids)}\n"
                f"Couleurs finales : {self.result.final_colors}\n"
                f"Tiles uniques : {len(self.result.unique_tiles)}"
            )
        except Exception as e:
            messagebox.showerror("DIMG",str(e))

    def export_palette_action(self):
        if self.result is None:
            return
        path=filedialog.asksaveasfilename(
            title="Exporter DPAL",defaultextension=".dpal",
            initialfile="DMS_PALETTES.dpal",
            filetypes=[("DMS Palette","*.dpal")]
        )
        if path:
            try:
                export_dpal(path,self.result.palettes,self.result.palette_ids)
                self.status.configure(text=f"DPAL exporté : {Path(path).name}")
            except Exception as e:
                messagebox.showerror("DPAL",str(e))

    @staticmethod
    def _set_text(widget,text):
        widget.configure(state="normal")
        widget.delete("1.0","end")
        widget.insert("1.0",text)
        widget.configure(state="disabled")


if __name__=="__main__":
    DMSImageConverter().mainloop()
