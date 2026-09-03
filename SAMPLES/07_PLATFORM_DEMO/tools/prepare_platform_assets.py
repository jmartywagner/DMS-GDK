#!/usr/bin/env python3
from __future__ import annotations
import json, struct, sys, zipfile
from pathlib import Path

PROJECT=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
RES=PROJECT/'res'; SRC=PROJECT/'src'; DOC=PROJECT/'DOCS_REPORTS'
DIMG=RES/'platform_tiles.dimg'; DMAP=RES/'stage_platform.dmap'; DCOLL=RES/'stage_platform.dcoll'
TILE_BASE=512

def read_json(z,n): return json.loads(z.read(n).decode('utf-8'))
def be_words(raw):
    if len(raw)%2: raise ValueError('flux word impair')
    return list(struct.unpack('>'+('H'*(len(raw)//2)),raw))
def final_word(interim,priority_code):
    tid=(interim&0x03FF)+TILE_BASE
    if tid>1023: raise ValueError(f'tile finale >1023: {tid}')
    pal=interim&0x1C00
    fx=0x4000 if interim&0x2000 else 0
    fy=0x8000 if interim&0x4000 else 0
    front=0x2000 if priority_code in (1,3) else 0
    return (tid&0x03FF)|pal|front|fx|fy

def c_u8(name,data,cols=16):
    out=[f'const uint8_t {name}[{len(data)}] = {{']
    for i in range(0,len(data),cols): out.append('    '+', '.join(f'0x{x:02X}' for x in data[i:i+cols])+',')
    out.append('};\n'); return '\n'.join(out)
def c_u16(name,data,cols=10):
    out=[f'const uint16_t {name}[{len(data)}] = {{']
    for i in range(0,len(data),cols): out.append('    '+', '.join(f'0x{x:04X}' for x in data[i:i+cols])+',')
    out.append('};\n'); return '\n'.join(out)

with zipfile.ZipFile(DIMG) as z:
    im=read_json(z,'manifest.json'); tiles=z.read('tiles.bin'); pals=be_words(z.read('palettes.bin')); pids=list(z.read('palette_ids.bin'))
if im.get('format')!='DIMG' or im.get('format_version')!=2: raise SystemExit('DIMG V2 requis')
tile_count=len(tiles)//32
if tile_count<=0 or TILE_BASE+tile_count>1024: raise SystemExit(f'budget tiles invalide: {tile_count}')
if len(pids)>4 or any(x>3 for x in pids): raise SystemExit('Mode 0: seulement P0..P3')

with zipfile.ZipFile(DMAP) as z:
    mm=read_json(z,'manifest.json'); a0=be_words(z.read('bg_a.bin')); b0=be_words(z.read('bg_b.bin')); pa=list(z.read('priority_a.bin')); pb=list(z.read('priority_b.bin')); objects=read_json(z,'objects.json')
if mm.get('format')!='DMAP' or mm.get('format_version')!=2: raise SystemExit('DMAP V2 requis')
w=int(mm['map']['width_cells']);h=int(mm['map']['height_cells'])
if w<64 or h>32: raise SystemExit(f'grande map attendue >=64 cols et <=32 rows; reçu {w}x{h}')
if len(a0)!=w*h or len(b0)!=w*h or len(pa)!=w*h or len(pb)!=w*h: raise SystemExit('DMAP taille incoherente')
a=[final_word(x,p) for x,p in zip(a0,pa)];b=[final_word(x,p) for x,p in zip(b0,pb)]

TYPE_NAMES=['SOLIDE','PLATEFORME_1_SENS','DANGER','ECHELLE','EAU','RALENTISSEMENT','DECLENCHEUR','SORTIE','CHECKPOINT','PERSONNALISE']
SHAPE_NAMES=['RECTANGLE','SEGMENT','PENTE','POLYGONE','POINT']
with zipfile.ZipFile(DCOLL) as z: cm=read_json(z,'manifest.json')
if cm.get('format')!='DCOLL' or cm.get('format_version')!=1: raise SystemExit('DCOLL V1 requis')
zones=cm.get('zones',[])
if not any(q.get('forme')=='PENTE' for q in zones): raise SystemExit('DCOLL sans pente')

obj_types={'COLLECTIBLE':1,'ENEMY':2,'SPRING':3,'BOOSTER':4,'MOVING_PLATFORM':5,'CHECKPOINT':6,'PLAYER_START':7}
objs=[]
for o in objects:
    typ=obj_types.get(o.get('type'),0)
    p1=int(o.get('patrol',o.get('range',o.get('direction',0))))
    p2=1 if o.get('axis')=='Y' else 0
    objs.append((int(o['x']),int(o['y']),typ,p1,p2))

# Compact collision table generated FROM the DCOLL source.
cz=[]
for z in zones:
    pts=z.get('points') or [[0,0],[0,0]]
    bnd=z.get('bounds') or [0,0,0,0]
    p0=pts[0];p1=pts[-1]
    cz.append((int(bnd[0]),int(bnd[1]),int(bnd[2]),int(bnd[3]),TYPE_NAMES.index(z['type_zone']),SHAPE_NAMES.index(z['forme']),int(p0[0]),int(p0[1]),int(p1[0]),int(p1[1]),int(z.get('cible_mask',1))))

header=f'''#ifndef DMS1_PLATFORM_DATA_H\n#define DMS1_PLATFORM_DATA_H\n#include <stdint.h>\n#define PLATFORM_WORLD_CELLS_W {w}u\n#define PLATFORM_WORLD_CELLS_H {h}u\n#define PLATFORM_WORLD_W {w*8}u\n#define PLATFORM_WORLD_H {h*8}u\n#define PLATFORM_TILE_BASE {TILE_BASE}u\n#define PLATFORM_TILE_COUNT {tile_count}u\n#define PLATFORM_OBJECT_COUNT {len(objs)}u\n#define PLATFORM_ZONE_COUNT {len(cz)}u\n\nenum {{ POBJ_NONE=0, POBJ_COLLECTIBLE=1, POBJ_ENEMY=2, POBJ_SPRING=3, POBJ_BOOSTER=4, POBJ_MOVING_PLATFORM=5, POBJ_CHECKPOINT=6, POBJ_PLAYER_START=7 }};\nenum {{ PCOLL_SOLID=0, PCOLL_ONEWAY=1, PCOLL_DANGER=2, PCOLL_LADDER=3, PCOLL_WATER=4, PCOLL_SLOW=5, PCOLL_TRIGGER=6, PCOLL_EXIT=7, PCOLL_CHECKPOINT=8, PCOLL_CUSTOM=9 }};\nenum {{ PSHAPE_RECT=0, PSHAPE_SEGMENT=1, PSHAPE_SLOPE=2, PSHAPE_POLYGON=3, PSHAPE_POINT=4 }};\ntypedef struct {{ int16_t x,y; uint8_t type; int16_t param1; uint8_t param2; }} PlatformObjectDef;\ntypedef struct {{ int16_t x0,y0,x1,y1; uint8_t type,shape; int16_t ax,ay,bx,by; uint8_t target_mask; }} PlatformZoneDef;\nextern const uint8_t platform_tiles[];\nextern const uint16_t platform_palettes[3][16];\nextern const uint16_t platform_map_a[PLATFORM_WORLD_CELLS_W*PLATFORM_WORLD_CELLS_H];\nextern const uint16_t platform_map_b[PLATFORM_WORLD_CELLS_W*PLATFORM_WORLD_CELLS_H];\nextern const PlatformObjectDef platform_objects[PLATFORM_OBJECT_COUNT];\nextern const PlatformZoneDef platform_zones[PLATFORM_ZONE_COUNT];\n#endif\n'''
SRC.joinpath('platform_data.h').write_text(header,encoding='ascii')

src=['#include <stdint.h>','#include "platform_data.h"','']
src.append(c_u8('platform_tiles',tiles))
# palettes map exact P0..P2 in file order
pp=[]
for pi in range(3):
    if pi in pids:
        ix=pids.index(pi); pp.append(pals[ix*16:(ix+1)*16])
    else: pp.append([0]*16)
src.append('const uint16_t platform_palettes[3][16] = {')
for row in pp: src.append('    {'+', '.join(f'0x{x:03X}' for x in row)+'},')
src.append('};\n')
src.append(c_u16('platform_map_a',a));src.append(c_u16('platform_map_b',b))
src.append('const PlatformObjectDef platform_objects[PLATFORM_OBJECT_COUNT] = {')
for x,y,t,p1,p2 in objs: src.append(f'    {{{x},{y},{t}u,{p1},{p2}u}},')
src.append('};\n')
src.append('const PlatformZoneDef platform_zones[PLATFORM_ZONE_COUNT] = {')
for q in cz: src.append('    {'+','.join(str(v) + ('u' if i in (4,5,10) else '') for i,v in enumerate(q))+'},')
src.append('};\n')
SRC.joinpath('platform_data.c').write_text('\n'.join(src),encoding='ascii')

report=(f'DMS-1 07_PLATFORM_DEMO - ASSET HANDOFF PASS\n'
        f'DIMG V2: {tile_count} tiles -> VRAM tiles {TILE_BASE}..{TILE_BASE+tile_count-1}\n'
        f'DMAP V2: {w}x{h} cells / {w*8}x{h*8} px / BG A+B\n'
        f'DCOLL V1: {len(cz)} zones / slopes={sum(1 for z in zones if z.get("forme")=="PENTE")} / one-way={sum(1 for z in zones if z.get("type_zone")=="PLATEFORME_1_SENS")}\n'
        f'Objects: {len(objs)}\n'
        'Runtime strategy: graphismes precharges; streaming de colonnes TILEMAP uniquement.\n')
DOC.mkdir(exist_ok=True);DOC.joinpath('LAST_ASSET_HANDOFF_REPORT.txt').write_text(report,encoding='utf-8')
print(report,end='')
