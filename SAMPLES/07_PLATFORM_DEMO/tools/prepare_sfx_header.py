#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from pathlib import Path
p=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
music=p/'res'/'music.dmr'; mf=p/'res'/'audio'/'audio_manifest.json'; out=p/'src'/'sfx_generated.h'
if not music.exists() or not mf.exists(): raise SystemExit('music/audio manifest absent')
m=json.loads(mf.read_text(encoding='utf-8'));base=(music.stat().st_size+255)//256
L=['#ifndef DMS1_PLATFORM_SFX_GENERATED_H','#define DMS1_PLATFORM_SFX_GENERATED_H','',f'#define GAME_SFX_BANK_BASE_PAGE {base}u',f'#define GAME_SFX_BANK_BYTES {int(m.get("bank_bytes",0))}u','']
for s in m['samples']:
 sym=s['symbol'].upper();codec=1 if s['codec']=='A' else 2
 L += [f'#define GAME_SFX_{sym}_CODEC {codec}u',f'#define GAME_SFX_{sym}_START_PAGE {base+int(s["start_page"])}u',f'#define GAME_SFX_{sym}_END_PAGE {base+int(s["end_page"])}u',f'#define GAME_SFX_{sym}_RATE_HZ {int(s["rate_hz"])}u','']
L += ['#endif',''];out.write_text('\n'.join(L),encoding='ascii');print(f'PASS SFX header base={base} samples={len(m["samples"])}')
