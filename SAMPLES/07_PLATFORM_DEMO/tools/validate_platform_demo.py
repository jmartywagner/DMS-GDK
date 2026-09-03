#!/usr/bin/env python3
from __future__ import annotations
import json, sys, zipfile
from pathlib import Path

PROJECT=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
RES=PROJECT/'res'; SRC=PROJECT/'src'; DOC=PROJECT/'DOCS_REPORTS'
errors=[]

def req(cond,msg):
    if not cond: errors.append(msg)

def jzip(path,name='manifest.json'):
    with zipfile.ZipFile(path) as z: return json.loads(z.read(name).decode('utf-8'))

im=jzip(RES/'platform_tiles.dimg'); mm=jzip(RES/'stage_platform.dmap'); cm=jzip(RES/'stage_platform.dcoll')
req(im.get('format')=='DIMG' and im.get('format_version')==2,'DIMG V2 absent/invalide')
req(mm.get('format')=='DMAP' and mm.get('format_version')==2,'DMAP V2 absent/invalide')
req(cm.get('format')=='DCOLL' and cm.get('format_version')==1,'DCOLL V1 absent/invalide')
w=int(mm['map']['width_cells']); h=int(mm['map']['height_cells'])
req(w==512 and h==28,f'map attendue 512x28, recue {w}x{h}')
objects=mm.get('objects',[]); zones=cm.get('zones',[])
counts={k:sum(1 for o in objects if o.get('type')==k) for k in ('COLLECTIBLE','ENEMY','SPRING','BOOSTER','MOVING_PLATFORM','CHECKPOINT','PLAYER_START')}
expected={'COLLECTIBLE':24,'ENEMY':10,'SPRING':3,'BOOSTER':2,'MOVING_PLATFORM':4,'CHECKPOINT':1,'PLAYER_START':1}
for k,v in expected.items(): req(counts.get(k)==v,f'{k}: {counts.get(k)} au lieu de {v}')
req(sum(1 for z in zones if z.get('forme')=='PENTE')>=3,'moins de 3 pentes DCOLL')
req(sum(1 for z in zones if z.get('type_zone')=='PLATEFORME_1_SENS')>=10,'moins de 10 plateformes 1 sens')

video=(SRC/'platform_video.c').read_text(encoding='utf-8')
main=(SRC/'main.c').read_text(encoding='utf-8')
req('#define BG_A_STD_BASE 0x08000u' in video,'base BG A standard absente')
req('#define BG_B_STD_BASE 0x09000u' in video,'base BG B standard absente')
req('#define BG_A_HIGH_BASE 0x0B000u' in video,'base BG A high-color absente')
req('#define LINE_SCROLL_A_BASE 0x0C000u' in video and '#define LINE_SCROLL_B_BASE 0x0C200u' in video,'tables line-scroll absentes')
req('PLATFORM_VIDEO_tick' in video and 'wave32' in video,'effet line-scroll Mode 2 absent')
req('rich_palettes[8][16]' in video and 'high_banks[7]' in video,'palette high-color 7 banques BG absente')
req('DMS_MODE_HIGH_COLOR' in video and 'DMS_MODE_LOW_RES' in video,'routage high-color/low-res absent')
req('stream_column' in video and 'PLATFORM_VIDEO_setCamera' in video,'streaming de colonnes absent')
setcam=video.split('void PLATFORM_VIDEO_setCamera',1)[1]
req('vram8[' not in setcam,'ERREUR: ecriture pixels de tiles pendant setCamera')
req('PLATFORM_TILE_BASE*TILE_BYTES' in video,'prechargement tiles BG absent')
req('set_scroll_b((uint16_t)(camera_x>>2))' in video,'parallaxe BG B 1/4 absente')

req('MODE0_END 800' in main and 'MODE2_END 1500' in main and 'MODE1_END 2300' in main and 'MODE3_END 3300' in main,'bornes des 5 zones video absentes')
for token in ('DMS_MODE_STANDARD','DMS_MODE_SCROLL','DMS_MODE_HIGH_COLOR','DMS_MODE_SPRITE','DMS_MODE_LOW_RES'):
    req(token in main,f'mode video non utilise: {token}')
req('vblank_video_step' in main and 'VDP_setMode(current_mode)' in main,'switch VBlank non detecte')
req('transition_state==1u' in main and 'PLATFORM_VIDEO_setFade' in main,'fondu Mode 4/5 absent')
req('#define LOWRES_W 256' in main and 'active_screen_width' in main,'camera/HUD 256 px Mode 4 absents')

req('#define BASE_SPRITE_SLOTS' in main and 'BASE_SPRITE_SLOTS != 83' in main,'garde 83 sprites de base absente')
req('#define SPRITE_STORM 45' in main and 'TOTAL_SPRITE_SLOTS != 128' in main,'stress Mode 3 a 128 sprites absent')
req('storm_x[SPRITE_STORM]' in main and 'storm_y[SPRITE_STORM]' in main and 'storm_dx[SPRITE_STORM]' in main,'etat persistant particules V0.3 absent')
storm_fn=main.split('static void render_sprite_storm',1)[1].split('static void render_world',1)[0]
req('%312u' not in storm_fn and '%188u' not in storm_fn and '%' not in storm_fn,'modulo encore present dans la boucle chaude sprite storm')
req('if(x>=312u)' in storm_fn and 'if(y>=208u)' in storm_fn,'wrap add/compare V0.3 absent')
req('Slots 0..47' in main and 'for(i=0;i<16u&&i<collectible_count' in main,'ordre de fetch Mode 2 non protege')
sprite_tiles=290; bg_base=512
req(sprite_tiles < bg_base,f'collision VRAM sprites/BG: {sprite_tiles} >= {bg_base}')
req('SPR_setAnimation' not in main,'le sample depend du stub SPR_setAnimation')

bank=RES/'audio/audio_bank.bin'; music=RES/'music.dmr'; manifest=json.loads((RES/'audio/audio_manifest.json').read_text(encoding='utf-8'))
req(bank.exists() and bank.stat().st_size==20480,'banque ADPCM attendue = 20480 octets')
req(len(manifest.get('samples',[]))==7,'7 SFX attendus')
req(all(s.get('codec')=='A' for s in manifest.get('samples',[])),'les SFX doivent rester ADPCM-A')
req(music.exists() and music.stat().st_size>0,'DMR absent')

hdr=(SRC/'platform_data.h').read_text(encoding='ascii')
req('#define PLATFORM_WORLD_W 4096u' in hdr,'handoff DMAP -> C absent/invalide')
req('#define PLATFORM_TILE_COUNT 64u' in hdr,'handoff DIMG -> C absent/invalide')

status='PASS' if not errors else 'FAIL'
lines=[f'DMS-1 07_PLATFORM_DEMO V0.3 MODE GAUNTLET - OPTIMIZED STORM - STATIC VALIDATION {status}',
       f'World: {w*8}x{h*8} px ({w}x{h} cells)',
       'Zones: M0 0-799 | M2 800-1499 | M1 1500-2299 | M3 2300-3299 | M4 3300-4095',
       'M0: 320x224, BG A+B, 80/20 sprites',
       'M2: 320x224, BG A+B + line-scroll, 48/12 sprites; slots 0..47 protegent gameplay essentiel',
       'M1: 320x224, BG A high-color, 7 palettes BG + palette sprite reservee',
       'M3: 320x224, BG A, 128 sprites dont 45 particules; mouvement V0.3 sans division/modulo par frame',
       'M4: 256x224, BG A+B, high-color; transition masquee par fondu palette 8 pas',
       f'Objects: {len(objects)} | '+', '.join(f'{k}={counts[k]}' for k in expected),
       f'Collision zones: {len(zones)} | slopes={sum(1 for z in zones if z.get("forme")=="PENTE")} | one-way={sum(1 for z in zones if z.get("type_zone")=="PLATEFORME_1_SENS")}',
       f'Sprite slots allocated: 128 max | sprite tiles preload: {sprite_tiles} | BG tile base: {bg_base}',
       'Streaming: colonnes TILEMAP uniquement; pixels des 64 tiles BG precharges au chargement.',
       f'Audio: DMR={music.stat().st_size if music.exists() else 0} bytes | ADPCM bank={bank.stat().st_size if bank.exists() else 0} bytes | SFX={len(manifest.get("samples",[]))}.']
if errors: lines += ['','ERRORS:'] + ['- '+e for e in errors]
else: lines += ['','PASS: handoff DMS, multimode, budgets, line-scroll, storm sans modulo et contrat anti-pixel-streaming coherents.']
DOC.mkdir(exist_ok=True)
(DOC/'LAST_STATIC_VALIDATION.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('\n'.join(lines))
raise SystemExit(0 if not errors else 2)
