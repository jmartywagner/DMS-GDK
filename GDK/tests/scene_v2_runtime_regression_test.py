#!/usr/bin/env python3
"""Generic DSCENE V2 regression checks for deferred objects and authored FX."""
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dmsscenec', ROOT / 'tools' / 'dmsscenec.py')
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

scene = {
    'format':'DSCENE','format_version':2,'name':'REGRESSION','type':'SCREEN','video_mode':0,
    'objects':[
        {'id':'PLAYER','kind':'SPRITE','layer':'ACTORS','resource':'player.dres','visible':True,'sprite_cells':12,'palette':0},
        {'id':'DEFERRED','kind':'SPRITE','layer':'ACTORS','resource':'boss.dres','visible':False,'sprite_cells':72,'palette':0},
    ],
    'events':[],
}
diags = mod.validate_scene(scene, None)
errs = [msg for sev,msg in diags if sev == 'ERROR']
assert not any('budget sprites simultanés' in e for e in errs), errs

src = (ROOT / 'lib' / 'src' / 'dms_scene.c').read_text(encoding='utf-8')
assert 'DMS_FX_FLASH||e->ref==DMS_FX_PALETTE_STROBE' in src
assert 'p.color=0x01FFu' in src
print('PASS DSCENE V2 generic deferred-budget + bright flash regression')
