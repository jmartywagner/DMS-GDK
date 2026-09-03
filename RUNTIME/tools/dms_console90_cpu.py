#!/usr/bin/env python3
"""Small, real-opcode 68000/Z80 cores used by DMS-1 Console90 P0.5.

This is deliberately a bootstrap subset, not a full ISA implementation. Every
implemented opcode uses its actual Motorola 68000 or Zilog Z80 encoding. The
purpose of P0.5 is to move game logic into cartridge CPU code immediately while
keeping the branch small enough to validate the DMS-1 bus design.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class CpuFault(RuntimeError):
    pass


class Bus68k(Protocol):
    def read8_68k(self, address: int) -> int: ...
    def write8_68k(self, address: int, value: int) -> None: ...


class BusZ80(Protocol):
    def read8_z80(self, address: int) -> int: ...
    def write8_z80(self, address: int, value: int) -> None: ...


def _s8(value: int) -> int:
    return value - 0x100 if value & 0x80 else value


def _s16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


@dataclass
class M68000:
    bus: Bus68k

    def __post_init__(self) -> None:
        self.d = [0] * 8
        self.a = [0] * 8
        self.pc = 0
        self.sr = 0x2700
        self.cycles = 0
        self.active_cycles = 0
        self.stopped = False
        self.reset()

    def read8(self, address: int) -> int:
        return self.bus.read8_68k(address & 0xFFFFFF) & 0xFF

    def read16(self, address: int) -> int:
        address &= 0xFFFFFF
        return (self.read8(address) << 8) | self.read8(address + 1)

    def read32(self, address: int) -> int:
        return (self.read16(address) << 16) | self.read16(address + 2)

    def write8(self, address: int, value: int) -> None:
        self.bus.write8_68k(address & 0xFFFFFF, value & 0xFF)

    def write16(self, address: int, value: int) -> None:
        self.write8(address, value >> 8)
        self.write8(address + 1, value)

    def write32(self, address: int, value: int) -> None:
        self.write16(address, value >> 16)
        self.write16(address + 2, value)

    def fetch16(self) -> int:
        value = self.read16(self.pc)
        self.pc = (self.pc + 2) & 0xFFFFFF
        return value

    def fetch32(self) -> int:
        value = self.read32(self.pc)
        self.pc = (self.pc + 4) & 0xFFFFFF
        return value

    def reset(self) -> None:
        self.d[:] = [0] * 8
        self.a[:] = [0] * 8
        self.a[7] = self.read32(0)
        self.pc = self.read32(4) & 0xFFFFFF
        self.sr = 0x2700
        self.cycles = 0
        self.active_cycles = 0
        self.stopped = False

    @property
    def z(self) -> bool:
        return bool(self.sr & 0x0004)

    def _set_nz(self, value: int, bits: int) -> None:
        mask = (1 << bits) - 1
        sign = 1 << (bits - 1)
        value &= mask
        self.sr &= ~0x000F  # N/Z/V/C; X intentionally untouched
        if value == 0:
            self.sr |= 0x0004
        if value & sign:
            self.sr |= 0x0008

    def _branch(self, condition: bool, opcode: int) -> int:
        displacement8 = opcode & 0xFF
        if displacement8 == 0:
            displacement = _s16(self.fetch16())
            cost = 12
        else:
            displacement = _s8(displacement8)
            cost = 10
        if condition:
            self.pc = (self.pc + displacement) & 0xFFFFFF
        return cost

    def step(self) -> int:
        if self.stopped:
            self.cycles += 4
            return 4
        pc_before = self.pc
        opcode = self.fetch16()

        if opcode == 0x4E71:  # NOP
            cost = 4
        elif opcode == 0x4E72:  # STOP #sr
            self.sr = self.fetch16()
            self.stopped = True
            cost = 4
        elif (opcode & 0xF100) == 0x7000:  # MOVEQ #imm,Dn
            reg = (opcode >> 9) & 7
            value = _s8(opcode & 0xFF) & 0xFFFFFFFF
            self.d[reg] = value
            self._set_nz(value, 32)
            cost = 4
        elif (opcode & 0xF1FF) == 0x1039:  # MOVE.B abs.l,Dn
            reg = (opcode >> 9) & 7
            address = self.fetch32()
            value = self.read8(address)
            self.d[reg] = (self.d[reg] & 0xFFFFFF00) | value
            self._set_nz(value, 8)
            cost = 16
        elif (opcode & 0xF1FF) == 0x3039:  # MOVE.W abs.l,Dn
            reg = (opcode >> 9) & 7
            address = self.fetch32()
            value = self.read16(address)
            self.d[reg] = (self.d[reg] & 0xFFFF0000) | value
            self._set_nz(value, 16)
            cost = 16
        elif (opcode & 0xF1FF) == 0x2039:  # MOVE.L abs.l,Dn
            reg = (opcode >> 9) & 7
            address = self.fetch32()
            value = self.read32(address)
            self.d[reg] = value
            self._set_nz(value, 32)
            cost = 20
        elif (opcode & 0xFFF8) == 0x13C0:  # MOVE.B Dn,abs.l
            reg = opcode & 7
            address = self.fetch32()
            value = self.d[reg] & 0xFF
            self.write8(address, value)
            self._set_nz(value, 8)
            cost = 16
        elif (opcode & 0xFFF8) == 0x33C0:  # MOVE.W Dn,abs.l
            reg = opcode & 7
            address = self.fetch32()
            value = self.d[reg] & 0xFFFF
            self.write16(address, value)
            self._set_nz(value, 16)
            cost = 16
        elif (opcode & 0xFFF8) == 0x23C0:  # MOVE.L Dn,abs.l
            reg = opcode & 7
            address = self.fetch32()
            value = self.d[reg] & 0xFFFFFFFF
            self.write32(address, value)
            self._set_nz(value, 32)
            cost = 20
        elif opcode == 0x13FC:  # MOVE.B #imm,abs.l
            value = self.fetch16() & 0xFF
            address = self.fetch32()
            self.write8(address, value)
            self._set_nz(value, 8)
            cost = 20
        elif opcode == 0x33FC:  # MOVE.W #imm,abs.l
            value = self.fetch16()
            address = self.fetch32()
            self.write16(address, value)
            self._set_nz(value, 16)
            cost = 20
        elif opcode == 0x23FC:  # MOVE.L #imm,abs.l
            value = self.fetch32()
            address = self.fetch32()
            self.write32(address, value)
            self._set_nz(value, 32)
            cost = 28
        elif (opcode & 0xFFF8) == 0x0200:  # ANDI.B #imm,Dn
            reg = opcode & 7
            immediate = self.fetch16() & 0xFF
            value = (self.d[reg] & 0xFF) & immediate
            self.d[reg] = (self.d[reg] & 0xFFFFFF00) | value
            self._set_nz(value, 8)
            cost = 8
        elif (opcode & 0xFFF8) == 0x0240:  # ANDI.W #imm,Dn
            reg = opcode & 7
            immediate = self.fetch16()
            value = (self.d[reg] & 0xFFFF) & immediate
            self.d[reg] = (self.d[reg] & 0xFFFF0000) | value
            self._set_nz(value, 16)
            cost = 8
        elif (opcode & 0xFFF8) == 0x0C00:  # CMPI.B #imm,Dn
            reg = opcode & 7
            immediate = self.fetch16() & 0xFF
            lhs = self.d[reg] & 0xFF
            result = (lhs - immediate) & 0xFF
            self._set_nz(result, 8)
            cost = 8
        elif (opcode & 0xFFF8) == 0x4A00:  # TST.B Dn
            reg = opcode & 7
            self._set_nz(self.d[reg] & 0xFF, 8)
            cost = 4
        elif (opcode & 0xFFF8) == 0x5240:  # ADDQ.W #1,Dn
            reg = opcode & 7
            value = ((self.d[reg] & 0xFFFF) + 1) & 0xFFFF
            self.d[reg] = (self.d[reg] & 0xFFFF0000) | value
            self._set_nz(value, 16)
            cost = 4
        elif (opcode & 0xFFF8) == 0x5340:  # SUBQ.W #1,Dn
            reg = opcode & 7
            value = ((self.d[reg] & 0xFFFF) - 1) & 0xFFFF
            self.d[reg] = (self.d[reg] & 0xFFFF0000) | value
            self._set_nz(value, 16)
            cost = 4
        elif (opcode & 0xFF00) == 0x6000:  # BRA
            cost = self._branch(True, opcode)
        elif (opcode & 0xFF00) == 0x6600:  # BNE
            cost = self._branch(not self.z, opcode)
        elif (opcode & 0xFF00) == 0x6700:  # BEQ
            cost = self._branch(self.z, opcode)
        else:
            raise CpuFault(f"68000 opcode ${opcode:04X} non implemente a ${pc_before:06X}")

        self.cycles += cost
        self.active_cycles += cost
        return cost

    def run(self, budget: int) -> int:
        consumed = 0
        while consumed < budget:
            # STOP is a hardware sleep state. Do not burn Python time executing
            # millions of synthetic 4-cycle STOP slots; the console scheduler
            # advances the stopped CPU clock directly until the next interrupt.
            if self.stopped:
                remaining = budget - consumed
                self.cycles += remaining
                consumed += remaining
                break
            consumed += self.step()
        return consumed


@dataclass
class Z80:
    bus: BusZ80

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.a = self.f = self.b = self.c = self.d = self.e = self.h = self.l = 0
        self.ix = 0
        self.sp = 0xFFFF
        self.pc = 0
        self.cycles = 0
        self.active_cycles = 0
        self.halted = False
        self.zf = False

    def rb(self, address: int) -> int:
        return self.bus.read8_z80(address & 0xFFFF) & 0xFF

    def wb(self, address: int, value: int) -> None:
        self.bus.write8_z80(address & 0xFFFF, value & 0xFF)

    def next8(self) -> int:
        value = self.rb(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        return value

    def next16(self) -> int:
        lo = self.next8()
        hi = self.next8()
        return lo | (hi << 8)

    def step(self) -> int:
        if self.halted:
            self.cycles += 4
            return 4
        pc_before = self.pc
        opcode = self.next8()
        if opcode == 0x00:  # NOP
            cost = 4
        elif opcode == 0x31:  # LD SP,nn
            self.sp = self.next16()
            cost = 10
        elif opcode == 0x21:  # LD HL,nn
            value = self.next16()
            self.h = (value >> 8) & 0xFF
            self.l = value & 0xFF
            cost = 10
        elif opcode == 0x26:  # LD H,n
            self.h = self.next8()
            cost = 7
        elif opcode == 0x2E:  # LD L,n
            self.l = self.next8()
            cost = 7
        elif opcode == 0x23:  # INC HL
            value = (((self.h << 8) | self.l) + 1) & 0xFFFF
            self.h = (value >> 8) & 0xFF
            self.l = value & 0xFF
            cost = 6
        elif opcode == 0x3E:  # LD A,n
            self.a = self.next8()
            cost = 7
        elif opcode == 0x7E:  # LD A,(HL)
            self.a = self.rb((self.h << 8) | self.l)
            cost = 7
        elif opcode == 0x47:  # LD B,A
            self.b = self.a
            cost = 4
        elif opcode == 0x4F:  # LD C,A
            self.c = self.a
            cost = 4
        elif opcode == 0x57:  # LD D,A
            self.d = self.a
            cost = 4
        elif opcode == 0x5F:  # LD E,A
            self.e = self.a
            cost = 4
        elif opcode == 0x79:  # LD A,C
            self.a = self.c
            cost = 4
        elif opcode == 0x7A:  # LD A,D
            self.a = self.d
            cost = 4
        elif opcode == 0x7B:  # LD A,E
            self.a = self.e
            cost = 4
        elif opcode == 0x77:  # LD (HL),A
            self.wb((self.h << 8) | self.l, self.a)
            cost = 7
        elif opcode == 0x19:  # ADD HL,DE
            value = (((self.h << 8) | self.l) + ((self.d << 8) | self.e)) & 0xFFFF
            self.h = (value >> 8) & 0xFF
            self.l = value & 0xFF
            cost = 11
        elif opcode == 0xE5:  # PUSH HL
            value = ((self.h << 8) | self.l) & 0xFFFF
            self.sp = (self.sp - 1) & 0xFFFF; self.wb(self.sp, (value >> 8) & 0xFF)
            self.sp = (self.sp - 1) & 0xFFFF; self.wb(self.sp, value & 0xFF)
            cost = 11
        elif opcode == 0xE1:  # POP HL
            lo = self.rb(self.sp); self.sp = (self.sp + 1) & 0xFFFF
            hi = self.rb(self.sp); self.sp = (self.sp + 1) & 0xFFFF
            self.h = hi; self.l = lo
            cost = 10
        elif opcode == 0xDD:  # IX-prefixed subset used by native DMS audio firmware
            sub = self.next8()
            if sub == 0x2A:  # LD IX,(nn)
                address = self.next16()
                lo = self.rb(address); hi = self.rb((address + 1) & 0xFFFF)
                self.ix = lo | (hi << 8)
                cost = 20
            elif sub == 0x7E:  # LD A,(IX+d)
                disp = _s8(self.next8())
                self.a = self.rb((self.ix + disp) & 0xFFFF)
                cost = 19
            elif sub == 0x77:  # LD (IX+d),A
                disp = _s8(self.next8())
                self.wb((self.ix + disp) & 0xFFFF, self.a)
                cost = 19
            elif sub == 0x23:  # INC IX
                self.ix = (self.ix + 1) & 0xFFFF
                cost = 10
            else:
                raise CpuFault(f"Z80 opcode DD ${sub:02X} non implemente a ${pc_before:04X}")
        elif opcode == 0x3A:  # LD A,(nn)
            self.a = self.rb(self.next16())
            cost = 13
        elif opcode == 0x32:  # LD (nn),A
            self.wb(self.next16(), self.a)
            cost = 13
        elif opcode == 0xAF:  # XOR A
            self.a = 0
            self.zf = True
            cost = 4
        elif opcode == 0xB7:  # OR A
            self.zf = self.a == 0
            cost = 4
        elif opcode == 0xFE:  # CP n
            value = self.next8()
            self.zf = self.a == value
            cost = 7
        elif opcode == 0x10:  # DJNZ e
            disp = _s8(self.next8())
            self.b = (self.b - 1) & 0xFF
            if self.b != 0:
                self.pc = (self.pc + disp) & 0xFFFF
                cost = 13
            else:
                cost = 8
        elif opcode in (0x18, 0x20, 0x28):  # JR / JR NZ / JR Z
            disp = _s8(self.next8())
            take = opcode == 0x18 or (opcode == 0x20 and not self.zf) or (opcode == 0x28 and self.zf)
            if take:
                self.pc = (self.pc + disp) & 0xFFFF
                cost = 12
            else:
                cost = 7
        elif opcode == 0xC3:  # JP nn
            self.pc = self.next16()
            cost = 10
        elif opcode == 0x76:  # HALT
            self.halted = True
            cost = 4
        else:
            raise CpuFault(f"Z80 opcode ${opcode:02X} non implemente a ${pc_before:04X}")
        self.cycles += cost
        self.active_cycles += cost
        return cost

    def run(self, budget: int) -> int:
        consumed = 0
        while consumed < budget:
            consumed += self.step()
        return consumed
