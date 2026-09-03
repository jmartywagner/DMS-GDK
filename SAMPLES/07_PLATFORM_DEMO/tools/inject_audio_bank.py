#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path

if len(sys.argv) != 4:
    raise SystemExit('usage: inject_audio_bank.py <GDK_ROOT> <DMC> <ABNK>')
root=Path(sys.argv[1]).resolve(); dmc=Path(sys.argv[2]).resolve(); bank=Path(sys.argv[3]).resolve()
sys.path.insert(0, str(root/'RUNTIME'/'tools'))
from dms_console90_format import load_image, save_image
img=load_image(dmc)
chunks=[(c.kind,c.data) for c in img.chunks if c.kind != b'ABNK']
chunks.append((b'ABNK',bank.read_bytes()))
save_image(dmc,chunks,flags=img.flags)
check=load_image(dmc)
if check.optional_chunk(b'ABNK') != bank.read_bytes():
    raise SystemExit('ABNK verification failed')
report_path=dmc.parent/'BUILD_GCC_REPORT.json'
if report_path.exists():
    import json
    try:
        report=json.loads(report_path.read_text(encoding='utf-8'))
        report['audio_bank_bytes']=bank.stat().st_size
        report['rom_bytes']=dmc.stat().st_size
        report['sfx_transport']='Z80 PLAY_SAMPLE mailbox + ABNK'
        report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    except Exception:
        pass
print(f'PASS ABNK: {bank.stat().st_size} bytes -> {dmc.name} | final={dmc.stat().st_size} bytes')
