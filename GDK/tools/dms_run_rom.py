#!/usr/bin/env python3
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path
HERE=Path(__file__).resolve(); ROOT=HERE.parents[2]; RUNTIME=ROOT/'RUNTIME'
def main()->int:
    ap=argparse.ArgumentParser(description='Lancer une ROM DMS-1 sans reconstruire le runtime Windows')
    ap.add_argument('rom',type=Path); a=ap.parse_args(); rom=a.rom.resolve()
    if not rom.is_file(): print('ERREUR : ROM introuvable :',rom,file=sys.stderr); return 2
    player=RUNTIME/'tools'/'dms_console90_native_player.py'
    if not player.is_file(): print('ERREUR : player runtime absent :',player,file=sys.stderr); return 2
    needed=[RUNTIME/'build'/'dms1_m68k.dll',RUNTIME/'build'/'dms1_vdp_render.dll']
    missing=[str(x) for x in needed if not x.is_file()]
    if missing:
        print('ERREUR : runtime precompile incomplet :',*missing,sep='\n  ',file=sys.stderr); print('Lance ADMIN\\DMS_DOCTOR.bat.',file=sys.stderr); return 2
    print('DMS RUN ROM - runtime precompile, aucune recompilation MSYS2')
    print('ROM :',rom)
    return subprocess.call([sys.executable,str(player),str(rom)],cwd=str(RUNTIME))
if __name__=='__main__': raise SystemExit(main())
