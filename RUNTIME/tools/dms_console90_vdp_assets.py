#!/usr/bin/env python3
"""Small internal P0.8 VDP test assets.

These are deliberately engineering graphics, not the art direction of a future
DMS-1 game. Their purpose is to prove colour, planes, scrolling and sprites.
"""
from __future__ import annotations

from dms_console90_vdp import (
    BG_A_STANDARD_BASE, BG_B_STANDARD_BASE, BG_A_HIGH_BASE,
    MAP_W, MAP_H, TILE_BYTES, BG_PALETTE_SHIFT, BG_PRIORITY,
    SPRITE_TABLE_BASE, SPR_SIZE16, LINE_SCROLL_A_BASE, LINE_SCROLL_B_BASE,
    MAX_SPRITES, pack_rgb333,
)


def _pack_tile(pixels: list[int]) -> bytes:
    if len(pixels) != 64:
        raise ValueError("tile 8x8 attendu")
    out = bytearray()
    for i in range(0, 64, 2):
        out.append(((pixels[i] & 15) << 4) | (pixels[i + 1] & 15))
    return bytes(out)


def build_tiles(player_tiles: bytes | None = None) -> bytes:
    tiles: list[bytes] = []
    # 0 transparent
    tiles.append(_pack_tile([0] * 64))
    # 1 full 1..15 ramp: proves all 16 entries of a palette are real.
    tiles.append(_pack_tile([1 + ((x + y * 3) % 15) for y in range(8) for x in range(8)]))
    # 2 checker
    tiles.append(_pack_tile([2 if ((x // 2 + y // 2) & 1) else 5 for y in range(8) for x in range(8)]))
    # 3 brick
    tiles.append(_pack_tile([7 if y in (0, 4) or (x + (4 if y >= 4 else 0)) % 8 == 0 else 3 for y in range(8) for x in range(8)]))
    # 4 star/dot field, transparent around the points.
    tiles.append(_pack_tile([15 if (x, y) in ((1,1),(6,3),(3,6)) else 0 for y in range(8) for x in range(8)]))
    # 5 diagonal bands
    tiles.append(_pack_tile([1 + ((x + y) % 7) for y in range(8) for x in range(8)]))
    # 6 frame
    tiles.append(_pack_tile([12 if x in (0,7) or y in (0,7) else 0 for y in range(8) for x in range(8)]))
    # 7 ground stripes
    tiles.append(_pack_tile([10 if y in (1,5) else 4 + ((x // 2) & 3) for y in range(8) for x in range(8)]))

    # 8..11: four 8x8 pieces of a 16x16 player diamond.
    big = []
    for y in range(16):
        for x in range(16):
            d = abs(x - 7.5) + abs(y - 7.5)
            if d < 4.5: c = 15
            elif d < 6.5: c = 11
            elif d < 8.0: c = 6
            else: c = 0
            big.append(c)
    built_player = []
    for ty in range(2):
        for tx in range(2):
            pix = []
            for y in range(8):
                for x in range(8):
                    pix.append(big[(ty * 8 + y) * 16 + tx * 8 + x])
            built_player.append(_pack_tile(pix))
    if player_tiles is not None:
        if len(player_tiles) != 4 * TILE_BYTES:
            raise ValueError("player_tiles doit contenir 4 tiles 8x8 4bpp")
        built_player = [player_tiles[i:i+TILE_BYTES] for i in range(0, len(player_tiles), TILE_BYTES)]
    tiles.extend(built_player)
    return b"".join(tiles)


def _word(tile: int, palette: int, *, priority: bool = False) -> int:
    return (tile & 0x3FF) | ((palette & 7) << BG_PALETTE_SHIFT) | (BG_PRIORITY if priority else 0)


def build_standard_a_map() -> bytes:
    out = bytearray()
    for y in range(MAP_H):
        for x in range(MAP_W):
            if y >= 21:
                word = _word(7 if y & 1 else 3, 3, priority=(y == 21))
            elif (x + y * 2) % 17 == 0:
                word = _word(6, 2)
            else:
                word = _word(0, 0)
            out += word.to_bytes(2, "big")
    return bytes(out)


def build_standard_b_map() -> bytes:
    out = bytearray()
    for y in range(MAP_H):
        for x in range(MAP_W):
            tile = 4 if ((x * 5 + y * 3) % 11 == 0) else (2 if y > 14 else 5)
            palette = (x // 12 + y // 8) & 1
            out += _word(tile, palette).to_bytes(2, "big")
    return bytes(out)


def build_high_map() -> bytes:
    out = bytearray()
    for y in range(MAP_H):
        for x in range(MAP_W):
            palette = ((x // 8) + (y // 4)) & 7
            tile = 1 if ((x + y) & 1) else 5
            out += _word(tile, palette, priority=((x // 4) & 1) == 0).to_bytes(2, "big")
    return bytes(out)


def build_cram(palette2: list[int] | None = None) -> bytes:
    out = bytearray()
    # 128 distinct-ish entries in the RGB333 cube. n*37 is a permutation
    # modulo 512 because 37 and 512 are coprime. Entry 0 is forced black.
    for n in range(128):
        value = 0 if n == 0 else (n * 37) & 0x1FF
        if palette2 is not None and 32 <= n < 48:
            if len(palette2) != 16:
                raise ValueError("palette2 doit contenir 16 couleurs RGB333")
            value = palette2[n - 32] & 0x01FF
        out += value.to_bytes(2, "big")
    return bytes(out)


def build_sprite_table(x: int = 152, y: int = 96) -> bytes:
    table = bytearray([0xFF] * (MAX_SPRITES * 8))
    # sprite 0: 16x16 made from tiles 8..11, palette 2, high priority.
    table[0:2] = (y & 0x1FF).to_bytes(2, "big")
    table[2:4] = (x & 0x1FF).to_bytes(2, "big")
    table[4:6] = (8).to_bytes(2, "big")
    table[6:8] = (2 | 0x0008 | SPR_SIZE16).to_bytes(2, "big")

    # Entries 80..127 deliberately exist only to reveal the extended sprite
    # fetch budgets of LOW RES (80..95) and SPRITE mode (80..127). Standard
    # and High Color stop before these entries, so their scene stays clean.
    for index in range(80, MAX_SPRITES):
        col = (index - 80) % 12
        row = (index - 80) // 12
        sx = 16 + col * 24
        sy = 18 + row * 28
        off = index * 8
        table[off:off+2] = (sy & 0x1FF).to_bytes(2, "big")
        table[off+2:off+4] = (sx & 0x1FF).to_bytes(2, "big")
        table[off+4:off+6] = (6).to_bytes(2, "big")
        table[off+6:off+8] = ((index & 3) | 0x0008).to_bytes(2, "big")
    return bytes(table)


def build_line_scroll_tables() -> tuple[bytes, bytes]:
    """Static per-scanline offsets used by MODE 2 SCROLL.

    The global A/B scroll values still move every frame; these tables add a
    second, scanline-local displacement, proving that the VDP performs fetch
    address changes inside the frame rather than only whole-plane scrolling.
    """
    a = bytearray()
    b = bytearray()
    for y in range(224):
        band = (y // 16) & 7
        # Alternating positive/negative bands. Values are signed 16-bit pixels.
        av = (band * 3) if (band & 1) == 0 else -(band * 2)
        bv = -(band * 2) if (band & 1) == 0 else (band * 1)
        a += (av & 0xFFFF).to_bytes(2, "big")
        b += (bv & 0xFFFF).to_bytes(2, "big")
    return bytes(a), bytes(b)


def vram_initial_writes(player_tiles: bytes | None = None) -> list[tuple[int, int]]:
    """Return (VRAM offset, byte) pairs written by the 68000 boot ROM."""
    line_a, line_b = build_line_scroll_tables()
    regions = [
        (0x00000, build_tiles(player_tiles)),
        (BG_A_STANDARD_BASE, build_standard_a_map()),
        (BG_B_STANDARD_BASE, build_standard_b_map()),
        (BG_A_HIGH_BASE, build_high_map()),
        (SPRITE_TABLE_BASE, build_sprite_table()),
        (LINE_SCROLL_A_BASE, line_a),
        (LINE_SCROLL_B_BASE, line_b),
    ]
    writes: list[tuple[int, int]] = []
    for base, data in regions:
        writes.extend((base + i, b) for i, b in enumerate(data))
    return writes
