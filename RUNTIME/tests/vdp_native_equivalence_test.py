#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from dms_console90_machine import Console90Machine
from dms_console90_vdp import SUPPORTED_MODES
from dms_vdp_native import NativeVdpRenderer

rom=ROOT/'roms'/'dms1_gdk_system_demo.dmc'
m=Console90Machine.from_path(rom)
r=NativeVdpRenderer(ROOT)
# Let the cartridge populate VRAM/CRAM and produce non-zero scrolling/sprites.
for _ in range(9): m.step_frame()
for mode in SUPPORTED_MODES:
    m.vdp.request_mode(mode,vblank=True)
    # Perturb scroll registers so the equivalence test covers address wrapping.
    m.vdp.scroll_a_x=(17+mode*23)&0x1ff
    m.vdp.scroll_a_y=(5+mode*7)&0xff
    m.vdp.scroll_b_x=(11+mode*13)&0x1ff
    m.vdp.scroll_b_y=(3+mode*9)&0xff
    py=m.vdp.render_rgb()
    width,native=r.render(m.vdp)
    assert width==m.vdp.active_width, (mode,width,m.vdp.active_width)
    assert native==py, f'mode {mode}: native VDP differs from Python reference'
print('P1.0.2 NATIVE VDP EQUIVALENCE TEST OK')
