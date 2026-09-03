#!/usr/bin/env python3
"""DMS-GDK P1.1 integrated game build.

This is the first resource-aware cartridge builder. It consumes dmsres output,
keeps the source DMS formats intact, builds a genuine 68000 bootstrap ROM,
uses the locked P1.0.9 VDP/runtime, keeps the DMR native music stream, and
extends the Z80 mailbox with ADPCM-A/B gameplay SFX.

The current CPU frontend is deliberately a bootstrap, not a fake ISO-C toolchain.
A real m68k-gcc can replace this stage later without changing resources.dmsres,
DMC2, DMR, the Z80 driver or the public GDK headers.
"""
from __future__ import annotations
import argparse, json, math, sys, zipfile
from pathlib import Path

HERE=Path(__file__).resolve(); GDK_ROOT=HERE.parents[1]; PACKAGE_ROOT=HERE.parents[2]
RUNTIME=PACKAGE_ROOT/'RUNTIME'; sys.path.insert(0,str(RUNTIME/'tools')); sys.path.insert(0,str(HERE.parent))
from dmsres import compile_project, symbol
from dms_console90_firmware import Asm68k, build_z80_native_driver
from dms_console90_format import save_image
from dms_z80_native import build_native_commands, pack_banked_stream
from dms_console90_vdp import MODE_PROFILES, PATTERN_BASE, BG_A_STANDARD_BASE, BG_B_STANDARD_BASE, BG_A_HIGH_BASE, SPRITE_TABLE_BASE, SPR_SIZE16, SPR_PRIORITY

WORK_X=0x100000; WORK_Y=0x100002; WORK_PREV_PAD=0x100006
VRAM_BASE=0x200000; CRAM_BASE=0x220000; VDP_MODE=0x300002
PAD0=0x400000; MAIL=0x500000
PAD_UP=1; PAD_DOWN=2; PAD_LEFT=4; PAD_RIGHT=8; PAD_A=0x10; PAD_B=0x20; PAD_C=0x40; PAD_START=0x80

class BuildError(RuntimeError): pass

def flip_tile(raw:bytes, fx:bool, fy:bool)->bytes:
    if len(raw)!=32: raise BuildError('tile DRES doit faire 32 octets')
    px=[]
    for b in raw: px += [(b>>4)&15,b&15]
    rows=[px[i*8:(i+1)*8] for i in range(8)]
    if fy: rows.reverse()
    if fx: rows=[list(reversed(r)) for r in rows]
    vals=[v for r in rows for v in r]; out=bytearray()
    for i in range(0,64,2): out.append((vals[i]<<4)|vals[i+1])
    return bytes(out)

def _first(entries,kind): return next((e for e in entries if e.kind==kind),None)

def actor_speed(entries)->int:
    e=_first(entries,'ACTOR')
    if not e: return 1
    a=(e.manifest or {}).get('actor') or {}
    try: return max(1,min(4,int(round(float((a.get('mouvement') or {}).get('vitesse_max_x',1))))))
    except Exception: return 1

def collect_visual(entries):
    map_e=_first(entries,'MAP'); spr_e=_first(entries,'SPRITE')
    if not map_e: raise BuildError('une MAP DMAP est requise pour la démo pipeline')
    tileset_name=symbol(map_e.options.get('TILESET','')) if map_e.options.get('TILESET') else ''
    if not tileset_name:
        raise BuildError(f'{map_e.name}: TILESET=<IMAGE> requis dans resources.dmsres')
    img_e=next((e for e in entries if e.kind=='IMAGE' and e.name==tileset_name),None)
    if not img_e:
        raise BuildError(f'{map_e.name}: TILESET={tileset_name} ne référence aucune IMAGE DIMG')
    if int(map_e.compiled.get('width',0))>64 or int(map_e.compiled.get('height',0))>32:
        raise BuildError('grande DMAP: utiliser le builder GCC P1.2/P0.3 (streaming libdms natif)')
    mode=int(map_e.compiled['mode']); prof=MODE_PROFILES[mode]
    tiles=bytearray(img_e.payloads['tiles.bin'])
    if len(tiles)%32: raise BuildError('tiles.bin DIMG non aligné sur 32 octets')
    tile_count=len(tiles)//32
    if tile_count>1019: raise BuildError('tileset trop grand pour réserver un sprite 16x16')

    cram=bytearray(256)
    pids=list(img_e.payloads['palette_ids.bin'])
    pb=img_e.payloads['palettes.bin']
    for i,pid in enumerate(pids):
        if pid>=prof.palettes: raise BuildError(f'P{pid} interdite en mode {mode}')
        bank=pb[i*32:(i+1)*32]
        cram[pid*32:pid*32+len(bank)]=bank

    sprite_tile=0; sprite_pal=2; sprite_size16=False
    if spr_e:
        m=spr_e.manifest or {}; frames=m.get('frames') or []
        if not frames: raise BuildError('DRES sans frame')
        fi=0; fr=frames[fi]; w=int(fr.get('width',0)); h=int(fr.get('height',0))
        if (w,h)!=(16,16): raise BuildError('runtime P1.1: le sample pipeline accepte une première frame DRES 16x16')
        cells=[c for c in (m.get('cells') or []) if int(c.get('frame',-1))==fi and not c.get('empty')]
        if len(cells)>4: raise BuildError('runtime P1.1: frame DRES 16x16 limitée à 4 cellules 8x8')
        palset={int(c.get('palette',0)) for c in cells}
        if len(palset)>1: raise BuildError('runtime P1.1: un sprite hardware 16x16 doit utiliser une seule palette')
        localpal=next(iter(palset),0); sprite_pal=int(spr_e.options.get('PALETTE_BASE','2'))+localpal
        if sprite_pal>=prof.palettes: raise BuildError(f'palette sprite P{sprite_pal} interdite en mode {mode}')
        spb=spr_e.payloads['palettes.bin']; bank=spb[localpal*32:(localpal+1)*32]
        cram[sprite_pal*32:sprite_pal*32+len(bank)]=bank
        dtiles=spr_e.payloads['tiles.bin']; pieces={}
        for c in cells:
            tid=c.get('tile');
            if tid is None: continue
            tid=int(tid); raw=dtiles[tid*32:(tid+1)*32]
            raw=flip_tile(raw,bool(c.get('flip_x')),bool(c.get('flip_y')))
            pieces[(int(c.get('x',0))//8,int(c.get('y',0))//8)]=raw
        sprite_tile=tile_count
        for ty in range(2):
            for tx in range(2): tiles += pieces.get((tx,ty),bytes(32))
        sprite_size16=True

    bg_a=map_e.compiled['vdp_bg_a']; bg_b=map_e.compiled['vdp_bg_b']
    regions=[(PATTERN_BASE,bytes(tiles)),(prof.bg_a_base,bg_a)]
    if prof.bg_b_base is not None: regions.append((prof.bg_b_base,bg_b))
    if sprite_size16:
        table=bytearray([0xFF]*(128*8)); x=152; y=96
        table[0:2]=(y&0x1ff).to_bytes(2,'big'); table[2:4]=(x&0x1ff).to_bytes(2,'big')
        table[4:6]=(sprite_tile&0x3ff).to_bytes(2,'big')
        table[6:8]=(sprite_pal|SPR_PRIORITY|SPR_SIZE16).to_bytes(2,'big')
        regions.append((SPRITE_TABLE_BASE,bytes(table)))
    return mode,bytes(cram),regions,sprite_size16

def choose_music(entries):
    e=_first(entries,'MUSIC'); return e.payloads['music.dmr'] if e else b''

def choose_audio(entries,dmr_len):
    e=_first(entries,'AUDIO')
    if not e: return b'',None,[]
    bank=e.payloads['audio_bank.bin']; base=(dmr_len+255)//256
    sfx=e.compiled.get('sfx',[])
    wanted=symbol(e.options.get('DEFAULT_SFX','')) if e.options.get('DEFAULT_SFX') else ''
    sample=None
    if wanted:
        sample=next((s for s in sfx if symbol(str(s.get('name','')))==wanted and s.get('kind')=='SAMPLE'),None)
        if sample is None:
            raise BuildError(f'{e.name}: DEFAULT_SFX={wanted} introuvable ou non SAMPLE')
    if sample is None:
        sample=next((s for s in sfx if s.get('kind')=='SAMPLE' and s.get('codec') in ('A','B')),None)
    if sample:
        sample=dict(sample); sample['abs_start_page']=base+int(sample['start_page']); sample['abs_end_page']=base+int(sample['end_page'])
    return bank,sample,sfx

def delta_n(rate:int)->int:
    # ADPCM-B service clock fixed by the DMS audio core at ~55.56 kHz.
    return max(1,min(0xFFFF,int(round(max(1,rate)*65536/55556.0))))

def build_m68k(mode:int, cram:bytes, regions, has_sprite:bool, speed:int, sfx)->bytes:
    entry=0x100; image=bytearray(entry); image[0:4]=(0x10FFFC).to_bytes(4,'big'); image[4:8]=entry.to_bytes(4,'big')
    for v in range(2,64): image[v*4:v*4+4]=entry.to_bytes(4,'big')
    a=Asm68k(entry)
    for off in range(0,len(cram),2): a.move_w_imm_abs(int.from_bytes(cram[off:off+2],'big'),CRAM_BASE+off)
    for base,data in regions:
        for i,b in enumerate(data): a.move_b_imm_abs(b,VRAM_BASE+base+i)
    a.move_w_imm_abs(152,WORK_X); a.move_w_imm_abs(96,WORK_Y); a.move_b_imm_abs(0,WORK_PREV_PAD)
    # Audio SFX parameters are static metadata; gameplay only sends intention CMD=3.
    if sfx:
        codec=1 if sfx.get('codec')=='A' else 2; sp=int(sfx['abs_start_page']); ep=int(sfx['abs_end_page'])
        lev=int(sfx.get('level',0 if codec==1 else 224))&255; pan=int(sfx.get('pan',0xC0))&255
        dn=delta_n(int(sfx.get('rate_hz',26000))) if codec==2 else 0
        for off,val in [(3,codec),(4,sp&255),(5,(sp>>8)&255),(6,ep&255),(7,(ep>>8)&255),(8,lev),(9,pan),(10,dn&255),(11,(dn>>8)&255),(12,0)]:
            a.move_b_imm_abs(val,MAIL+off)
    # Auto-start music once; this mirrors MUS_play from the sample main.c.
    a.move_b_imm_abs(1,MAIL+0)
    a.label('frame'); a.stop(0x2000)
    # First legal VBlank commits video mode.
    a.move_b_abs_dn(MAIL+0x20,0); a.cmpi_b(0,0); a.branch('bne','mode_done')
    a.move_b_imm_abs(mode,VDP_MODE); a.move_b_imm_abs(1,MAIL+0x20)
    a.label('mode_done')
    if has_sprite:
        for bit,work,inc,label in [(PAD_LEFT,WORK_X,False,'l'),(PAD_RIGHT,WORK_X,True,'r'),(PAD_UP,WORK_Y,False,'u'),(PAD_DOWN,WORK_Y,True,'d')]:
            a.move_b_abs_dn(PAD0,0); a.andi_b(bit,0); a.branch('beq',f'no_{label}')
            a.move_w_abs_dn(work,0)
            for _ in range(speed): (a.addq_w_1(0) if inc else a.subq_w_1(0))
            a.move_w_dn_abs(0,work); a.label(f'no_{label}')
        a.move_w_abs_dn(WORK_Y,0); a.move_w_dn_abs(0,VRAM_BASE+SPRITE_TABLE_BASE)
        a.move_w_abs_dn(WORK_X,0); a.move_w_dn_abs(0,VRAM_BASE+SPRITE_TABLE_BASE+2)
    # Edge-trigger A=music, B=stop, C=SFX. START also restarts music.
    for bit,cmd,label in [(PAD_A,1,'a'),(PAD_B,2,'b'),(PAD_START,1,'start')]:
        a.move_b_abs_dn(PAD0,0); a.andi_b(bit,0); a.branch('beq',f'no_{label}')
        a.move_b_abs_dn(WORK_PREV_PAD,0); a.andi_b(bit,0); a.branch('bne',f'no_{label}')
        a.move_b_imm_abs(cmd,MAIL); a.label(f'no_{label}')
    if sfx:
        a.move_b_abs_dn(PAD0,0); a.andi_b(PAD_C,0); a.branch('beq','no_c')
        a.move_b_abs_dn(WORK_PREV_PAD,0); a.andi_b(PAD_C,0); a.branch('bne','no_c')
        a.move_b_imm_abs(3,MAIL); a.label('no_c')
    a.move_b_abs_dn(PAD0,0); a.move_b_dn_abs(0,WORK_PREV_PAD); a.branch('bra','frame')
    image += a.finish(); return bytes(image)

def build(project:Path,out:Path,generated:Path)->dict:
    res=compile_project(project,generated)
    errs=[d for d in res.diagnostics if d.severity=='ERROR']
    if errs: raise BuildError('; '.join(f'{d.resource}: {d.message}' for d in errs))
    mode,cram,regions,has_sprite=collect_visual(res.entries)
    dmr=choose_music(res.entries)
    if not dmr: raise BuildError('une ressource MUSIC DMR est requise dans cette grosse build')
    commands,_,_=build_native_commands(dmr); ndrv=pack_banked_stream(commands)
    bank,sample,sfx=choose_audio(res.entries,len(dmr))
    m68k=build_m68k(mode,cram,regions,has_sprite,actor_speed(res.entries),sample)
    meta={'format':'DMS-GDK-P1.1-BIG-BUILD','source_project':str(project),'runtime':'DMS1 Console90 P1.0.9 FINAL RUNTIME LOCK','video_mode':mode,
          'resources':[{'kind':e.kind,'name':e.name,'source':e.path.name} for e in res.entries],
          'resource_compiler':'dmsres P1.1','cpu_frontend':'bootstrap genuine-68000 opcodes; m68k-gcc integration pending','audio_runtime':'DMR + Z80 mailbox ADPCM-A/B SFX extension'}
    chunks=[(b'M68K',m68k),(b'Z80 ',build_z80_native_driver()),(b'META',json.dumps(meta,indent=2,ensure_ascii=False).encode()),(b'DMR0',dmr),(b'NDRV',ndrv),
            (b'RSRC',(generated/'resources.bin').read_bytes())]
    if bank: chunks += [(b'ABNK',bank),(b'ASFX',json.dumps(sfx,ensure_ascii=False).encode())]
    out.parent.mkdir(parents=True,exist_ok=True); save_image(out,chunks)
    report={'rom':str(out),'rom_bytes':out.stat().st_size,'m68k_bytes':len(m68k),'z80_bytes':len(chunks[1][1]),'dmr_bytes':len(dmr),'audio_bank_bytes':len(bank),
            'resources':len(res.entries),'mode':mode,'sprite_runtime':has_sprite,'sfx_sample':sample,'warnings':[d.message for d in res.diagnostics if d.severity=='WARN']}
    (out.parent/'BUILD_GAME_REPORT.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    return report

def main()->int:
    ap=argparse.ArgumentParser(description='DMS-GDK P1.1 integrated builder'); ap.add_argument('project',type=Path); ap.add_argument('--out',type=Path,default=None); ap.add_argument('--generated',type=Path,default=None)
    a=ap.parse_args(); project=a.project.resolve(); builddir=project.parent/'build'; out=(a.out or builddir/(project.parent.name+'.dmc')).resolve(); gen=(a.generated or builddir/'generated').resolve()
    try:
        r=build(project,out,gen); print(f"DMSBUILD: PASS -> {out}"); print(f"ROM {r['rom_bytes']} octets | {r['resources']} ressources | mode {r['mode']} | SFX sample {'oui' if r['sfx_sample'] else 'non'}"); return 0
    except Exception as exc:
        print('ERREUR DMSBUILD:',exc,file=sys.stderr); return 2
if __name__=='__main__': raise SystemExit(main())
