#!/usr/bin/env python3
from __future__ import annotations
import shutil, subprocess, sys, tempfile, importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
BUILDER=ROOT/'GDK'/'tools'/'dmsgcc_build.py'
spec=importlib.util.spec_from_file_location('dmsgcc_build_locator', BUILDER)
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
print('DMS-GDK - vérification toolchain Motorola 68000')
try:
    tc=mod.find_toolchain()
except Exception as exc:
    print('INFO : toolchain m68k-gcc/binutils non détectée.')
    print('DETAIL :',exc)
    print('Lance INSTALL_M68K_TOOLCHAIN.bat si aucune ancienne installation n’existe.')
    raise SystemExit(1)
bin_dir=tc/'bin'
def pick(name):
    for q in (bin_dir/(name+'.exe'), bin_dir/name):
        if q.exists(): return q
    return None
gcc=pick('m68k-elf-gcc'); ld=pick('m68k-elf-ld'); objdump=pick('m68k-elf-objdump')
if not gcc or not ld:
    print('ERREUR : toolchain trouvée mais incomplète :',tc); raise SystemExit(2)
print('GCC :',gcc); print('LD  :',ld)
print(subprocess.check_output([str(gcc),'--version'],text=True,errors='replace').splitlines()[0])
print(subprocess.check_output([str(ld),'--version'],text=True,errors='replace').splitlines()[0])
with tempfile.TemporaryDirectory(prefix='dms68k_') as td:
    td=Path(td); c=td/'smoke.c'; o=td/'smoke.o'
    c.write_text('#include <stdint.h>\nvoid f(void){*(volatile uint8_t*)0x300002u=0;}\n',encoding='ascii')
    cp=subprocess.run([str(gcc),'-m68000','-Os','-ffreestanding','-fno-builtin','-fomit-frame-pointer','-nostdlib','-c',str(c),'-o',str(o)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace')
    if cp.returncode or not o.exists():
        print(cp.stdout); print('ERREUR : smoke test C->m68k échoué.'); raise SystemExit(2)
    if objdump:
        info=subprocess.check_output([str(objdump),'-f',str(o)],text=True,errors='replace')
        for line in info.splitlines():
            if 'file format' in line or 'architecture' in line: print(line.strip())
print('PASS : toolchain locale prête pour le développement DMS-1.')
