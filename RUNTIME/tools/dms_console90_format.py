#!/usr/bin/env python3
"""DMC2 cartridge container for the DMS-1 Console90 CPU branch."""
from __future__ import annotations

from dataclasses import dataclass
import json
import struct
import zlib
from pathlib import Path

MAGIC = b"DMC2"
VERSION = 1
HEADER_SIZE = 32
DIR_ENTRY_SIZE = 16
ALIGNMENT = 16


class Dmc2Error(RuntimeError):
    pass


@dataclass(frozen=True)
class Dmc2Chunk:
    kind: bytes
    offset: int
    size: int
    crc32: int
    data: bytes


@dataclass(frozen=True)
class Dmc2Image:
    chunks: tuple[Dmc2Chunk, ...]
    flags: int = 0

    def chunk(self, kind: bytes) -> bytes:
        for chunk in self.chunks:
            if chunk.kind == kind:
                return chunk.data
        raise Dmc2Error(f"chunk {kind!r} absent")

    def optional_chunk(self, kind: bytes) -> bytes | None:
        for chunk in self.chunks:
            if chunk.kind == kind:
                return chunk.data
        return None

    @property
    def metadata(self) -> dict:
        raw = self.optional_chunk(b"META")
        if raw is None:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise Dmc2Error(f"META invalide: {exc}") from exc


def _align(value: int, alignment: int = ALIGNMENT) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def build_image(chunks: list[tuple[bytes, bytes]], *, flags: int = 0) -> bytes:
    if not chunks:
        raise Dmc2Error("aucun chunk DMC2")
    if len(chunks) > 32:
        raise Dmc2Error("trop de chunks DMC2")
    seen: set[bytes] = set()
    normalized: list[tuple[bytes, bytes]] = []
    for kind, data in chunks:
        if not isinstance(kind, (bytes, bytearray)) or len(kind) != 4:
            raise Dmc2Error("type de chunk DMC2 doit faire 4 octets")
        kind = bytes(kind)
        if kind in seen:
            raise Dmc2Error(f"chunk DMC2 duplique: {kind!r}")
        seen.add(kind)
        normalized.append((kind, bytes(data)))

    dir_offset = HEADER_SIZE
    payload_offset = _align(dir_offset + len(normalized) * DIR_ENTRY_SIZE)
    entries: list[tuple[bytes, int, int, int]] = []
    cursor = payload_offset
    for kind, data in normalized:
        cursor = _align(cursor)
        entries.append((kind, cursor, len(data), zlib.crc32(data) & 0xFFFFFFFF))
        cursor += len(data)
    total_size = _align(cursor)

    output = bytearray(total_size)
    struct.pack_into(">4sHHIIHH12s", output, 0,
                     MAGIC, VERSION, HEADER_SIZE, total_size, dir_offset,
                     len(entries), flags & 0xFFFF, b"\0" * 12)
    for index, (kind, offset, size, crc) in enumerate(entries):
        struct.pack_into(">4sIII", output, dir_offset + index * DIR_ENTRY_SIZE,
                         kind, offset, size, crc)
    for (kind, data), (_, offset, size, _) in zip(normalized, entries):
        assert size == len(data)
        output[offset:offset + size] = data
    return bytes(output)


def parse_image(data: bytes) -> Dmc2Image:
    if len(data) < HEADER_SIZE:
        raise Dmc2Error("DMC2 tronquee")
    magic, version, header_size, total_size, dir_offset, count, flags, reserved = \
        struct.unpack_from(">4sHHIIHH12s", data, 0)
    if magic != MAGIC:
        raise Dmc2Error("magic DMC2 invalide")
    if version != VERSION:
        raise Dmc2Error(f"version DMC2 non supportee: {version}")
    if header_size != HEADER_SIZE or dir_offset != HEADER_SIZE:
        raise Dmc2Error("header/repertoire DMC2 invalide")
    if total_size != len(data):
        raise Dmc2Error("taille totale DMC2 incoherente")
    if reserved != b"\0" * 12:
        raise Dmc2Error("zone reservee DMC2 non nulle")
    if count == 0 or count > 32:
        raise Dmc2Error("nombre de chunks DMC2 invalide")
    dir_end = dir_offset + count * DIR_ENTRY_SIZE
    if dir_end > len(data):
        raise Dmc2Error("repertoire DMC2 hors image")

    chunks: list[Dmc2Chunk] = []
    seen: set[bytes] = set()
    occupied: list[tuple[int, int]] = []
    for index in range(count):
        kind, offset, size, crc = struct.unpack_from(">4sIII", data,
                                                     dir_offset + index * DIR_ENTRY_SIZE)
        if kind in seen:
            raise Dmc2Error(f"chunk duplique: {kind!r}")
        seen.add(kind)
        if offset % ALIGNMENT:
            raise Dmc2Error(f"chunk {kind!r} non aligne")
        end = offset + size
        if offset < _align(dir_end) or end > len(data) or end < offset:
            raise Dmc2Error(f"chunk {kind!r} hors image")
        for other_start, other_end in occupied:
            if offset < other_end and end > other_start:
                raise Dmc2Error(f"chunk {kind!r} chevauche un autre chunk")
        occupied.append((offset, end))
        payload = bytes(data[offset:end])
        actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
        if actual_crc != crc:
            raise Dmc2Error(f"CRC invalide pour {kind!r}")
        chunks.append(Dmc2Chunk(kind, offset, size, crc, payload))

    required = {b"M68K", b"Z80 ", b"META"}
    missing = required - seen
    if missing:
        raise Dmc2Error(f"chunks DMC2 obligatoires absents: {sorted(missing)!r}")
    return Dmc2Image(tuple(chunks), flags)


def load_image(path: str | Path) -> Dmc2Image:
    return parse_image(Path(path).read_bytes())


def save_image(path: str | Path, chunks: list[tuple[bytes, bytes]], *, flags: int = 0) -> None:
    Path(path).write_bytes(build_image(chunks, flags=flags))
