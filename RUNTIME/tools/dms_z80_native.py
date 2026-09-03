#!/usr/bin/env python3
"""DMS-1 P0.6 native Z80 music-driver compiler and trace runner.

This module is deliberately separate from the legacy DSEQ renderer.  It:
  1. resolves a DMR/DSEQ program into chronological DMS-1 MMIO writes,
  2. expands ADPCM PLAY/STOP opcodes into the actual audio-register writes,
  3. packs a tiny banked command stream for a real Z80 firmware,
  4. executes the genuine Z80 opcodes in the Console90 Z80 core at 4 MHz,
  5. records the writes that the Z80 actually emits, with their 24 MHz times.

The resulting ZTR1 trace can be rendered by dms1emu without executing DSEQ.
This gives us an auditable transition path from the old sequencer to the native
sound CPU while keeping the proven OPZ/SSG/ADPCM/output-stage implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import struct
from pathlib import Path
from typing import Iterable

from dms_console90_cpu import Z80
from dms_console90_firmware import build_z80_native_driver

SYSTEM_CLOCK = 24_000_000
Z80_CLOCK = 4_000_000
MASTER_PER_Z80_T = SYSTEM_CLOCK // Z80_CLOCK  # 6
ROM_WINDOW = 0x8000
BANK_BYTES = 0x8000
RAM_BYTES = 0x2000

MAIL_CMD = 0x2000
MAIL_STATUS = 0x2002
AUDIO_ADDR_HI = 0x4000
AUDIO_ADDR_LO = 0x4001
AUDIO_DATA = 0x4002
TIMER_3 = 0x4010
TIMER_2 = 0x4011
TIMER_1 = 0x4012
TIMER_0 = 0x4013
TIMER_CTRL = 0x4014
SONG_RESET = 0x4015
BANKREG = 0x6000
BANKREG_HI = 0x6001

OP_END = 0x00
OP_WAIT_ABS32 = 0x01
OP_WR8 = 0x10
OP_BANK16 = 0x7E
OP_BANK = 0x7F


class NativeDriverError(RuntimeError):
    pass


@dataclass(frozen=True)
class SampleEntry:
    sample_id: int
    codec: int
    start_page: int
    end_page: int


@dataclass(frozen=True)
class ScheduledWrite:
    cycle: int
    address: int
    data: int


@dataclass(frozen=True)
class DriverResult:
    events: tuple[ScheduledWrite, ...]
    reference_events: tuple[ScheduledWrite, ...]
    halt_cycle: int
    reference_halt_cycle: int
    z80_cycles: int
    stream_bytes: int
    banks: int
    missed_deadlines: int
    max_lateness_cycles: int
    mean_lateness_cycles: float


def _be16(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(data):
        raise NativeDriverError("lecture DMR 16 bits hors limites")
    return struct.unpack_from(">H", data, offset)[0]


def _be32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise NativeDriverError("lecture DMR 32 bits hors limites")
    return struct.unpack_from(">I", data, offset)[0]


def _chunks(data: bytes) -> dict[bytes, tuple[int, int]]:
    if len(data) < 64 or data[:4] != b"DMR0" or data[0x10:0x14] != b"DMS1":
        raise NativeDriverError("ROM DMR/DMS1 invalide")
    if _be32(data, 0x24) != SYSTEM_CLOCK:
        raise NativeDriverError("DMR hors timebase 24 MHz")
    directory = _be32(data, 0x18)
    count = _be16(data, 0x1C)
    entry_size = _be16(data, 0x1E)
    if entry_size != 16:
        raise NativeDriverError("repertoire DMR incompatible")
    result: dict[bytes, tuple[int, int]] = {}
    for index in range(count):
        pos = directory + index * 16
        if pos + 16 > len(data):
            raise NativeDriverError("repertoire DMR tronque")
        kind, offset, size, _flags = struct.unpack_from(">4sIII", data, pos)
        if offset + size > len(data):
            raise NativeDriverError(f"chunk {kind!r} hors ROM")
        result[kind] = (offset, size)
    if b"CODE" not in result:
        raise NativeDriverError("chunk CODE absent")
    return result


def _samples(data: bytes, chunks: dict[bytes, tuple[int, int]]) -> dict[int, SampleEntry]:
    result: dict[int, SampleEntry] = {}
    entry = chunks.get(b"SDIR")
    if entry is None:
        return result
    offset, size = entry
    if size % 16:
        raise NativeDriverError("SDIR non multiple de 16")
    for pos in range(offset, offset + size, 16):
        sample_id, codec, _flags, start, end, _rate, _level, _pan, _root, _fine = (
            struct.unpack_from(">HBBHHIBBBb", data, pos)
        )
        result[sample_id] = SampleEntry(sample_id, codec, start, end)
    return result


def _read_uleb(data: bytes, cursor: int, end: int) -> tuple[int, int]:
    value = 0
    shift = 0
    for _ in range(10):
        if cursor >= end:
            raise NativeDriverError("ULEB128 DSEQ tronque")
        byte = data[cursor]
        cursor += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, cursor
        shift += 7
    raise NativeDriverError("ULEB128 DSEQ invalide")


def _adpcm_a_writes(sample: SampleEntry, level: int, pan: int, page_base: int = 0) -> list[tuple[int, int]]:
    if sample.codec != 1:
        raise NativeDriverError("PLAY_A cible un sample non ADPCM-A")
    start_page = sample.start_page + int(page_base)
    end_page = sample.end_page + int(page_base)
    if end_page > 0xFFFF:
        raise NativeDriverError("ADPCM-A rebased pages exceed 16-bit sample bus")
    return [
        (0x0120, 0x02),
        (0x0121, pan),
        (0x0122, level),
        (0x0124, start_page & 0xFF),
        (0x0125, (start_page >> 8) & 0xFF),
        (0x0126, end_page & 0xFF),
        (0x0127, (end_page >> 8) & 0xFF),
        (0x0120, 0x01),
    ]


def _adpcm_b_writes(sample: SampleEntry, delta_n: int, level: int,
                    pan: int, flags: int, page_base: int = 0) -> list[tuple[int, int]]:
    if sample.codec != 2 or delta_n == 0:
        raise NativeDriverError("PLAY_B cible un sample invalide")
    start_page = sample.start_page + int(page_base)
    end_page = sample.end_page + int(page_base)
    if end_page > 0xFFFF:
        raise NativeDriverError("ADPCM-B rebased pages exceed 16-bit sample bus")
    return [
        (0x0140, 0x01),
        (0x0141, pan),
        (0x0142, start_page & 0xFF),
        (0x0143, (start_page >> 8) & 0xFF),
        (0x0144, end_page & 0xFF),
        (0x0145, (end_page >> 8) & 0xFF),
        (0x0149, delta_n & 0xFF),
        (0x014A, (delta_n >> 8) & 0xFF),
        (0x014B, level),
        (0x0140, 0x80 | (0x10 if flags & 1 else 0x00)),
    ]


def flatten_dmr(data: bytes) -> tuple[list[ScheduledWrite], list[int], int]:
    """Resolve DSEQ control flow to writes and absolute wait deadlines.

    Returns (writes, wait_targets, halt_cycle).  wait_targets contains every
    positive DSEQ WAIT endpoint in execution order; writes keep the exact DSEQ
    cycle at which they were logically issued before Z80 execution cost.
    """
    chunks = _chunks(data)
    samples = _samples(data, chunks)
    code_offset, code_size = chunks[b"CODE"]
    code_end = code_offset + code_size
    cursor = _be32(data, 0x20)
    if not code_offset <= cursor < code_end:
        raise NativeDriverError("entrypoint DMR hors CODE")

    cycle = 0
    writes: list[ScheduledWrite] = []
    waits: list[int] = []
    loops: dict[int, tuple[int, int]] = {}
    budget = 2_000_000

    def add(address: int, value: int) -> None:
        if not 0 <= address <= 0x01FF:
            raise NativeDriverError(f"MMIO DMS hors plage: ${address:04X}")
        writes.append(ScheduledWrite(cycle, address, value & 0xFF))

    while budget:
        budget -= 1
        if not code_offset <= cursor < code_end:
            raise NativeDriverError("execution DSEQ hors CODE")
        instruction_pc = cursor
        opcode = data[cursor]
        cursor += 1
        if opcode == 0x00:
            return writes, waits, cycle
        if opcode == 0x01:
            duration, cursor = _read_uleb(data, cursor, code_end)
            cycle += duration
            if duration:
                if cycle > 0xFFFFFFFF:
                    raise NativeDriverError("P0.6 stream limite a 32 bits de cycles chanson")
                waits.append(cycle)
            continue
        if opcode == 0x10:
            if cursor + 3 > code_end:
                raise NativeDriverError("WR8 tronque")
            add(_be16(data, cursor), data[cursor + 2])
            cursor += 3
            continue
        if opcode == 0x11:
            if cursor + 3 > code_end:
                raise NativeDriverError("WRN tronque")
            address = _be16(data, cursor)
            length = data[cursor + 2]
            cursor += 3
            if cursor + length > code_end or address + length > 0x10000:
                raise NativeDriverError("WRN hors limites")
            for index in range(length):
                add(address + index, data[cursor + index])
            cursor += length
            continue
        if opcode == 0x20:
            if cursor + 4 > code_end:
                raise NativeDriverError("PLAY_A tronque")
            sample_id = _be16(data, cursor)
            level = data[cursor + 2]
            pan = data[cursor + 3]
            cursor += 4
            sample = samples.get(sample_id)
            if sample is None:
                raise NativeDriverError(f"sample A {sample_id} absent")
            for address, value in _adpcm_a_writes(sample, level, pan):
                add(address, value)
            continue
        if opcode == 0x21:
            add(0x0120, 0x02)
            continue
        if opcode == 0x22:
            if cursor + 7 > code_end:
                raise NativeDriverError("PLAY_B tronque")
            sample_id = _be16(data, cursor)
            delta_n = _be16(data, cursor + 2)
            level = data[cursor + 4]
            pan = data[cursor + 5]
            flags = data[cursor + 6]
            cursor += 7
            sample = samples.get(sample_id)
            if sample is None:
                raise NativeDriverError(f"sample B {sample_id} absent")
            for address, value in _adpcm_b_writes(sample, delta_n, level, pan, flags):
                add(address, value)
            continue
        if opcode == 0x23:
            add(0x0140, 0x00)
            continue
        if opcode == 0x30:
            if cursor + 4 > code_end:
                raise NativeDriverError("JUMP tronque")
            target = _be32(data, cursor)
            if not code_offset <= target < code_end:
                raise NativeDriverError("JUMP hors CODE")
            cursor = target
            continue
        if opcode == 0x31:
            if cursor + 7 > code_end:
                raise NativeDriverError("LOOP tronque")
            slot = data[cursor]
            count = _be16(data, cursor + 1)
            target = _be32(data, cursor + 3)
            cursor += 7
            if slot >= 8 or count == 0 or not code_offset <= target < code_end:
                raise NativeDriverError("LOOP invalide")
            state = loops.get(slot)
            if state is None or state[0] != instruction_pc:
                state = (instruction_pc, count)
            remaining = state[1]
            if remaining > 1:
                loops[slot] = (instruction_pc, remaining - 1)
                cursor = target
            else:
                loops.pop(slot, None)
            continue
        raise NativeDriverError(f"opcode DSEQ inconnu ${opcode:02X} a ${instruction_pc:08X}")
    raise NativeDriverError("budget DSEQ depasse")


def build_native_commands(data: bytes, sample_page_base: int = 0) -> tuple[list[bytes], list[ScheduledWrite], int]:
    """Compile a DMR to simple native commands while preserving DSEQ order."""
    # Re-run the DSEQ in a single pass so waits and writes stay interleaved.
    chunks = _chunks(data)
    samples = _samples(data, chunks)
    code_offset, code_size = chunks[b"CODE"]
    code_end = code_offset + code_size
    cursor = _be32(data, 0x20)
    cycle = 0
    commands: list[bytes] = []
    reference: list[ScheduledWrite] = []
    loops: dict[int, tuple[int, int]] = {}
    budget = 2_000_000

    def wr(address: int, value: int) -> None:
        reference.append(ScheduledWrite(cycle, address, value & 0xFF))
        commands.append(bytes((OP_WR8, (address >> 8) & 0xFF, address & 0xFF, value & 0xFF)))

    while budget:
        budget -= 1
        if not code_offset <= cursor < code_end:
            raise NativeDriverError("execution DSEQ hors CODE")
        instruction_pc = cursor
        opcode = data[cursor]
        cursor += 1
        if opcode == 0x00:
            commands.append(bytes((OP_END,)))
            return commands, reference, cycle
        if opcode == 0x01:
            duration, cursor = _read_uleb(data, cursor, code_end)
            cycle += duration
            if duration:
                if cycle > 0xFFFFFFFF:
                    raise NativeDriverError("cycle chanson > 32 bits")
                commands.append(bytes((OP_WAIT_ABS32,)) + cycle.to_bytes(4, "big"))
            continue
        if opcode == 0x10:
            address = _be16(data, cursor); value = data[cursor + 2]; cursor += 3
            wr(address, value); continue
        if opcode == 0x11:
            address = _be16(data, cursor); length = data[cursor + 2]; cursor += 3
            for index in range(length): wr(address + index, data[cursor + index])
            cursor += length; continue
        if opcode == 0x20:
            sample_id = _be16(data, cursor); level=data[cursor+2]; pan=data[cursor+3]; cursor += 4
            sample=samples.get(sample_id)
            if sample is None: raise NativeDriverError(f"sample A {sample_id} absent")
            for address,value in _adpcm_a_writes(sample,level,pan,sample_page_base): wr(address,value)
            continue
        if opcode == 0x21:
            wr(0x0120,0x02); continue
        if opcode == 0x22:
            sample_id=_be16(data,cursor); delta=_be16(data,cursor+2); level=data[cursor+4]; pan=data[cursor+5]; flags=data[cursor+6]; cursor+=7
            sample=samples.get(sample_id)
            if sample is None: raise NativeDriverError(f"sample B {sample_id} absent")
            for address,value in _adpcm_b_writes(sample,delta,level,pan,flags,sample_page_base): wr(address,value)
            continue
        if opcode == 0x23:
            wr(0x0140,0x00); continue
        if opcode == 0x30:
            target=_be32(data,cursor)
            if not code_offset <= target < code_end: raise NativeDriverError("JUMP hors CODE")
            cursor=target; continue
        if opcode == 0x31:
            slot=data[cursor]; count=_be16(data,cursor+1); target=_be32(data,cursor+3); cursor+=7
            if slot>=8 or count==0 or not code_offset <= target < code_end: raise NativeDriverError("LOOP invalide")
            state=loops.get(slot)
            if state is None or state[0]!=instruction_pc: state=(instruction_pc,count)
            if state[1]>1:
                loops[slot]=(instruction_pc,state[1]-1); cursor=target
            else: loops.pop(slot,None)
            continue
        raise NativeDriverError(f"opcode DSEQ inconnu ${opcode:02X}")
    raise NativeDriverError("budget DSEQ depasse")


def pack_banked_stream(commands: Iterable[bytes], base_bank: int = 0, pad_final: bool = False) -> bytes:
    commands = list(commands)
    if not 0 <= int(base_bank) <= 0xFFFF:
        raise NativeDriverError("base bank Z80 hors plage")
    pages: list[bytearray] = [bytearray()]
    bank = int(base_bank)
    for index, command in enumerate(commands):
        if not command or len(command) > BANK_BYTES - 3:
            raise NativeDriverError("commande native invalide/trop grande")
        page = pages[-1]
        more_after = index + 1 < len(commands)
        next_bank = bank + 1
        marker_size = 2 if next_bank <= 0xFF else 3
        reserve = marker_size if more_after else 0
        if len(page) + len(command) + reserve > BANK_BYTES:
            if next_bank > 0xFFFF:
                raise NativeDriverError("catalogue musique depasse 65536 banques Z80")
            if len(page) + marker_size > BANK_BYTES:
                raise NativeDriverError("pas de place pour marqueur BANK")
            if next_bank <= 0xFF:
                page += bytes((OP_BANK, next_bank))
            else:
                page += bytes((OP_BANK16, (next_bank >> 8) & 0xFF, next_bank & 0xFF))
            page += bytes(BANK_BYTES - len(page))
            pages.append(bytearray())
            bank = next_bank
            page = pages[-1]
        page += command
    for page in pages[:-1]:
        if len(page) != BANK_BYTES:
            page += bytes(BANK_BYTES - len(page))
    if pad_final and pages and len(pages[-1]) < BANK_BYTES:
        pages[-1] += bytes(BANK_BYTES - len(pages[-1]))
    return b"".join(bytes(page) for page in pages)


class NativeZ80Bus:
    def __init__(self, firmware: bytes, stream: bytes) -> None:
        if len(firmware) > RAM_BYTES:
            raise NativeDriverError("firmware Z80 trop grand")
        self.ram = bytearray(RAM_BYTES)
        self.ram[:len(firmware)] = firmware
        self.stream = stream
        self.mail = bytearray(0x100)
        self.bank = 0
        self.audio_address = 0
        self.timer_bytes = [0, 0, 0, 0]
        self.timer_target_master: int | None = None
        self.song_start_master = 0
        self.master_cycle = 0
        self.events: list[ScheduledWrite] = []
        self.missed_deadlines = 0

    def read8_z80(self, address: int) -> int:
        address &= 0xFFFF
        if address < RAM_BYTES:
            return self.ram[address]
        if MAIL_CMD <= address < MAIL_CMD + len(self.mail):
            return self.mail[address - MAIL_CMD]
        if address == BANKREG:
            return self.bank & 0xFF
        if address == BANKREG_HI:
            return (self.bank >> 8) & 0xFF
        if ROM_WINDOW <= address <= 0xFFFF:
            absolute = self.bank * BANK_BYTES + (address - ROM_WINDOW)
            return self.stream[absolute] if absolute < len(self.stream) else 0x00
        return 0xFF

    def write8_z80(self, address: int, value: int) -> None:
        address &= 0xFFFF; value &= 0xFF
        if address < RAM_BYTES:
            self.ram[address] = value; return
        if MAIL_CMD <= address < MAIL_CMD + len(self.mail):
            self.mail[address - MAIL_CMD] = value; return
        if address == BANKREG:
            self.bank = (self.bank & 0xFF00) | value; return
        if address == BANKREG_HI:
            self.bank = (self.bank & 0x00FF) | (value << 8); return
        if address == AUDIO_ADDR_HI:
            self.audio_address = (self.audio_address & 0x00FF) | (value << 8); return
        if address == AUDIO_ADDR_LO:
            self.audio_address = (self.audio_address & 0xFF00) | value; return
        if address == AUDIO_DATA:
            if self.audio_address > 0x01FF:
                raise NativeDriverError(f"Z80 ecrit MMIO hors DMS: ${self.audio_address:04X}")
            # LD (nn),A writes near the end of the 13-T instruction.  We stamp
            # it 10 T-states after instruction start; this keeps burst writes
            # physically separated instead of pretending they are zero-time.
            absolute = self.master_cycle + 10 * MASTER_PER_Z80_T
            relative = max(0, absolute - self.song_start_master)
            self.events.append(ScheduledWrite(relative, self.audio_address, value)); return
        if TIMER_3 <= address <= TIMER_0:
            self.timer_bytes[address - TIMER_3] = value; return
        if address == SONG_RESET:
            if value:
                self.song_start_master = self.master_cycle
                self.timer_target_master = None
                self.events.clear()
                self.missed_deadlines = 0
            return
        if address == TIMER_CTRL:
            if value & 1:
                target_song = int.from_bytes(bytes(self.timer_bytes), "big")
                target_master = self.song_start_master + target_song
                if target_master <= self.master_cycle:
                    self.missed_deadlines += 1
                self.timer_target_master = target_master
            else:
                self.timer_target_master = None
            return


class NativeZ80Rig:
    def __init__(self, stream: bytes) -> None:
        self.bus = NativeZ80Bus(build_z80_native_driver(), stream)
        self.cpu = Z80(self.bus)

    def run(self, max_master_cycles: int, start_bank: int = 0) -> tuple[list[ScheduledWrite], int, int]:
        self.bus.mail[1] = int(start_bank) & 0xFF
        self.bus.mail[3] = (int(start_bank) >> 8) & 0xFF
        self.bus.mail[0] = 1  # PLAY command
        steps = 0
        max_steps = 20_000_000
        seen_running = False
        while self.bus.master_cycle <= max_master_cycles:
            if self.bus.mail[2] == 0xC2:
                seen_running = True
            elif seen_running and self.bus.mail[2] == 0x80 and self.bus.timer_target_master is None:
                return self.bus.events, max(0, self.bus.master_cycle - self.bus.song_start_master), steps
            if self.cpu.halted:
                target = self.bus.timer_target_master
                if target is None:
                    raise NativeDriverError("Z80 HALT sans timer pendant la lecture")
                # Z80 sees the wake on its next T-state boundary.
                wake = max(self.bus.master_cycle, target)
                rem = wake % MASTER_PER_Z80_T
                if rem:
                    wake += MASTER_PER_Z80_T - rem
                delta = wake - self.bus.master_cycle
                self.cpu.cycles += delta // MASTER_PER_Z80_T
                self.bus.master_cycle = wake
                self.bus.timer_target_master = None
                self.cpu.halted = False
                continue
            if steps >= max_steps:
                raise NativeDriverError("budget d'instructions Z80 depasse")
            cost = self.cpu.step()
            self.bus.master_cycle += cost * MASTER_PER_Z80_T
            steps += 1
        raise NativeDriverError("driver Z80 n'a pas termine dans la limite temporelle")


def compile_and_run(data: bytes) -> DriverResult:
    commands, reference, reference_halt = build_native_commands(data)
    stream = pack_banked_stream(commands)
    rig = NativeZ80Rig(stream)
    max_cycles = reference_halt + 5 * SYSTEM_CLOCK
    events, halt_cycle, _steps = rig.run(max_cycles)
    if len(events) != len(reference):
        raise NativeDriverError(
            f"nombre de writes Z80 {len(events)} != reference {len(reference)}"
        )
    lateness: list[int] = []
    for index, (actual, expected) in enumerate(zip(events, reference)):
        if (actual.address, actual.data) != (expected.address, expected.data):
            raise NativeDriverError(
                f"divergence write #{index}: Z80 ${actual.address:04X}=${actual.data:02X} "
                f"vs DSEQ ${expected.address:04X}=${expected.data:02X}"
            )
        lateness.append(actual.cycle - expected.cycle)
    return DriverResult(
        events=tuple(events), reference_events=tuple(reference),
        halt_cycle=halt_cycle, reference_halt_cycle=reference_halt,
        z80_cycles=rig.cpu.cycles, stream_bytes=len(stream),
        banks=max(1, (len(stream) + BANK_BYTES - 1) // BANK_BYTES),
        missed_deadlines=rig.bus.missed_deadlines,
        max_lateness_cycles=max(lateness, default=0),
        mean_lateness_cycles=(sum(lateness) / len(lateness)) if lateness else 0.0,
    )


def write_ztr1(path: Path, result: DriverResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = bytearray()
    header += b"ZTR1"
    header += struct.pack(">I", 1)
    header += struct.pack(">Q", result.halt_cycle)
    header += struct.pack(">I", len(result.events))
    header += struct.pack(">I", 0)
    body = bytearray()
    last = -1
    for event in result.events:
        if event.cycle < last:
            raise NativeDriverError("trace Z80 non chronologique")
        last = event.cycle
        body += struct.pack(">QHBB", event.cycle, event.address, event.data, 0)
    path.write_bytes(bytes(header + body))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--stream", type=Path)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    commands, _reference, _halt = build_native_commands(data)
    stream = pack_banked_stream(commands)
    if args.stream:
        args.stream.parent.mkdir(parents=True, exist_ok=True)
        args.stream.write_bytes(stream)
    result = compile_and_run(data)
    if args.trace:
        write_ztr1(args.trace, result)
    print(
        f"DMS Native Z80: {len(result.events)} writes | {result.banks} banks | "
        f"stream {result.stream_bytes} bytes | missed deadlines {result.missed_deadlines}"
    )
    print(
        f"halt Z80 {result.halt_cycle / SYSTEM_CLOCK:.6f}s | "
        f"DSEQ ref {result.reference_halt_cycle / SYSTEM_CLOCK:.6f}s | "
        f"max lateness {result.max_lateness_cycles} cycles "
        f"({result.max_lateness_cycles / SYSTEM_CLOCK * 1e6:.3f} us) | "
        f"mean {result.mean_lateness_cycles / SYSTEM_CLOCK * 1e6:.3f} us"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
