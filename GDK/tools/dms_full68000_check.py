#!/usr/bin/env python3
"""Functional smoke test for the installed native Musashi bridge."""
from __future__ import annotations
import ctypes, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
DLL=ROOT/'RUNTIME'/'build'/'dms1_m68k.dll'
if not DLL.exists():
    print('ERREUR :',DLL,'absente',file=sys.stderr); raise SystemExit(2)
try: lib=ctypes.CDLL(str(DLL))
except OSError as exc:
    print('ERREUR : DLL 68000 non chargeable :',exc,file=sys.stderr); raise SystemExit(3)
required=['dms68k_init','dms68k_load_rom','dms68k_reset','dms68k_run','dms68k_get_pc','dms68k_set_irq','dms68k_get_ram','dms68k_event_count','dms68k_event_address','dms68k_event_value','dms68k_event_kind']
missing=[n for n in required if not hasattr(lib,n)]
if missing:
    print('ERREUR : exports manquants :',', '.join(missing),file=sys.stderr); raise SystemExit(4)

lib.dms68k_init.restype=ctypes.c_int
lib.dms68k_load_rom.argtypes=[ctypes.POINTER(ctypes.c_uint8),ctypes.c_uint32];lib.dms68k_load_rom.restype=ctypes.c_int
lib.dms68k_run.argtypes=[ctypes.c_int];lib.dms68k_run.restype=ctypes.c_int
lib.dms68k_get_ram.argtypes=[ctypes.POINTER(ctypes.c_uint8),ctypes.c_uint32]
lib.dms68k_event_count.restype=ctypes.c_uint32
lib.dms68k_event_address.argtypes=[ctypes.c_uint32];lib.dms68k_event_address.restype=ctypes.c_uint32
lib.dms68k_event_value.argtypes=[ctypes.c_uint32];lib.dms68k_event_value.restype=ctypes.c_uint32
lib.dms68k_event_kind.argtypes=[ctypes.c_uint32];lib.dms68k_event_kind.restype=ctypes.c_uint32
lib.dms68k_get_pc.restype=ctypes.c_uint32

# Real 68000 program: write backdrop register, write work RAM, then loop.
rom=bytearray(0x120)
rom[0:4]=(0x0010FFFC).to_bytes(4,'big')
rom[4:8]=(0x00000100).to_bytes(4,'big')
pc=0x100
program=bytes.fromhex(
    '13 FC 00 05 00 30 00 04 '  # MOVE.B #$05,$300004
    '13 FC 00 7A 00 10 00 00 '  # MOVE.B #$7A,$100000
    '60 FE'                      # BRA.S self
)
rom[pc:pc+len(program)]=program
buf=(ctypes.c_uint8*len(rom)).from_buffer_copy(rom)
if not lib.dms68k_init() or not lib.dms68k_load_rom(buf,len(rom)):
    print('ERREUR : init/load ROM smoke test impossible',file=sys.stderr); raise SystemExit(5)
lib.dms68k_reset(); executed=lib.dms68k_run(200)
ram=(ctypes.c_uint8*0x10000)();lib.dms68k_get_ram(ram,0x10000)
if ram[0] != 0x7A:
    print(f'ERREUR : programme 68000 non exécuté (RAM[0]={ram[0]:02X})',file=sys.stderr); raise SystemExit(6)
found=False
for i in range(int(lib.dms68k_event_count())):
    if lib.dms68k_event_kind(i)==3 and lib.dms68k_event_address(i)==0x300004 and lib.dms68k_event_value(i)==5:
        found=True;break
if not found:
    print('ERREUR : écriture MMIO VDP du vrai programme 68000 non observée',file=sys.stderr); raise SystemExit(7)
print(f'PASS : Musashi exécute un vrai programme 68000 ({executed} cycles, PC=${lib.dms68k_get_pc():06X}, RAM/MMIO OK).')
