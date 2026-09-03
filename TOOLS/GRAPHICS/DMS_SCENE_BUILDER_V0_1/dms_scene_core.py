#!/usr/bin/env python3
from __future__ import annotations
import json, os, re, shutil, struct, sys, zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

APP_VERSION="1.1.1"
GDK_ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(GDK_ROOT/"GDK"/"tools"))
import dmsscenec
MODE_INFO={
0:{"name":"STANDARD","width":320,"height":224,"palettes":4,"bg_b":True,"sprites":80,"scanline":20},
1:{"name":"HIGH COLOR","width":320,"height":224,"palettes":8,"bg_b":False,"sprites":80,"scanline":20},
2:{"name":"SCROLL","width":320,"height":224,"palettes":4,"bg_b":True,"sprites":48,"scanline":12},
3:{"name":"SPRITE","width":320,"height":224,"palettes":4,"bg_b":False,"sprites":128,"scanline":32},
4:{"name":"LOW RES","width":256,"height":224,"palettes":8,"bg_b":True,"sprites":96,"scanline":24},
}
FX_ORDER=[
"NONE","SHAKE","KICK","FLASH","FADE_OUT","FADE_IN","PULSE","COLOR_CYCLE","WATER_WAVE","RIPPLE","HEAT_HAZE","SHEAR_WOBBLE","RASTER_SPLIT","SCAN_SWEEP","SPEED_BANDS","BG_PARALLAX_OSC","PALETTE_INVERT","PALETTE_TINT","PALETTE_DESATURATE","PALETTE_STROBE","HIT_FREEZE_VISUAL","EARTHQUAKE_RASTER","PERSPECTIVE_WARP","UNDERWATER_DRIFT","PARALLAX_KICK","BG_DEPTH_SWAY"]
FX_ID={n:i for i,n in enumerate(FX_ORDER)}
MODE2_FX={"WATER_WAVE","RIPPLE","HEAT_HAZE","SHEAR_WOBBLE","RASTER_SPLIT","SCAN_SWEEP","SPEED_BANDS","EARTHQUAKE_RASTER","PERSPECTIVE_WARP","UNDERWATER_DRIFT"}
BG2_FX={"BG_PARALLAX_OSC","PARALLAX_KICK","BG_DEPTH_SWAY"}
OPS={"SHOW":1,"HIDE":2,"TYPEWRITER":3,"SLIDE_IN":4,"FX_START":5,"MUSIC_PLAY":6,"MUSIC_STOP":7,"SFX_PLAY":8,"MENU_ENABLE":9,"WAIT_INPUT":10,"END":11,
"CAMERA_SET":12,"CAMERA_SPEED":13,"SCROLL_SET":14,"VIDEO_MODE":15,"TRIGGER":16,"SPAWN_FORMATION":17,"CHECKPOINT":18,"FLOW_EMIT":19}
KINDS={"TEXT":1,"MENU_ITEM":2,"UI":2}
WAIT_BITS={"A":0x10,"B":0x20,"+":0x40,"×":0x80,"C":0x40,"START":0x80,"X":0x80,"ANY":0xF0}

class SceneError(RuntimeError): pass

def symbol(s:str)->str:
    s=re.sub(r"[^A-Za-z0-9_]+","_",s.upper()).strip("_") or "SCENE"
    return "S_"+s if s[0].isdigit() else s

def default_scene()->dict[str,Any]:
    return {"format":"DSCENE","format_version":2,"name":"TITLE","type":"MENU","video_mode":0,
            "map":"","parallax":{"a_x":1.0,"a_y":1.0,"b_x":0.25,"b_y":0.125},"budgets":{"scanline_cells":4},
            "scroll":{"a_x":0,"a_y":0,"b_x":0,"b_y":0},"camera":{"x":0,"y":0,"speed_x":0.0,"speed_y":0.0},
            "font":{"source":"BUILTIN_5X7","palette_ids":[3,2]},
            "audio_dir":"","menu_move_sfx":0,"menu_validate_sfx":0,
            "objects":[{"id":"TITLE","kind":"TEXT","layer":"UI","text":"DMS SCENE BUILDER","x":88,"y":56,"palette":3,"selected_palette":2,"priority":True,"visible":True,"screen_space":True,"start_frame":0,"end_frame":0,"start_trigger":"","end_trigger":""}],
            "events":[]}

def load_scene(path:Path)->dict[str,Any]:
    raw=json.loads(path.read_text(encoding="utf-8-sig")); old_version=int(raw.get("format_version",0))
    data,warnings=dmsscenec.normalize_scene(raw)
    data["_path"]=str(path.resolve())
    data["_warnings"]=warnings
    if old_version==1:data["_migrated_from_version"]=1
    return data

def save_scene(path:Path,data:dict[str,Any])->None:
    out={k:v for k,v in data.items() if not k.startswith("_")}
    out,_=dmsscenec.normalize_scene(out);out["format"]="DSCENE"; out["format_version"]=2
    path.parent.mkdir(parents=True,exist_ok=True)
    if data.get("_migrated_from_version")==1 and path.exists() and not data.get("_migration_backup_done"):
        stamp=datetime.now().strftime("%Y%m%d_%H%M%S");backup=path.with_name(path.stem+f".dscene_v1_backup_{stamp}"+path.suffix)
        shutil.copy2(path,backup);data["_migration_backup_done"]=str(backup)
    temp=path.with_suffix(path.suffix+".tmp");temp.write_text(json.dumps(out,indent=2,ensure_ascii=False)+"\n",encoding="utf-8");os.replace(temp,path)

def resolve(scene:dict[str,Any],value:str)->Path:
    p=Path(value)
    if p.is_absolute(): return p
    sp=scene.get("_path")
    base=Path(sp).parent if sp else Path.cwd()
    return (base/p).resolve()

def _unpack_words(data:bytes)->list[int]:
    if len(data)%2: raise SceneError("flux 16-bit non aligné")
    return list(struct.unpack(">"+"H"*(len(data)//2),data)) if data else []

def _flip_tile(raw:bytes,fx:bool,fy:bool)->bytes:
    px=[]
    for b in raw: px += [(b>>4)&15,b&15]
    rows=[px[i*8:(i+1)*8] for i in range(8)]
    if fy: rows.reverse()
    if fx: rows=[list(reversed(r)) for r in rows]
    flat=[v for r in rows for v in r]; out=bytearray()
    for i in range(0,64,2): out.append((flat[i]<<4)|flat[i+1])
    return bytes(out)

def load_dimg(path:Path)->dict[str,Any]:
    if not path.is_file() or not zipfile.is_zipfile(path): raise SceneError(f"DIMG absent/invalide: {path}")
    with zipfile.ZipFile(path) as z:
        m=json.loads(z.read("manifest.json"));
        if m.get("format")!="DIMG" or int(m.get("format_version",0))!=2: raise SceneError(f"{path.name}: DIMG v2 requis")
        tiles=z.read("tiles.bin"); pals=_unpack_words(z.read("palettes.bin")); pids=list(z.read("palette_ids.bin")); rawmap=z.read("tilemap.bin")
    mode=m.get("mode") or {}; w=int(mode.get("w",320)); h=int(mode.get("h",224)); mw=w//8; mh=h//8
    words=_unpack_words(rawmap)
    if len(words)!=mw*mh:
        # The current converter also makes tileset-only DIMG; those are not a complete Scene background.
        raise SceneError(f"{path.name}: DIMG tileset ({len(words)} cases), pas une image plein écran {mw}x{mh}")
    final=[]
    for q in words:
        tid=q&0x03FF; pid=(q>>10)&7; fx=(q>>13)&1; fy=(q>>14)&1
        final.append(tid|(pid<<10)|(0x4000 if fx else 0)|(0x8000 if fy else 0))
    return {"manifest":m,"tiles":tiles,"tile_count":len(tiles)//32,"palettes":pals,"palette_ids":pids,"map":final,"map_w":mw,"map_h":mh}

def load_dmap_preview(scene:dict[str,Any])->dict[str,Any]|None:
    ref=str(scene.get("map","")).strip()
    if not ref or not any(c in ref for c in ("/","\\",".")):return None
    path=resolve(scene,ref)
    if not path.is_file() or not zipfile.is_zipfile(path):return None
    with zipfile.ZipFile(path) as z:m=json.loads(z.read("manifest.json"))
    if m.get("format")!="DMAP" or int(m.get("format_version",0))!=2:return None
    source=str((m.get("tileset") or {}).get("source","")).strip()
    dimg=(path.parent/source).resolve()
    if not dimg.is_file() or not zipfile.is_zipfile(dimg):return None
    with zipfile.ZipFile(dimg) as z:
        im=json.loads(z.read("manifest.json"));tiles=z.read("tiles.bin");pals=_unpack_words(z.read("palettes.bin"));pids=list(z.read("palette_ids.bin"))
    if im.get("format")!="DIMG":return None
    physical={int(pid):pals[i*16:(i+1)*16] for i,pid in enumerate(pids)}
    mode=MODE_INFO.get(int(scene.get("video_mode",0)),MODE_INFO[0]);w=mode["width"];h=224
    behind=[None]*(w*h);front=[None]*(w*h);layers=m.get("layers") or {};scroll=scene.get("scroll") or {}
    def paint(layer_name:str,plane_b:bool)->None:
        grid=layers.get(layer_name) or [];sx=int(scroll.get("b_x" if plane_b else "a_x",0));sy=int(scroll.get("b_y" if plane_b else "a_y",0));cell_x=sx//8;cell_y=sy//8;off_x=-(sx&7);off_y=-(sy&7)
        for yy in range((h+15)//8):
            gy=cell_y+yy
            if gy<0 or gy>=len(grid):continue
            row=grid[gy]
            for xx in range((w+15)//8):
                gx=cell_x+xx
                if gx<0 or gx>=len(row):continue
                c=row[gx] or {};tid=int(c.get("tile_id",c.get("tile",-1)))
                if tid<0:continue
                pal=int(c.get("palette",0));bank=physical.get(pal,[0]*16);raw=tiles[tid*32:(tid+1)*32]
                if len(raw)!=32:continue
                fx=bool(c.get("flip_x"));fy=bool(c.get("flip_y"));priority=int(c.get("priority_code",0)) in (1,3);target=front if priority else behind
                for py in range(8):
                    dy=off_y+yy*8+py
                    if not 0<=dy<h:continue
                    ty=7-py if fy else py
                    for px in range(8):
                        dx=off_x+xx*8+px
                        if not 0<=dx<w:continue
                        tx=7-px if fx else px;b=raw[ty*4+(tx>>1)];ci=(b&15) if tx&1 else (b>>4)
                        if ci:target[dy*w+dx]=(bank+[0]*16)[ci]
    if mode["bg_b"]:paint("BG_B",True)
    paint("BG_A",False)
    return {"width":w,"height":h,"behind":behind,"front":front,"map":m,"tileset":dimg.name}

DIGITS=[14,17,19,21,25,17,14,4,12,4,4,4,4,14,14,17,1,2,4,8,31,30,1,1,14,1,1,30,2,6,10,18,31,2,2,31,16,16,30,1,1,30,14,16,16,30,17,17,14,31,1,2,4,8,8,8,14,17,17,14,17,17,14,14,17,17,15,1,1,14]
LETTERS=[14,17,17,31,17,17,17,30,17,17,30,17,17,30,14,17,16,16,16,17,14,30,17,17,17,17,17,30,31,16,16,30,16,16,31,31,16,16,30,16,16,16,14,17,16,23,17,17,15,17,17,17,31,17,17,17,14,4,4,4,4,4,14,7,2,2,2,18,18,12,17,18,20,24,20,18,17,16,16,16,16,16,16,31,17,27,21,21,17,17,17,17,25,21,19,17,17,17,14,17,17,17,17,17,14,30,17,17,30,16,16,16,14,17,17,17,21,18,13,30,17,17,30,20,18,17,15,16,16,14,1,1,30,31,4,4,4,4,4,4,17,17,17,17,17,17,14,17,17,17,17,17,10,4,17,17,17,21,21,21,10,17,17,10,4,10,17,17,17,17,10,4,4,4,4,31,1,2,4,8,16,31]

def _glyph_rows(ch:str)->list[int]:
    if '0'<=ch<='9': return DIGITS[(ord(ch)-48)*7:(ord(ch)-47)*7]
    if 'A'<=ch<='Z': return LETTERS[(ord(ch)-65)*7:(ord(ch)-64)*7]
    c=[0]*7
    if ch=='-': c[3]=31
    elif ch=='_': c[6]=31
    elif ch=='/': c=[1,2,2,4,8,8,16]
    elif ch==':': c[2]=4;c[4]=4
    elif ch=='.': c[6]=4
    elif ch=='!': c=[4,4,4,4,0,4,0]
    elif ch=='?': c=[14,17,1,2,4,0,4]
    elif ch=='+': c=[0,4,4,31,4,4,0]
    return c

def _rows_tile(rows:list[int],color:int=15)->bytes:
    pix=[[0]*8 for _ in range(8)]
    for y,row in enumerate(rows[:7]):
        for x in range(5):
            if row&(1<<(4-x)): pix[y][x+1]=color
    out=bytearray()
    for y in range(8):
        for x in range(0,8,2): out.append((pix[y][x]<<4)|pix[y][x+1])
    return bytes(out)

def builtin_font()->dict[str,Any]:
    tiles=bytearray(96*32)
    for code in range(32,128): tiles[(code-32)*32:(code-31)*32]=_rows_tile(_glyph_rows(chr(code).upper()))
    p0=[0x000,0x001,0x003,0x007,0x047,0x087,0x0C7,0x107,0x147,0x187,0x1C7,0x1CF,0x1D7,0x1DF,0x1EF,0x1FF]
    p1=[0x000,0x040,0x080,0x0C0,0x100,0x140,0x180,0x1C0,0x1C8,0x1D0,0x1D8,0x1E0,0x1E8,0x1F0,0x1F8,0x1FF]
    return {"tiles":bytes(tiles),"palettes":p0+p1,"palette_count":2}

def load_dres_font(path:Path,glyph_order:str)->dict[str,Any]:
    if not path.is_file() or not zipfile.is_zipfile(path): raise SceneError(f"DRES font absent/invalide: {path}")
    with zipfile.ZipFile(path) as z:
        m=json.loads(z.read("manifest.json")); tiles=z.read("tiles.bin"); pals=_unpack_words(z.read("palettes.bin"))
    if m.get("format")!="DRES" or int(m.get("format_version",0))!=3: raise SceneError(f"{path.name}: DRES v3 requis")
    frames=m.get("frames") or []; cells=m.get("cells") or []
    if len(glyph_order)>len(frames): raise SceneError(f"font DRES: {len(glyph_order)} glyphes demandés, {len(frames)} frames")
    ascii_tiles=bytearray(96*32)
    for fi,ch in enumerate(glyph_order):
        fr=frames[fi]
        if int(fr.get("width",0))!=8 or int(fr.get("height",0))!=8: raise SceneError(f"font DRES frame {fi}: 8x8 requis")
        cc=[c for c in cells if int(c.get("frame",-1))==fi and not c.get("empty")]
        if len(cc)!=1: raise SceneError(f"font DRES frame {fi}: exactement 1 cellule non vide requise")
        tid=int(cc[0].get("tile",-1)); raw=tiles[tid*32:(tid+1)*32]
        if len(raw)!=32: raise SceneError(f"font DRES frame {fi}: tile invalide")
        raw=_flip_tile(raw,bool(cc[0].get("flip_x")),bool(cc[0].get("flip_y")))
        code=ord(ch)
        if 32<=code<128: ascii_tiles[(code-32)*32:(code-31)*32]=raw
    pc=max(1,len(pals)//16)
    if pc==1: pals=pals+pals; pc=2
    return {"tiles":bytes(ascii_tiles),"palettes":pals[:32],"palette_count":min(pc,2),"manifest":m}

def _actor_dres(scene:dict[str,Any],actor_path:Path)->Path|None:
    try:
        with zipfile.ZipFile(actor_path) as z:a=json.loads(z.read("actor.json"))
        ref=str((a.get("actor") or {}).get("ressource_dres","")).strip()
        if ref.lower().endswith(".dres"):
            p=(actor_path.parent/ref).resolve()
            return p if p.is_file() else None
    except Exception:pass
    return None

def load_object_frame(scene:dict[str,Any],obj:dict[str,Any],frame_clock:int=0)->dict[str,Any]|None:
    """Decode one real DRES frame for the editor preview (index 0 transparent)."""
    ref=str(obj.get("resource","")).strip()
    if not ref:return None
    path=resolve(scene,ref)
    if str(obj.get("kind","")).upper() in ("ACTOR","BOSS"):
        path=_actor_dres(scene,path) or path
    if not path.is_file() or not zipfile.is_zipfile(path):return None
    with zipfile.ZipFile(path) as z:
        m=json.loads(z.read("manifest.json"))
        if m.get("format")!="DRES" or int(m.get("format_version",0))!=3:return None
        tiles=z.read("tiles.bin");pals=_unpack_words(z.read("palettes.bin"))
    frames=m.get("frames") or [];cells=m.get("cells") or []
    if not frames:return None
    desc=m.get("animation_descriptors") or {};names=list(desc)
    anim=obj.get("animation",0)
    if isinstance(anim,str) and not anim.isdigit():ad=desc.get(anim) or desc.get(anim.upper())
    else:
        ai=int(anim or 0);ad=desc.get(names[ai]) if 0<=ai<len(names) else None
    ids=[int(x) for x in (ad or {}).get("frames",[]) if 0<=int(x)<len(frames)] or [0]
    cadence=max(1,int(obj.get("cadence",0) or 0))
    if int(obj.get("cadence",0) or 0)<=0:
        cadence=max(1,int(round(int(frames[ids[0]].get("duration_ms",100) or 100)*60/1000)))
    fi=ids[(frame_clock//cadence)%len(ids)] if obj.get("loop",True) else ids[min(len(ids)-1,frame_clock//cadence)]
    fr=frames[fi];w=max(1,int(fr.get("width",8)));h=max(1,int(fr.get("height",8)));pixels=[None]*(w*h)
    pc=max(1,len(pals)//16)
    palette_pos=0
    if str(obj.get("palette_animation","NONE")).upper() in ("CYCLE","CYCLE_PALETTES") and int(obj.get("palette_span",1) or 1)>1:
        palette_pos=(frame_clock//max(1,int(obj.get("palette_cadence",8) or 8)))%int(obj.get("palette_span",1))
    palette_override=(int(obj.get("palette",0) or 0)+palette_pos)%pc
    for c in cells:
        if int(c.get("frame",-1))!=fi or c.get("empty") or c.get("tile") is None:continue
        tid=int(c.get("tile"));raw=tiles[tid*32:(tid+1)*32]
        if len(raw)!=32:continue
        raw=_flip_tile(raw,bool(c.get("flip_x")),bool(c.get("flip_y")));bank=pals[palette_override*16:(palette_override+1)*16]
        ox=int(c.get("x",0));oy=int(c.get("y",0))
        for yy in range(8):
            for xx in range(8):
                b=raw[yy*4+(xx>>1)];ci=(b&15) if xx&1 else (b>>4);px=ox+xx;py=oy+yy
                if ci and 0<=px<w and 0<=py<h:pixels[py*w+px]=(bank+[0]*16)[ci]
    return {"width":w,"height":h,"pixels":pixels,"frame":fi,"pivot":fr.get("pivot",[0,0]),"palette":palette_override}

def load_font(scene:dict[str,Any])->dict[str,Any]:
    f=scene.get("font") or {}; src=f.get("source","BUILTIN_5X7")
    if src=="BUILTIN_5X7": return builtin_font()
    return load_dres_font(resolve(scene,src),str(f.get("glyph_order","")))

def load_audio(scene:dict[str,Any])->list[dict[str,Any]]:
    val=str(scene.get("audio_dir") or "").strip()
    if not val: return []
    p=resolve(scene,val); mf=p/"audio_manifest.json"
    if not mf.is_file(): raise SceneError(f"audio_manifest.json absent: {p}")
    m=json.loads(mf.read_text(encoding="utf-8")); out=[]
    for s in m.get("samples",[]):
        out.append({"id":int(s.get("id",len(out))),"codec":1 if str(s.get("codec"))=="A" else 2,"start_page":int(s.get("start_page",0)),"end_page":int(s.get("end_page",0)),"rate_hz":int(s.get("rate_hz",26000)),"level":int(s.get("level",0 if str(s.get("codec"))=="A" else 224)),"pan":int(s.get("pan",192)),"flags":int(s.get("flags",0)),"name":s.get("symbol") or s.get("name") or "SFX"})
    return sorted(out,key=lambda x:x["id"])

def validate(scene:dict[str,Any],gdk_root:Path|None=None)->list[tuple[str,str]]:
    clean={k:v for k,v in scene.items() if not k.startswith("_")};clean,_=dmsscenec.normalize_scene(clean)
    source=Path(scene["_path"]) if scene.get("_path") else None
    out=dmsscenec.validate_scene(clean,source);mode=int(clean.get("video_mode",0));info=MODE_INFO.get(mode,MODE_INFO[0]);objs=clean.get("objects") or [];ids={str(o.get("id","")) for o in objs}
    for o in objs:
        oid=str(o.get("id",""))
        if o.get("kind")=="BACKGROUND":
            if str(o.get("plane","B")).upper()=="B" and not info["bg_b"]:out.append(("ERROR",f"{oid}: BG B interdit en mode {mode}"))
            try:
                bg=load_dimg(resolve(scene,str(o.get("resource",""))))
                if bg["tile_count"]>768:out.append(("ERROR",f"{oid}: {bg['tile_count']} tiles, réserve font V1 à 768 dépassée"))
            except Exception as e:out.append(("ERROR",str(e)))
    if any(str(o.get("text","")) for o in objs):
        try:
            font=load_font(scene);fpids=(scene.get("font") or {}).get("palette_ids",[3,2])
            if len(fpids)<2:out.append(("ERROR","font.palette_ids doit fournir normal + selected"))
            for pid in fpids[:2]:
                if int(pid)>=info["palettes"]:out.append(("ERROR",f"font: P{pid} interdite en mode {mode}"))
            if len(font["tiles"])!=96*32:out.append(("ERROR","font ASCII compilée invalide"))
        except Exception as e:out.append(("ERROR",str(e)))
    if gdk_root:
        hdr=gdk_root/"GDK/include/dms_fx.h"
        if not hdr.is_file(): out.append(("ERROR","dms_fx.h absent"))
    event_mode=mode
    for e in sorted(scene.get("events") or [],key=lambda q:int(q.get("frame",0))):
        op=str(e.get("op",""))
        if op not in OPS: out.append(("ERROR",f"Event op inconnu: {op}")); continue
        if op in ("SHOW","HIDE","TYPEWRITER","SLIDE_IN","SPAWN_FORMATION") and str(e.get("target","")) not in ids: out.append(("ERROR",f"Event {op}: target absent {e.get('target')}"))
        if op=="VIDEO_MODE":
            new_mode=int(e.get("mode",e.get("ref",-1)))
            if new_mode not in MODE_INFO: out.append(("ERROR","VIDEO_MODE invalide"))
            else:event_mode=new_mode
        if op=="FX_START":
            fx=str(e.get("fx","")); einfo=MODE_INFO.get(event_mode,info)
            if fx not in FX_ID: out.append(("ERROR",f"FX absent de la bibliothèque: {fx}"))
            elif fx in MODE2_FX and event_mode!=2: out.append(("ERROR",f"FX {fx} requiert MODE 2 à cette frame"))
            elif fx in BG2_FX and not einfo["bg_b"]: out.append(("ERROR",f"FX {fx} requiert BG A+B"))
    try:
        samples=load_audio(scene); maxid=max([s["id"] for s in samples],default=-1)
        for key in ("menu_move_sfx","menu_validate_sfx"):
            sid=int(scene.get(key,0))
            if samples and sid>maxid: out.append(("ERROR",f"{key}={sid}: SFX absent"))
    except Exception as e: out.append(("ERROR",str(e)))
    if not [x for x in out if x[0]=="ERROR"]:out.insert(0,("PASS","Scene valide pour le profil hardware sélectionné"))
    return out

def _c_u8(name:str,data:bytes)->str:
    vals=[f"0x{b:02X}" for b in data]; lines=[", ".join(vals[i:i+16]) for i in range(0,len(vals),16)]
    return f"static const uint8_t {name}[{len(data)}] = {{\n    "+",\n    ".join(lines)+"\n};\n"
def _c_u16(name:str,data:list[int])->str:
    vals=[f"0x{v&0xFFFF:04X}" for v in data]; lines=[", ".join(vals[i:i+12]) for i in range(0,len(vals),12)]
    return f"static const uint16_t {name}[{len(data)}] = {{\n    "+",\n    ".join(lines)+"\n};\n"
def _c_str(s:str)->str: return json.dumps(s,ensure_ascii=True)

def export_scene(scene:dict[str,Any],out_dir:Path,gdk_root:Path|None=None)->dict[str,Path]:
    diags=validate(scene,gdk_root)
    errs=[m for sev,m in diags if sev=="ERROR"]
    if errs: raise SceneError("; ".join(errs))
    unsupported=[o.get("id") for o in scene.get("objects",[]) if o.get("kind") not in ("TEXT","MENU_ITEM","UI","BACKGROUND")]
    if unsupported:raise SceneError("L’export C autonome V1 est réservé aux écrans texte/DIMG. Pour les objets runtime V2 (%s), ajouter SCENE dans resources.dmsres; BUILD + RUN compile automatiquement."%", ".join(map(str,unsupported)))
    out_dir.mkdir(parents=True,exist_ok=True); name=symbol(str(scene.get("name","SCENE"))); base=name.lower()
    objects=scene.get("objects") or []; render_objs=[o for o in objects if o.get("kind")!="BACKGROUND"]
    index={str(o.get("id")):i for i,o in enumerate(render_objs)}
    bgobj=next((o for o in objects if o.get("kind")=="BACKGROUND"),None); bg=None
    if bgobj: bg=load_dimg(resolve(scene,str(bgobj.get("resource"))))
    font=load_font(scene); fpids=[int(x) for x in (scene.get("font") or {}).get("palette_ids",[3,2])[:2]]
    while len(fpids)<2: fpids.append(fpids[-1] if fpids else 0)
    fpals=font["palettes"][:32]
    if len(fpals)<32: fpals=fpals+[0]*(32-len(fpals))
    events=sorted(scene.get("events") or [],key=lambda e:int(e.get("frame",0)))
    evrows=[]
    for e in events:
        op=OPS[e["op"]]; target=index.get(str(e.get("target","")),255); a=b=c=d=0; ref=int(e.get("ref",0))
        if e["op"]=="TYPEWRITER": a=int(e.get("speed",2))
        elif e["op"]=="SLIDE_IN": a=int(e.get("offset",20)); b=int(e.get("duration",24))
        elif e["op"]=="FX_START": ref=FX_ID[str(e.get("fx"))]; a=int(e.get("intensity",15)); b=int(e.get("duration",0)); c=int(e.get("secondary",0)); d=int(e.get("palette_mask",15))
        elif e["op"]=="WAIT_INPUT":
            wait=str(e.get("wait","ANY")).upper(); a=WAIT_BITS.get(wait,15)
        evrows.append((int(e.get("frame",0)),op,target,a,b,c,d,ref))
    # Background uses the requested plane. Text always lives on the UI plane A.
    map_a=None; map_b=None
    if bg:
        if str(bgobj.get("plane","B")).upper()=="A": map_a=bg["map"]
        else: map_b=bg["map"]
    prefix=base
    c=[]; c.append('#include <stdint.h>\n#include "dms1.h"\n#include "dms_resource_runtime.h"\n#include "'+base+'_scene.h"\n')
    if bg:
        c.append(_c_u8(prefix+'_bg_tiles',bg["tiles"])); c.append(_c_u16(prefix+'_bg_palettes',bg["palettes"])); c.append(_c_u8(prefix+'_bg_palette_ids',bytes(bg["palette_ids"])))
        if map_a is not None: c.append(_c_u16(prefix+'_map_a',map_a))
        if map_b is not None: c.append(_c_u16(prefix+'_map_b',map_b))
        c.append(f"static const DmsSceneVisual {prefix}_visual = {{{prefix}_bg_tiles,{bg['tile_count']}u,{prefix}_bg_palettes,{prefix}_bg_palette_ids,{len(bg['palette_ids'])}u,{prefix+'_map_a' if map_a is not None else '0'},{prefix+'_map_b' if map_b is not None else '0'},{bg['map_w']}u,{bg['map_h']}u}};\n")
    c.append(_c_u8(prefix+'_font_tiles',font["tiles"])); c.append(_c_u16(prefix+'_font_palettes',fpals)); c.append(_c_u8(prefix+'_font_palette_ids',bytes(fpids)))
    c.append(f"static const DmsSceneObjectDesc {prefix}_objects[{max(1,len(render_objs))}] = {{\n")
    if not render_objs: c.append("    {0},\n")
    for o in render_objs:
        kind=KINDS[str(o.get("kind","TEXT"))]; c.append("    {%s,%d,%d,%du,%du,%du,%du,%du},\n"%(_c_str(str(o.get("text",""))),int(o.get("x",0))//8,int(o.get("y",0))//8,int(o.get("action",0)),kind,int(o.get("palette",fpids[0])),int(o.get("selected_palette",fpids[1])),1 if o.get("visible",False) else 0))
    c.append("};\n")
    c.append(f"static const DmsSceneEvent {prefix}_events[{max(1,len(evrows))}] = {{\n")
    if not evrows: c.append("    {0},\n")
    for r in evrows: c.append("    {%du,%du,%du,%d,%d,%d,%d,%du},\n"%r)
    c.append("};\n")
    samples=load_audio(scene)
    if samples:
        maxid=max(s["id"] for s in samples); by={s["id"]:s for s in samples}
        c.append(f"const DmsAudioSampleResourceDesc dms_audio_sample_resources[{maxid+1}] = {{\n")
        for i in range(maxid+1):
            s=by.get(i,{"codec":0,"start_page":0,"end_page":0,"rate_hz":0,"level":0,"pan":0,"flags":0})
            c.append("    {%du,%du,%du,%du,%du,%du,%du},\n"%(s["codec"],s["start_page"],s["end_page"],s["rate_hz"],s["level"],s["pan"],s["flags"]))
        c.append("};\nconst uint16_t dms_audio_sample_resource_count = %du;\n"%(maxid+1))
    scroll=scene.get("scroll") or {}
    c.append(f"const DmsSceneDef {name}_SCENE = {{{int(scene.get('video_mode',0))}u,{int(scroll.get('a_x',0))},{int(scroll.get('a_y',0))},{int(scroll.get('b_x',0))},{int(scroll.get('b_y',0))},{'&'+prefix+'_visual' if bg else '0'},{prefix}_font_tiles,{prefix}_font_palettes,{prefix}_font_palette_ids,2u,{prefix}_objects,{len(render_objs)}u,{prefix}_events,{len(evrows)}u,{int(scene.get('menu_move_sfx',0))}u,{int(scene.get('menu_validate_sfx',0))}u}};\n")
    cpath=out_dir/(base+'_scene.c'); cpath.write_text(''.join(c),encoding='utf-8')
    actions=[]
    for o in render_objs:
        if int(o.get("action",0)):
            actions.append((symbol(str(o.get("destination") or o.get("id"))),int(o.get("action"))))
    guard=symbol(base+'_scene_h')
    h=[f"#ifndef {guard}\n#define {guard}\n#include \"dms_scene.h\"\nextern const DmsSceneDef {name}_SCENE;\n"]
    for a,v in actions: h.append(f"#define SCENE_ACTION_{a} {v}u\n")
    h.append("#endif\n"); hpath=out_dir/(base+'_scene.h'); hpath.write_text(''.join(h),encoding='utf-8')
    # Portable packed companion: header + object records + event records + strings + raw hardware data.
    strings=bytearray(); objbin=bytearray()
    for o in render_objs:
        off=len(strings); strings+=str(o.get("text","")).encode("ascii","replace")+b"\0"
        objbin+=struct.pack(">hhHBBBBH",int(o.get("x",0))//8,int(o.get("y",0))//8,int(o.get("action",0)),KINDS[str(o.get("kind","TEXT"))],int(o.get("palette",0)),int(o.get("selected_palette",0)),1 if o.get("visible",False) else 0,off)
    evbin=bytearray()
    for r in evrows: evbin+=struct.pack(">HBBhhhhH",*r)
    bgtiles=bg["tiles"] if bg else b""; bgmap=b"".join(struct.pack(">H",x) for x in ((map_a or map_b) if bg else [])); fonttiles=font["tiles"]
    palblob=b"".join(struct.pack(">H",x) for x in ((bg["palettes"] if bg else [])+fpals))
    header=struct.pack(">4sHBBHIIIIIII",b"DSC1",1,int(scene.get("video_mode",0)),len(render_objs),len(evrows),len(objbin),len(evbin),len(strings),len(bgtiles),len(bgmap),len(fonttiles),len(palblob))
    bpath=out_dir/(base+'_scene_data.bin'); bpath.write_bytes(header+objbin+evbin+strings+bgtiles+bgmap+fonttiles+palblob)
    manifest={"format":"DMS-SCENE-EXPORT","version":1,"source":Path(scene.get("_path","<memory>")).name,"scene":scene.get("name"),"video_mode":int(scene.get("video_mode",0)),"objects":len(render_objs),"events":len(evrows),"background_tiles":bg["tile_count"] if bg else 0,"font_tiles":96,"audio_samples":len(samples),"fx":[e.get("fx") for e in events if e.get("op")=="FX_START"],"outputs":[cpath.name,hpath.name,bpath.name]}
    mpath=out_dir/(base+'_scene_manifest.json'); mpath.write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
    return {"c":cpath,"h":hpath,"bin":bpath,"manifest":mpath}
