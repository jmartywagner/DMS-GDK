#!/usr/bin/env python3
"""DMS-1 P0.8 multimode video display processor core.

Frozen modes:
  MODE 0 STANDARD   320x224, 4x16 colours, BG A + BG B + sprites.
  MODE 1 HIGH COLOR 320x224, 8x16 colours, BG A + sprites.
  MODE 2 SCROLL     320x224, 4x16 colours, BG A + BG B + per-line scroll,
                    reduced sprite fetch budget.
  MODE 3 SPRITE     320x224, 4x16 colours, BG A + extended sprite budget.
  MODE 4 LOW RES    256x224, 8x16 colours, BG A + BG B + larger sprite budget.

All modes use the same 512-colour RGB 3:3:3 master space and 4-bpp pixels.
Mode switching is accepted only during VBlank.
"""
from __future__ import annotations

from dataclasses import dataclass

WIDTH = 320
LOW_RES_WIDTH = 256
HEIGHT = 224
VRAM_SIZE = 0x20000  # 128 KiB
CRAM_ENTRIES = 128    # 8 palettes x 16 entries
CRAM_SIZE = CRAM_ENTRIES * 2

MODE_STANDARD = 0
MODE_HIGH_COLOR = 1
MODE_SCROLL = 2
MODE_SPRITE = 3
MODE_LOW_RES = 4
SUPPORTED_MODES = (MODE_STANDARD, MODE_HIGH_COLOR, MODE_SCROLL, MODE_SPRITE, MODE_LOW_RES)

# Host presentation profiles. These are deliberately separate from the five
# frozen hardware video modes above. Register 0x05 selects only the final
# monitor presentation and never changes VDP bandwidth or memory semantics.
PRESENT_RAW = 0
PRESENT_SCANLINES = 1
PRESENT_CRT_SOFT = 2
PRESENT_CRT_SCANLINES = 3
PRESENT_COMPOSITE = 4
PRESENTATION_PROFILES = (PRESENT_RAW, PRESENT_SCANLINES, PRESENT_CRT_SOFT, PRESENT_CRT_SCANLINES, PRESENT_COMPOSITE)

TILE_W = 8
TILE_H = 8
TILE_BYTES = 32  # 8x8 @ 4 bpp
MAX_TILES = 1024
PATTERN_BASE = 0x00000
PATTERN_BYTES = MAX_TILES * TILE_BYTES

MAP_W = 64
MAP_H = 32
MAP_BYTES = MAP_W * MAP_H * 2
BG_A_STANDARD_BASE = 0x08000
BG_B_STANDARD_BASE = 0x09000
SPRITE_TABLE_BASE = 0x0A000
BG_A_HIGH_BASE = 0x0B000
LINE_SCROLL_A_BASE = 0x0C000
LINE_SCROLL_B_BASE = 0x0C200
LINE_SCROLL_BYTES = HEIGHT * 2

MAX_SPRITES = 128
SPRITE_COUNT = MAX_SPRITES  # compatibility name
SPRITE_ENTRY_BYTES = 8

# Background map word:
#  0..9  tile index
# 10..12 palette P0..P7 (masked by current mode)
# 13     priority
# 14     H flip
# 15     V flip
BG_TILE_MASK = 0x03FF
BG_PALETTE_SHIFT = 10
BG_PRIORITY = 0x2000
BG_HFLIP = 0x4000
BG_VFLIP = 0x8000

# Sprite attr word:
#  0..2  palette
#  3     priority
#  4     H flip
#  5     V flip
#  6     16x16 (otherwise 8x8)
SPR_PRIORITY = 0x0008
SPR_HFLIP = 0x0010
SPR_VFLIP = 0x0020
SPR_SIZE16 = 0x0040


@dataclass(frozen=True)
class ModeProfile:
    mode: int
    name: str
    width: int
    palettes: int
    bg_a_base: int
    bg_b_base: int | None
    line_scroll: bool
    sprite_total: int
    sprite_per_scanline: int
    purpose: str


MODE_PROFILES: dict[int, ModeProfile] = {
    MODE_STANDARD: ModeProfile(
        MODE_STANDARD, "STANDARD", WIDTH, 4,
        BG_A_STANDARD_BASE, BG_B_STANDARD_BASE, False,
        80, 20, "general gameplay / two planes",
    ),
    MODE_HIGH_COLOR: ModeProfile(
        MODE_HIGH_COLOR, "HIGH COLOR", WIDTH, 8,
        BG_A_HIGH_BASE, None, False,
        80, 20, "128 colours / one background plane",
    ),
    MODE_SCROLL: ModeProfile(
        MODE_SCROLL, "SCROLL", WIDTH, 4,
        BG_A_STANDARD_BASE, BG_B_STANDARD_BASE, True,
        48, 12, "per-scanline scrolling / reduced sprite bandwidth",
    ),
    MODE_SPRITE: ModeProfile(
        MODE_SPRITE, "SPRITE", WIDTH, 4,
        BG_A_STANDARD_BASE, None, False,
        128, 32, "extended sprite fetch / one background plane",
    ),
    MODE_LOW_RES: ModeProfile(
        MODE_LOW_RES, "LOW RES", LOW_RES_WIDTH, 8,
        BG_A_HIGH_BASE, BG_B_STANDARD_BASE, False,
        96, 24, "256-wide trade for palettes + sprite bandwidth",
    ),
}


def rgb333_to_rgb888(value: int) -> tuple[int, int, int]:
    """Expand 9-bit RGB 3:3:3 to display RGB without inventing extra precision."""
    value &= 0x01FF
    r = (value >> 6) & 7
    g = (value >> 3) & 7
    b = value & 7
    return ((r * 255 + 3) // 7, (g * 255 + 3) // 7, (b * 255 + 3) // 7)


def pack_rgb333(r: int, g: int, b: int) -> int:
    return ((r & 7) << 6) | ((g & 7) << 3) | (b & 7)


def _signed16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


@dataclass
class DmsVdp:
    """Raster VDP model for the five frozen DMS-1 P0.8 video modes."""

    def __post_init__(self) -> None:
        self.vram = bytearray(VRAM_SIZE)
        self.cram = bytearray(CRAM_SIZE)
        self.mode = MODE_STANDARD
        self.backdrop = 0
        self.presentation_profile = PRESENT_RAW
        self.scroll_a_x = 0
        self.scroll_a_y = 0
        self.scroll_b_x = 0
        self.scroll_b_y = 0
        self.mode_write_rejected = False
        self._tile_cache: list[tuple[int, ...] | None] = [None] * MAX_TILES
        self._plane_cache: dict[int, tuple[bytearray, bytearray] | None] = {
            BG_A_STANDARD_BASE: None,
            BG_B_STANDARD_BASE: None,
            BG_A_HIGH_BASE: None,
        }
        # P1.0.8 host transport: track memory writes so the realtime frontend
        # does not serialize the complete 128 KiB VRAM on every 60 Hz frame.
        # These sets are frontend-neutral hardware bookkeeping; they do not
        # change VDP behaviour.
        self._dirty_vram: set[int] = set()
        self._dirty_cram: set[int] = set()

    @property
    def profile(self) -> ModeProfile:
        return MODE_PROFILES[self.mode]

    @property
    def active_width(self) -> int:
        return self.profile.width

    @property
    def palette_count(self) -> int:
        return self.profile.palettes

    @property
    def bg_b_enabled(self) -> bool:
        return self.profile.bg_b_base is not None

    @property
    def line_scroll_enabled(self) -> bool:
        return self.profile.line_scroll

    @property
    def sprite_total_limit(self) -> int:
        return self.profile.sprite_total

    @property
    def sprite_scanline_limit(self) -> int:
        return self.profile.sprite_per_scanline

    def reset_status_latches(self) -> None:
        self.mode_write_rejected = False

    def status(self, vblank: bool) -> int:
        return (1 if vblank else 0) | (2 if self.mode_write_rejected else 0)

    def request_mode(self, mode: int, *, vblank: bool) -> bool:
        """Mode changes are legal only during VBlank."""
        mode &= 0xFF
        if mode not in SUPPORTED_MODES or not vblank:
            self.mode_write_rejected = True
            return False
        self.mode = mode
        return True

    def read_cram8(self, offset: int) -> int:
        return self.cram[offset] if 0 <= offset < CRAM_SIZE else 0xFF

    def write_cram8(self, offset: int, value: int) -> None:
        if 0 <= offset < CRAM_SIZE:
            self.cram[offset] = value & 0xFF
            if offset & 1 == 0:
                self.cram[offset] &= 0x01
            self._dirty_cram.add(offset)

    def cram_word(self, index: int) -> int:
        index &= 0x7F
        off = index * 2
        return ((self.cram[off] << 8) | self.cram[off + 1]) & 0x01FF

    def set_cram_word(self, index: int, value: int) -> None:
        index &= 0x7F
        value &= 0x01FF
        off = index * 2
        self.cram[off] = (value >> 8) & 1
        self.cram[off + 1] = value & 0xFF
        self._dirty_cram.add(off)
        self._dirty_cram.add(off + 1)

    def palette_rgb(self, palette: int, colour: int) -> tuple[int, int, int]:
        palette &= (self.palette_count - 1)
        return rgb333_to_rgb888(self.cram_word(palette * 16 + (colour & 0x0F)))

    def read_vram8(self, offset: int) -> int:
        return self.vram[offset] if 0 <= offset < VRAM_SIZE else 0xFF

    def write_vram8(self, offset: int, value: int) -> None:
        if 0 <= offset < VRAM_SIZE:
            self.vram[offset] = value & 0xFF
            self._dirty_vram.add(offset)
            if PATTERN_BASE <= offset < PATTERN_BYTES:
                self._tile_cache[offset // TILE_BYTES] = None
                for base in self._plane_cache:
                    self._plane_cache[base] = None
            elif BG_A_STANDARD_BASE <= offset < BG_A_STANDARD_BASE + MAP_BYTES:
                self._plane_cache[BG_A_STANDARD_BASE] = None
            elif BG_B_STANDARD_BASE <= offset < BG_B_STANDARD_BASE + MAP_BYTES:
                self._plane_cache[BG_B_STANDARD_BASE] = None
            elif BG_A_HIGH_BASE <= offset < BG_A_HIGH_BASE + MAP_BYTES:
                self._plane_cache[BG_A_HIGH_BASE] = None


    @staticmethod
    def _coalesce_dirty(offsets: set[int]) -> list[tuple[int, int]]:
        """Return inclusive-start/exclusive-end ranges for dirty byte offsets."""
        if not offsets:
            return []
        ordered = sorted(offsets)
        out: list[tuple[int, int]] = []
        start = prev = ordered[0]
        for off in ordered[1:]:
            if off == prev + 1:
                prev = off
                continue
            out.append((start, prev + 1))
            start = prev = off
        out.append((start, prev + 1))
        return out

    def consume_host_dirty(self) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
        """Consume memory dirtiness for an external presentation host.

        The values themselves remain in VRAM/CRAM; only the write markers are
        consumed. NativeHostClient merges these markers with any packet that is
        still waiting in its asynchronous pipe, so dropping an intermediate
        video packet cannot lose a static palette/tile update.
        """
        vr = self._coalesce_dirty(self._dirty_vram)
        cr = self._coalesce_dirty(self._dirty_cram)
        self._dirty_vram.clear()
        self._dirty_cram.clear()
        return vr, cr

    def mark_host_full_dirty(self) -> None:
        self._dirty_vram.update(range(VRAM_SIZE))
        self._dirty_cram.update(range(CRAM_SIZE))

    def _decoded_tile(self, tile: int) -> tuple[int, ...]:
        tile &= BG_TILE_MASK
        cached = self._tile_cache[tile]
        if cached is not None:
            return cached
        off = PATTERN_BASE + tile * TILE_BYTES
        raw = self.vram[off:off + TILE_BYTES]
        pix: list[int] = []
        for value in raw:
            pix.append((value >> 4) & 15)
            pix.append(value & 15)
        decoded = tuple(pix)
        self._tile_cache[tile] = decoded
        return decoded

    def read_reg8(self, offset: int, *, vblank: bool) -> int:
        offset &= 0xFF
        if offset == 0x00:
            return self.status(vblank)
        if offset == 0x02:
            return self.mode
        if offset == 0x04:
            return self.backdrop & 0x7F
        if offset == 0x05:
            return self.presentation_profile
        values = {
            0x06: self.active_width & 0xFF,
            0x07: 1 if self.active_width == LOW_RES_WIDTH else 0,
            0x08: self.sprite_total_limit & 0xFF,
            0x09: self.sprite_scanline_limit & 0xFF,
            0x10: (self.scroll_a_x >> 8) & 0xFF,
            0x11: self.scroll_a_x & 0xFF,
            0x12: (self.scroll_a_y >> 8) & 0xFF,
            0x13: self.scroll_a_y & 0xFF,
            0x14: (self.scroll_b_x >> 8) & 0xFF,
            0x15: self.scroll_b_x & 0xFF,
            0x16: (self.scroll_b_y >> 8) & 0xFF,
            0x17: self.scroll_b_y & 0xFF,
        }
        return values.get(offset, 0)

    def write_reg8(self, offset: int, value: int, *, vblank: bool) -> None:
        offset &= 0xFF
        value &= 0xFF
        if offset == 0x02:
            self.request_mode(value, vblank=vblank)
        elif offset == 0x04:
            self.backdrop = value & 0x7F
        elif offset == 0x05:
            if value in PRESENTATION_PROFILES:
                self.presentation_profile = value
        elif offset == 0x10:
            self.scroll_a_x = ((value << 8) | (self.scroll_a_x & 0xFF)) & 0x01FF
        elif offset == 0x11:
            self.scroll_a_x = ((self.scroll_a_x & 0x0100) | value) & 0x01FF
        elif offset == 0x12:
            self.scroll_a_y = ((value << 8) | (self.scroll_a_y & 0xFF)) & 0x00FF
        elif offset == 0x13:
            self.scroll_a_y = value & 0xFF
        elif offset == 0x14:
            self.scroll_b_x = ((value << 8) | (self.scroll_b_x & 0xFF)) & 0x01FF
        elif offset == 0x15:
            self.scroll_b_x = ((self.scroll_b_x & 0x0100) | value) & 0x01FF
        elif offset == 0x16:
            self.scroll_b_y = ((value << 8) | (self.scroll_b_y & 0xFF)) & 0x00FF
        elif offset == 0x17:
            self.scroll_b_y = value & 0xFF

    def _plane_pixels(self, base: int) -> tuple[bytearray, bytearray]:
        cached = self._plane_cache.get(base)
        if cached is not None:
            return cached
        width = MAP_W * TILE_W
        height = MAP_H * TILE_H
        indices = bytearray(width * height)
        priorities = bytearray(width * height)
        vram = self.vram
        for cy in range(MAP_H):
            for cx in range(MAP_W):
                moff = base + ((cy * MAP_W + cx) * 2)
                word = (vram[moff] << 8) | vram[moff + 1]
                palette = (word >> BG_PALETTE_SHIFT) & 7
                pri = 1 if word & BG_PRIORITY else 0
                hflip = bool(word & BG_HFLIP)
                vflip = bool(word & BG_VFLIP)
                tile = self._decoded_tile(word & BG_TILE_MASK)
                x0 = cx * 8
                y0 = cy * 8
                for py in range(8):
                    sy = 7 - py if vflip else py
                    row = (y0 + py) * width + x0
                    srcrow = sy * 8
                    for px in range(8):
                        sx = 7 - px if hflip else px
                        colour = tile[srcrow + sx]
                        if colour:
                            pos = row + px
                            indices[pos] = (palette << 4) | colour
                            priorities[pos] = pri
        result = (indices, priorities)
        self._plane_cache[base] = result
        return result

    def _line_scroll_offset(self, base: int, y: int) -> int:
        off = base + ((y % HEIGHT) * 2)
        return _signed16((self.vram[off] << 8) | self.vram[off + 1])


    def debug_sprite_metrics(self) -> dict:
        """Inspect the hardware sprite table without changing VDP state.

        Counts are fetch-oriented: each visible vertical slice of a programmed
        sprite consumes one sprite slot on the corresponding scanline even if
        its pixels are transparent. This matches the limit enforced by render_rgb().
        """
        profile = self.profile
        scanline_counts = [0] * HEIGHT
        active = 0
        entries = []
        vram = self.vram
        for index in range(MAX_SPRITES):
            off = SPRITE_TABLE_BASE + index * SPRITE_ENTRY_BYTES
            sy = ((vram[off] << 8) | vram[off + 1]) & 0x01FF
            sx = ((vram[off + 2] << 8) | vram[off + 3]) & 0x01FF
            tile = ((vram[off + 4] << 8) | vram[off + 5]) & BG_TILE_MASK
            attr = (vram[off + 6] << 8) | vram[off + 7]
            if sx == 0x1FF and sy == 0x1FF:
                continue
            active += 1
            size = 16 if attr & SPR_SIZE16 else 8
            for oy in range(size):
                y = sy + oy
                if 0 <= y < HEIGHT:
                    scanline_counts[y] += 1
            entries.append({
                "index": index, "x": sx, "y": sy, "tile": tile, "attr": attr,
                "size": size, "palette": attr & 7,
                "priority": 1 if attr & SPR_PRIORITY else 0,
            })
        peak = max(scanline_counts) if scanline_counts else 0
        over = [i for i, count in enumerate(scanline_counts) if count > profile.sprite_per_scanline]
        worst = sorted(((count, y) for y, count in enumerate(scanline_counts)), reverse=True)[:12]
        return {
            "active": active,
            "total_limit": profile.sprite_total,
            "scanline_limit": profile.sprite_per_scanline,
            "peak_scanline": peak,
            "overflow_lines": over,
            "overflow_count": len(over),
            "scanlines": scanline_counts,
            "worst_scanlines": [(y, count) for count, y in worst if count],
            "entries": entries,
        }

    def render_rgb(self) -> bytes:
        """Render one RGB frame at the current hardware mode resolution."""
        profile = self.profile
        width = profile.width
        color_bytes = [bytes(rgb333_to_rgb888(self.cram_word(i))) for i in range(CRAM_ENTRIES)]
        backdrop = color_bytes[self.backdrop & 0x7F]
        out = bytearray(backdrop * (width * HEIGHT))
        owner_pri = bytearray(width * HEIGHT)
        owner_layer = bytearray(width * HEIGHT)
        palette_mask = profile.palettes - 1
        world_w = MAP_W * 8
        world_h = MAP_H * 8

        def render_plane(base: int, sx: int, sy: int, layer: int, line_table: int | None = None) -> None:
            indices, priorities = self._plane_pixels(base)
            for y in range(HEIGHT):
                wy = (y + sy) & (world_h - 1)
                wrow = wy * world_w
                srow = y * width
                line_x = sx + (self._line_scroll_offset(line_table, y) if line_table is not None else 0)
                for x in range(width):
                    wx = (x + line_x) & (world_w - 1)
                    wpos = wrow + wx
                    encoded = indices[wpos]
                    if not encoded:
                        continue
                    pos = srow + x
                    pri = priorities[wpos]
                    if pri < owner_pri[pos] or (pri == owner_pri[pos] and layer < owner_layer[pos]):
                        continue
                    owner_pri[pos] = pri
                    owner_layer[pos] = layer
                    palette = ((encoded >> 4) & 7) & palette_mask
                    colour = encoded & 15
                    poff = pos * 3
                    out[poff:poff + 3] = color_bytes[palette * 16 + colour]

        if profile.bg_b_base is not None:
            render_plane(
                profile.bg_b_base, self.scroll_b_x, self.scroll_b_y, 0,
                LINE_SCROLL_B_BASE if profile.line_scroll else None,
            )
        render_plane(
            profile.bg_a_base, self.scroll_a_x, self.scroll_a_y, 1,
            LINE_SCROLL_A_BASE if profile.line_scroll else None,
        )

        # Sprite fetch budget is mode-dependent and per scanline, as on a real
        # raster VDP. Later sprites on an overloaded line disappear.
        scanline_counts = [0] * HEIGHT
        vram = self.vram
        total = min(profile.sprite_total, MAX_SPRITES)
        for index in range(total):
            off = SPRITE_TABLE_BASE + index * SPRITE_ENTRY_BYTES
            sy = ((vram[off] << 8) | vram[off + 1]) & 0x01FF
            sx = ((vram[off + 2] << 8) | vram[off + 3]) & 0x01FF
            tile = ((vram[off + 4] << 8) | vram[off + 5]) & BG_TILE_MASK
            attr = (vram[off + 6] << 8) | vram[off + 7]
            if sx == 0x1FF and sy == 0x1FF:
                continue
            size = 16 if attr & SPR_SIZE16 else 8
            palette = (attr & 7) & palette_mask
            pri = 1 if attr & SPR_PRIORITY else 0
            hflip = bool(attr & SPR_HFLIP)
            vflip = bool(attr & SPR_VFLIP)
            for oy in range(size):
                y = sy + oy
                if y >= HEIGHT:
                    continue
                if scanline_counts[y] >= profile.sprite_per_scanline:
                    continue
                scanline_counts[y] += 1
                ly = size - 1 - oy if vflip else oy
                row = y * width
                for ox in range(size):
                    x = sx + ox
                    if x >= width:
                        continue
                    lx = size - 1 - ox if hflip else ox
                    if size == 16:
                        sub_x, px = divmod(lx, 8)
                        sub_y, py = divmod(ly, 8)
                        subtile = tile + sub_y * 2 + sub_x
                    else:
                        subtile, px, py = tile, lx, ly
                    colour = self._decoded_tile(subtile)[(py << 3) + px]
                    if colour == 0:
                        continue
                    pos = row + x
                    if pri < owner_pri[pos] or (pri == owner_pri[pos] and 2 < owner_layer[pos]):
                        continue
                    owner_pri[pos] = pri
                    owner_layer[pos] = 2
                    poff = pos * 3
                    out[poff:poff + 3] = color_bytes[palette * 16 + colour]
        return bytes(out)
