#!/usr/bin/env python3
"""DMS-GDK P1.0 bootstrap C-subset compiler.

This is intentionally not advertised as a full ISO C compiler. It parses the
small DMS SDK bootstrap API used by the first sample and emits genuine 68000
opcodes. A future full m68k C toolchain can replace this frontend without
changing the DMS hardware API or cartridge format.
"""
from __future__ import annotations
import re, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools"))
from dms_console90_firmware import build_m68k_boot
from dms_assetc import compile_png

MODES={
    "DMS_MODE_STANDARD":0,"DMS_MODE_HIGH_COLOR":1,"DMS_MODE_SCROLL":2,
    "DMS_MODE_SPRITE":3,"DMS_MODE_LOW_RES":4,
}

def strip_comments(s:str)->str:
    s=re.sub(r"/\*.*?\*/","",s,flags=re.S); s=re.sub(r"//.*","",s)
    return s

def compile_source(path:Path)->bytes:
    s=strip_comments(path.read_text(encoding="utf-8"))
    if not re.search(r"\bint\s+main\s*\(\s*void\s*\)",s): raise ValueError("int main(void) requis")
    if "SYS_init()" not in s: raise ValueError("SYS_init() requis")
    if "DEMO_runMultimode()" not in s: raise ValueError("P1.0 bootstrap exige DEMO_runMultimode()")
    m=re.search(r"VDP_setMode\s*\(\s*([A-Z0-9_]+)\s*\)",s)
    start_mode=MODES.get(m.group(1),0) if m else 0
    m=re.search(r"DEMO_setScroll\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)",s)
    sa,sb=(2,1) if not m else (int(m.group(1)),int(m.group(2)))
    player_tiles=None; player_palette=None
    m=re.search(r'SPR_loadPNG\s*\(\s*"([^"]+)"\s*,\s*(\d+)\s*\)',s)
    if m:
        palette=int(m.group(2))
        if palette!=2: raise ValueError("bootstrap P1.0: sprite sample doit utiliser palette 2")
        img=compile_png((path.parent/m.group(1)).resolve())
        if (img.width,img.height)!=(16,16): raise ValueError("sprite sample P1.0 doit faire 16x16")
        player_tiles=img.tiles; player_palette=list(img.palette)
    return build_m68k_boot(start_mode=start_mode,scroll_a_step=sa,scroll_b_step=sb,
                           player_tiles=player_tiles,player_palette=player_palette)

def main()->int:
    import argparse
    ap=argparse.ArgumentParser(); ap.add_argument("source",type=Path); ap.add_argument("--out",type=Path,required=True)
    a=ap.parse_args(); blob=compile_source(a.source); a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_bytes(blob)
    print(f"DMSCC P1.0: {a.source} -> {a.out} ({len(blob)} bytes 68000)")
    return 0
if __name__=="__main__": raise SystemExit(main())
