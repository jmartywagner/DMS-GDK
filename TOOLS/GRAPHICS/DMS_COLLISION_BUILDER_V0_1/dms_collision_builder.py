from __future__ import annotations

import json
import math
import os
import struct
import zipfile
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


APP_NAME = "DMS Collision Builder"
APP_VERSION = "0.4.0 MAP FIRST"

PROFILS = ["Plateforme", "Shoot'em up", "Vue du dessus", "Action / aventure", "Personnalisé"]

TYPES = [
    "SOLIDE",
    "PLATEFORME_1_SENS",
    "DANGER",
    "ECHELLE",
    "EAU",
    "RALENTISSEMENT",
    "DECLENCHEUR",
    "SORTIE",
    "CHECKPOINT",
    "PERSONNALISE",
]

FORMES = ["RECTANGLE", "SEGMENT", "PENTE", "POLYGONE", "POINT"]

COULEURS = {
    "SOLIDE": "#ff5c5c",
    "PLATEFORME_1_SENS": "#ffd166",
    "DANGER": "#ff2b78",
    "ECHELLE": "#7bd66f",
    "EAU": "#4ba8ff",
    "RALENTISSEMENT": "#a77add",
    "DECLENCHEUR": "#39d5e8",
    "SORTIE": "#f19b45",
    "CHECKPOINT": "#5de6a9",
    "PERSONNALISE": "#eeeeee",
}

TYPE_IDS = {n:i for i,n in enumerate(TYPES)}
FORME_IDS = {n:i for i,n in enumerate(FORMES)}
CIBLE_BITS = {
    "joueur": 1,
    "ennemis": 2,
    "projectiles_joueur": 4,
    "projectiles_ennemis": 8,
    "objets": 16,
}


@dataclass
class ActionZone:
    active: bool = False
    action: str = "AUCUNE"
    parametre_a: str = ""
    parametre_b: str = ""
    une_fois: bool = False


@dataclass
class Zone:
    id: int
    nom: str
    type_zone: str
    forme: str
    points: list
    joueur: bool = True
    ennemis: bool = True
    projectiles_joueur: bool = False
    projectiles_ennemis: bool = False
    objets: bool = False
    groupe: str = "MONDE"
    active: bool = True
    note: str = ""
    action: ActionZone = field(default_factory=ActionZone)

    def masque_cibles(self):
        m = 0
        if self.joueur: m |= CIBLE_BITS["joueur"]
        if self.ennemis: m |= CIBLE_BITS["ennemis"]
        if self.projectiles_joueur: m |= CIBLE_BITS["projectiles_joueur"]
        if self.projectiles_ennemis: m |= CIBLE_BITS["projectiles_ennemis"]
        if self.objets: m |= CIBLE_BITS["objets"]
        return m

    def bounds(self):
        if not self.points:
            return (0,0,0,0)
        xs=[p[0] for p in self.points]
        ys=[p[1] for p in self.points]
        return (min(xs),min(ys),max(xs),max(ys))


@dataclass
class Scene:
    nom: str = "DMS_SCENE"
    largeur_px: int = 320
    hauteur_px: int = 224
    tile_size: int = 8
    source_map: str = ""
    source_tileset: str = ""
    profil: str = "Plateforme"


class CollisionTileset:
    """Tileset d'affichage du Collision Builder.

    Lit les PNG/GIF historiques et les DIMG V2 du Map Builder. Les variantes
    flip X/Y sont mises en cache afin que la map reste fluide, même très zoomée.
    """
    def __init__(self, master):
        self.master=master
        self.clear()

    def clear(self):
        self.path=""
        self.source_kind="NONE"
        self.image=None
        self.tile_size=8
        self.margin=0
        self.spacing=0
        self.columns=0
        self.rows=0
        self.tiles_base=[]
        self.tile_patterns=[]
        self.palette_banks={}
        self.palette_ids=[]
        self._variant_cache={}
        self._display_cache={}
        self._pixel_cache={}

    @property
    def count(self):
        return len(self.tile_patterns) if self.source_kind=="DIMG" else len(self.tiles_base)

    def load(self,path,tile_size=8,margin=0,spacing=0):
        path=Path(path)
        if path.suffix.lower()==".dimg":
            return self.load_dimg(path)
        self.clear()
        self.path=str(path)
        self.source_kind="PNG"
        self.tile_size=max(1,int(tile_size))
        self.margin=max(0,int(margin))
        self.spacing=max(0,int(spacing))
        self.image=tk.PhotoImage(master=self.master,file=str(path))
        step=self.tile_size+self.spacing
        usable_w=max(0,self.image.width()-self.margin*2)
        usable_h=max(0,self.image.height()-self.margin*2)
        self.columns=max(0,(usable_w+self.spacing)//step)
        self.rows=max(0,(usable_h+self.spacing)//step)
        for row in range(self.rows):
            for col in range(self.columns):
                x0=self.margin+col*step; y0=self.margin+row*step
                if x0+self.tile_size>self.image.width() or y0+self.tile_size>self.image.height():
                    continue
                out=tk.PhotoImage(master=self.master,width=self.tile_size,height=self.tile_size)
                out.tk.call(out,"copy",self.image,"-from",x0,y0,x0+self.tile_size,y0+self.tile_size,"-to",0,0)
                self.tiles_base.append(out)
        return self.count

    def load_dimg(self,path):
        self.clear()
        self.path=str(path)
        self.source_kind="DIMG"
        with zipfile.ZipFile(path,"r") as z:
            manifest=json.loads(z.read("manifest.json").decode("utf-8"))
            if manifest.get("format")!="DIMG":
                raise ValueError("Le fichier tileset n'est pas un DIMG valide.")
            self.tile_size=max(1,int(manifest.get("tiles",{}).get("tile_size",8)))
            self.palette_ids=[int(x) for x in manifest.get("selected_palette_ids",[])]
            for entry in manifest.get("palettes",[]):
                pid=int(entry.get("physical_id",0))
                cols=[]
                rgb333=entry.get("colors_rgb333",[])
                if rgb333:
                    for c in rgb333[:16]:
                        if isinstance(c,(list,tuple)) and len(c)>=3:
                            cols.append(tuple(max(0,min(7,int(v))) for v in c[:3]))
                elif entry.get("words_hex"):
                    for word in entry.get("words_hex",[])[:16]:
                        try:
                            n=int(str(word),16); cols.append(((n>>6)&7,(n>>3)&7,n&7))
                        except Exception:
                            pass
                if cols:
                    cols.extend([(0,0,0)]*(16-len(cols)))
                    self.palette_banks[pid]=cols[:16]
            if not self.palette_ids and "palette_ids.bin" in z.namelist():
                self.palette_ids=[int(v) for v in z.read("palette_ids.bin")]
            if not self.palette_ids:
                self.palette_ids=sorted(self.palette_banks)
            if "palettes.bin" in z.namelist() and self.palette_ids:
                rawpal=z.read("palettes.bin")
                for bi,pid in enumerate(self.palette_ids):
                    if pid in self.palette_banks and any(self.palette_banks[pid]):
                        continue
                    start=bi*32
                    if start+32>len(rawpal):
                        continue
                    cols=[]
                    for i in range(16):
                        w=struct.unpack(">H",rawpal[start+i*2:start+i*2+2])[0]
                        cols.append(((w>>6)&7,(w>>3)&7,w&7))
                    self.palette_banks[pid]=cols
            raw=z.read("tiles.bin")
            unique=int(manifest.get("tiles",{}).get("unique",0))
            bpt=self.tile_size*self.tile_size//2
            if bpt<=0:
                raise ValueError("Taille de tile DIMG invalide.")
            if unique<=0:
                unique=len(raw)//bpt
            if len(raw)<unique*bpt:
                raise ValueError("DIMG tronqué : tiles.bin incomplet.")
            for tid in range(unique):
                chunk=raw[tid*bpt:(tid+1)*bpt]
                vals=[]
                for b in chunk:
                    vals.extend(((b>>4)&15,b&15))
                vals=vals[:self.tile_size*self.tile_size]
                self.tile_patterns.append([vals[y*self.tile_size:(y+1)*self.tile_size] for y in range(self.tile_size)])
        self.columns=max(1,int(math.ceil(math.sqrt(self.count)))) if self.count else 0
        self.rows=(self.count+self.columns-1)//self.columns if self.columns else 0
        self.tiles_base=[None]*self.count
        return self.count

    @staticmethod
    def _rgb333_hex(c):
        r,g,b=(max(0,min(7,int(v))) for v in c[:3])
        return f"#{round(r*255/7):02x}{round(g*255/7):02x}{round(b*255/7):02x}"

    def _dimg_base(self,tile_id,palette_id):
        if not (0<=tile_id<len(self.tile_patterns)):
            return None
        if palette_id not in self.palette_banks:
            palette_id=self.palette_ids[0] if self.palette_ids else 0
        key=("base",tile_id,palette_id)
        if key in self._variant_cache:
            return self._variant_cache[key]
        pal=self.palette_banks.get(palette_id,[(0,0,0)]*16)
        colors=[self._rgb333_hex(c) for c in pal[:16]]
        colors.extend(["#000000"]*(16-len(colors)))
        out=tk.PhotoImage(master=self.master,width=self.tile_size,height=self.tile_size)
        for y,row in enumerate(self.tile_patterns[tile_id]):
            out.put("{"+" ".join(colors[v&15] for v in row)+"}",to=(0,y))
        self._variant_cache[key]=out
        return out

    def tile(self,tile_id,flip_x=False,flip_y=False,palette_id=None):
        tile_id=int(tile_id); fx=bool(flip_x); fy=bool(flip_y)
        pal=int(palette_id or 0) if self.source_kind=="DIMG" else None
        key=("tile",tile_id,fx,fy,pal)
        if key in self._variant_cache:
            return self._variant_cache[key]
        if self.source_kind=="DIMG":
            src=self._dimg_base(tile_id,pal)
        else:
            src=self.tiles_base[tile_id] if 0<=tile_id<len(self.tiles_base) else None
        if src is None:
            return None
        if not fx and not fy:
            self._variant_cache[key]=src
            return src
        ts=self.tile_size
        out=tk.PhotoImage(master=self.master,width=ts,height=ts)
        for y in range(ts):
            sy=ts-1-y if fy else y
            for x in range(ts):
                sx=ts-1-x if fx else x
                try:
                    if hasattr(src,"transparency_get") and src.transparency_get(sx,sy):
                        out.transparency_set(x,y,True)
                        continue
                except Exception:
                    pass
                c=src.get(sx,sy)
                if isinstance(c,str):
                    color=c
                else:
                    vals=tuple(int(v) for v in c[:3]); color="#%02x%02x%02x"%vals
                out.put(color,(x,y))
        self._variant_cache[key]=out
        return out

    def pixels(self,tile_id,flip_x=False,flip_y=False,palette_id=None):
        """Retourne la tuile en pixels RGB/None (None = transparent), mise en cache.

        Cette représentation permet de composer une grande map en mémoire Python
        puis de l'envoyer à Tk en UNE opération, au lieu de dizaines de milliers
        de copies Tcl qui donnaient l'impression que l'application gelait.
        """
        tid=int(tile_id); fx=bool(flip_x); fy=bool(flip_y)
        pal=int(palette_id or 0) if self.source_kind=="DIMG" else None
        key=(tid,fx,fy,pal)
        if key in self._pixel_cache:return self._pixel_cache[key]
        ts=self.tile_size
        rows=[]
        if self.source_kind=="DIMG":
            if not (0<=tid<len(self.tile_patterns)):
                return None
            if pal not in self.palette_banks:
                pal=self.palette_ids[0] if self.palette_ids else 0
            bank=self.palette_banks.get(pal,[(0,0,0)]*16)
            pattern=self.tile_patterns[tid]
            for y in range(ts):
                sy=ts-1-y if fy else y; row=[]
                for x in range(ts):
                    sx=ts-1-x if fx else x; ci=int(pattern[sy][sx])&15
                    if ci==0:
                        row.append(None); continue
                    c=bank[ci] if ci<len(bank) else (0,0,0)
                    row.append(tuple(round(max(0,min(7,int(v)))*255/7) for v in c[:3]))
                rows.append(row)
        else:
            if not (0<=tid<len(self.tiles_base)):
                return None
            src=self.tiles_base[tid]
            for y in range(ts):
                sy=ts-1-y if fy else y; row=[]
                for x in range(ts):
                    sx=ts-1-x if fx else x
                    try:
                        if hasattr(src,"transparency_get") and src.transparency_get(sx,sy):
                            row.append(None); continue
                    except Exception:pass
                    c=src.get(sx,sy)
                    if isinstance(c,str):
                        h=c.lstrip("#")
                        if len(h)>=6:rgb=(int(h[0:2],16),int(h[2:4],16),int(h[4:6],16))
                        else:rgb=(0,0,0)
                    else:
                        vals=tuple(int(v) for v in c[:3]); rgb=(vals+(0,0,0))[:3]
                    row.append(rgb)
                rows.append(row)
        self._pixel_cache[key]=rows
        return rows

    def display_tile(self,tile_id,flip_x=False,flip_y=False,zoom=1,palette_id=None):
        z=max(1,int(zoom))
        pal=int(palette_id or 0) if self.source_kind=="DIMG" else None
        key=("display",int(tile_id),bool(flip_x),bool(flip_y),z,pal)
        if key in self._display_cache:
            return self._display_cache[key]
        base=self.tile(tile_id,flip_x,flip_y,palette_id)
        if base is None:
            return None
        out=base.zoom(z,z) if z>1 else base
        self._display_cache[key]=out
        return out


# ---------------------------------------------------------------------------
# GEOMETRIE
# ---------------------------------------------------------------------------

def point_dans_rect(x,y,r):
    x0,y0,x1,y1=r
    return x0 <= x <= x1 and y0 <= y <= y1

def rectangle_non_nul(pts):
    return bool(len(pts)>=2 and int(pts[0][0])!=int(pts[1][0]) and int(pts[0][1])!=int(pts[1][1]))

def rects_se_touchent(a,b):
    ax0,ay0,ax1,ay1=a
    bx0,by0,bx1,by1=b
    return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)

def distance_point_segment(px,py,a,b):
    ax,ay=a; bx,by=b
    vx,vy=bx-ax,by-ay
    wx,wy=px-ax,py-ay
    vv=vx*vx+vy*vy
    if vv <= 1e-9:
        return math.hypot(px-ax,py-ay)
    t=max(0.0,min(1.0,(wx*vx+wy*vy)/vv))
    qx=ax+t*vx; qy=ay+t*vy
    return math.hypot(px-qx,py-qy)

def point_dans_polygone(x,y,pts):
    if len(pts) < 3:
        return False
    inside=False
    j=len(pts)-1
    for i in range(len(pts)):
        xi,yi=pts[i]; xj,yj=pts[j]
        if ((yi>y)!=(yj>y)):
            denom=(yj-yi) if yj!=yi else 1e-9
            cross=(xj-xi)*(y-yi)/denom+xi
            if x < cross:
                inside=not inside
        j=i
    return inside

def zone_touche_rect(zone, rect):
    if not zone.active:
        return False
    if zone.forme=="RECTANGLE":
        return rects_se_touchent(zone.bounds(),rect)
    cx=(rect[0]+rect[2])/2
    cy=(rect[1]+rect[3])/2
    if zone.forme in ("SEGMENT","PENTE") and len(zone.points)>=2:
        rayon=max(2,math.hypot(rect[2]-rect[0],rect[3]-rect[1])/2)
        return distance_point_segment(cx,cy,zone.points[0],zone.points[1]) <= rayon
    if zone.forme=="POINT" and zone.points:
        return point_dans_rect(zone.points[0][0],zone.points[0][1],rect)
    if zone.forme=="POLYGONE":
        if not rects_se_touchent(zone.bounds(),rect):
            return False
        tests=[(cx,cy),(rect[0],rect[1]),(rect[2],rect[1]),(rect[0],rect[3]),(rect[2],rect[3])]
        return any(point_dans_polygone(x,y,zone.points) for x,y in tests) or any(
            point_dans_rect(x,y,rect) for x,y in zone.points
        )
    return False


# ---------------------------------------------------------------------------
# IMPORT MAP BUILDER
# ---------------------------------------------------------------------------

def lire_dmapproj(path):
    data=json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format")!="DMS_MAP_PROJECT":
        raise ValueError("Ce fichier n'est pas un projet DMS Map Builder.")
    md=data.get("map",{})
    ts=int(md.get("tile_size",8))
    w=int(md.get("width",40))
    h=int(md.get("height",28))
    tsp=data.get("tileset",{}).get("path","")
    if tsp:
        p=Path(tsp)
        if not p.is_absolute():
            p=Path(path).parent/p
        tsp=str(p)
    scene=Scene(
        nom=md.get("name","DMS_SCENE"),
        largeur_px=w*ts,
        hauteur_px=h*ts,
        tile_size=ts,
        source_map=str(path),
        source_tileset=tsp,
    )
    return scene,data

def lire_dmap(path):
    with zipfile.ZipFile(path,"r") as z:
        data=json.loads(z.read("manifest.json").decode("utf-8"))
    if data.get("format")!="DMAP":
        raise ValueError("Ce fichier n'est pas une map DMS.")
    md=data.get("map",{})
    ti=data.get("tileset",{})
    tsp=ti.get("path") or ti.get("source") or ti.get("file") or ""
    if tsp:
        pp=Path(tsp)
        if not pp.is_absolute():
            pp=Path(path).parent/pp
        tsp=str(pp)
    scene=Scene(
        nom=md.get("name","DMS_SCENE"),
        largeur_px=int(md.get("pixel_width",320)),
        hauteur_px=int(md.get("pixel_height",224)),
        tile_size=int(md.get("tile_size",8)),
        source_map=str(path),
        source_tileset=tsp,
    )
    return scene,data

def fusionner_grille_collisions(grid,tile_size):
    if not grid:
        return []
    h=len(grid); w=len(grid[0]) if h else 0
    vus=[[False]*w for _ in range(h)]
    out=[]
    for y in range(h):
        for x in range(w):
            typ=grid[y][x]
            if vus[y][x] or typ=="NONE":
                continue
            # largeur max
            x1=x
            while x1<w and grid[y][x1]==typ and not vus[y][x1]:
                x1+=1
            # hauteur max avec la même largeur
            y1=y+1
            while y1<h:
                ok=True
                for xx in range(x,x1):
                    if vus[y1][xx] or grid[y1][xx]!=typ:
                        ok=False; break
                if not ok: break
                y1+=1
            for yy in range(y,y1):
                for xx in range(x,x1):
                    vus[yy][xx]=True
            out.append((typ,x*tile_size,y*tile_size,(x1-x)*tile_size,(y1-y)*tile_size))
    return out

def convertir_type_map(typ):
    return {
        "SOLID":"SOLIDE",
        "ONE_WAY":"PLATEFORME_1_SENS",
        "HAZARD":"DANGER",
        "LADDER":"ECHELLE",
        "WATER":"EAU",
        "SLOW":"RALENTISSEMENT",
        "CUSTOM":"PERSONNALISE",
    }.get(typ,"PERSONNALISE")


# ---------------------------------------------------------------------------
# EXPORT DCOLL
# ---------------------------------------------------------------------------

def rapport(scene,zones):
    lines=[
        "DMS COLLISION BUILDER - RAPPORT",
        "================================",
        f"Scene : {scene.nom}",
        f"Taille : {scene.largeur_px}×{scene.hauteur_px}px",
        f"Profil : {scene.profil}",
        f"Zones : {len(zones)}",
        "",
        "PAR TYPE",
        "--------",
    ]
    for typ in TYPES:
        n=sum(1 for z in zones if z.type_zone==typ)
        if n:
            lines.append(f"{typ}: {n}")
    lines+=["","CIBLES","------"]
    for attr,label in [
        ("joueur","Joueur"),("ennemis","Ennemis"),
        ("projectiles_joueur","Projectiles joueur"),
        ("projectiles_ennemis","Projectiles ennemis"),
        ("objets","Objets"),
    ]:
        n=sum(1 for z in zones if getattr(z,attr))
        lines.append(f"{label}: {n}")
    return "\n".join(lines)

def export_dcoll(path,scene,zones):
    manifest={
        "format":"DCOLL",
        "format_version":1,
        "generator":f"{APP_NAME} {APP_VERSION}",
        "scene":asdict(scene),
        "types":TYPE_IDS,
        "formes":FORME_IDS,
        "cible_bits":CIBLE_BITS,
        "zones":[
            {**asdict(z),"cible_mask":z.masque_cibles(),"bounds":list(z.bounds())}
            for z in zones
        ],
        "notes_runtime":{
            "coordonnees":"pixels",
            "collision_monde_separee_des_hitboxes_sprites":True,
            "plateforme_1_sens_demande_direction_mouvement":True,
            "le_gdk_execute_les_actions":True,
        }
    }

    # Zones binaires + vertices séparés.
    recs=bytearray()
    verts=bytearray()
    offset_vertices=0
    actions=[]
    for z in zones:
        pts=[(int(x),int(y)) for x,y in z.points]
        x0,y0,x1,y1=z.bounds()
        flags=(1 if z.active else 0) | ((1 if z.action.active else 0)<<1) | ((1 if z.action.une_fois else 0)<<2)
        recs += struct.pack(
            ">HBBBBHIIiiii",
            int(z.id)&0xFFFF,
            TYPE_IDS.get(z.type_zone,9)&0xFF,
            FORME_IDS.get(z.forme,0)&0xFF,
            z.masque_cibles()&0xFF,
            flags&0xFF,
            len(pts)&0xFFFF,
            offset_vertices,
            0,
            int(x0),int(y0),int(x1),int(y1)
        )
        for x,y in pts:
            verts += struct.pack(">ii",x,y)
        offset_vertices += len(pts)

        if z.action.active:
            actions.append({
                "zone_id":z.id,
                "action":z.action.action,
                "parametre_a":z.action.parametre_a,
                "parametre_b":z.action.parametre_b,
                "une_fois":z.action.une_fois
            })

    with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json",json.dumps(manifest,indent=2,ensure_ascii=False))
        zf.writestr("zones.bin",bytes(recs))
        zf.writestr("vertices.bin",bytes(verts))
        zf.writestr("actions.json",json.dumps(actions,indent=2,ensure_ascii=False))
        zf.writestr("report.txt",rapport(scene,zones))
        zf.writestr(
            "README.txt",
            "DCOLL V1 - collisions du monde DMS-1.\n"
            "Les hitboxes/hurtboxes animées des sprites restent dans Asset Lab.\n"
        )


# ---------------------------------------------------------------------------
# APPLICATION
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} - {APP_VERSION}")
        self.geometry("1600x930")
        self.minsize(1240,760)
        self.configure(bg="#17191d")

        self.scene=Scene()
        self.map_data=None
        self.tilesource=CollisionTileset(self)
        self.tileset=None  # alias historique conservé pour compatibilité interne
        self.tiles=[]
        self.reference=None
        self.reference_path=""
        # P0.1: la tilemap est composée une seule fois puis réutilisée.
        # Les déplacements de souris ne recréent plus des milliers d'items Tk.
        self._map_base_cache=None
        self._map_zoom_cache={}
        self._reference_zoom_cache={}
        # V0.2.1 RECOVERY : le décor reste statique pendant le dessin.
        # Les mouvements souris ne doivent jamais reconstruire la map/grille.
        self._static_dirty=True
        self._overlay_redraw_pending=False

        self.zones=[]
        self.next_id=1
        self.selected_id=None
        self.project_path=None

        self.zoom=3
        self.zoom_var=tk.StringVar(value="3×")
        self.grid_var=tk.IntVar(value=8)
        self.snap_var=tk.BooleanVar(value=True)
        self.show_grid=tk.BooleanVar(value=True)
        self.show_map=tk.BooleanVar(value=True)
        self.show_labels=tk.BooleanVar(value=True)
        self.auto_reload=tk.BooleanVar(value=True)
        self.left_panel_visible=True
        self.right_panel_visible=True
        self.map_source_var=tk.StringVar(value="Aucune map chargée")
        self.tileset_source_var=tk.StringVar(value="Tileset : -")
        self.live_status_var=tk.StringVar(value="LIVE : en attente d’une source")
        self._watch_stamps={}
        self._watch_job=None
        self._closing=False
        self.selected_tile_cell=None
        self.selected_tile_layer="BG A"
        self._tile_preview_images=[]
        self._tile_loupe=None

        self.profile_var=tk.StringVar(value="Plateforme")
        self.type_var=tk.StringVar(value="SOLIDE")
        self.forme_var=tk.StringVar(value="RECTANGLE")
        self.nom_var=tk.StringVar(value="Zone")
        self.groupe_var=tk.StringVar(value="MONDE")

        self.target_vars={
            "joueur":tk.BooleanVar(value=True),
            "ennemis":tk.BooleanVar(value=True),
            "projectiles_joueur":tk.BooleanVar(value=False),
            "projectiles_ennemis":tk.BooleanVar(value=False),
            "objets":tk.BooleanVar(value=False),
        }

        self.test_mode=tk.BooleanVar(value=False)
        self.test_w=tk.IntVar(value=16)
        self.test_h=tk.IntVar(value=24)
        self.test_x=32
        self.test_y=32

        self.drawing=False
        self.start=None
        self.current=None
        self.poly=[]

        self.undo_stack=[]
        self.redo_stack=[]

        self._style()
        self._build()
        self._bindings()
        self.apply_profile()
        self._sync_source_labels()
        self.redraw()
        self.after_idle(self._set_initial_panes)
        self.after(250,self._set_initial_panes)
        self._schedule_file_watch()

    def _style(self):
        s=ttk.Style(self)
        try:s.theme_use("clam")
        except:pass
        s.configure(".",font=("Segoe UI",9))
        s.configure("TFrame",background="#17191d")
        s.configure("TLabelframe",background="#17191d",foreground="#ececec")
        s.configure("TLabelframe.Label",background="#17191d",foreground="#ececec",font=("Segoe UI",9,"bold"))
        s.configure("TLabel",background="#17191d",foreground="#d9d9d9")
        s.configure("Title.TLabel",background="#17191d",foreground="#f5f5f5",font=("Segoe UI",17,"bold"))
        s.configure("Sub.TLabel",background="#17191d",foreground="#9aa3ad")
        s.configure("TCheckbutton",background="#17191d",foreground="#d9d9d9")
        s.configure("Accent.TButton",font=("Segoe UI",9,"bold"))

    def _build(self):
        # ------------------------------------------------------------------
        # Barre principale : le flux est volontairement MAP -> COLLISIONS -> EXPORT.
        # ------------------------------------------------------------------
        top=ttk.Frame(self,padding=(12,8)); top.pack(fill="x")
        ttk.Label(top,text=APP_NAME,style="Title.TLabel").pack(side="left")
        ttk.Label(top,text="V0.4 • MAP FIRST",style="Sub.TLabel").pack(side="left",padx=12)
        ttk.Button(top,text="Exporter .dcoll",command=self.export_action,style="Accent.TButton").pack(side="right",padx=4)
        ttk.Button(top,text="Sauver",command=self.save_project).pack(side="right",padx=4)
        ttk.Button(top,text="Ouvrir projet",command=self.open_project).pack(side="right",padx=4)
        ttk.Button(top,text="CHARGER MAP",command=self.import_map,style="Accent.TButton").pack(side="right",padx=10)

        # Bandeau source toujours visible : impossible de ne plus savoir quelle map est utilisée.
        sourcebar=ttk.Frame(self,padding=(12,0,12,8)); sourcebar.pack(fill="x")
        ttk.Label(sourcebar,text="MAP :",font=("Segoe UI",9,"bold")).pack(side="left")
        ttk.Label(sourcebar,textvariable=self.map_source_var).pack(side="left",padx=(4,12))
        ttk.Label(sourcebar,textvariable=self.tileset_source_var,style="Sub.TLabel").pack(side="left",padx=(0,12))
        ttk.Label(sourcebar,textvariable=self.live_status_var,style="Sub.TLabel").pack(side="left")
        ttk.Button(sourcebar,text="Recharger",command=self.reload_sources_now).pack(side="right",padx=(4,0))
        ttk.Button(sourcebar,text="Choisir tileset…",command=self.choose_tileset).pack(side="right",padx=(4,0))

        self.body=ttk.Panedwindow(self,orient="horizontal"); self.body.pack(fill="both",expand=True,padx=10,pady=(0,8))
        self.left_panel=ttk.Frame(self.body,padding=6)
        self.center_panel=ttk.Frame(self.body,padding=6)
        self.right_panel=ttk.Frame(self.body,padding=6)
        self.body.add(self.left_panel,weight=0)
        self.body.add(self.center_panel,weight=1)
        self.body.add(self.right_panel,weight=0)
        left=self.left_panel; center=self.center_panel; right=self.right_panel

        # ------------------------------------------------------------------
        # GAUCHE - uniquement ce qui sert à CRÉER les collisions.
        # ------------------------------------------------------------------
        src=ttk.LabelFrame(left,text="1. Sources",padding=8); src.pack(fill="x")
        ttk.Button(src,text="Charger / changer la map",command=self.import_map,style="Accent.TButton").pack(fill="x",pady=2)
        ttk.Button(src,text="Image PNG de référence (optionnel)",command=self.import_reference).pack(fill="x",pady=2)
        ttk.Button(src,text="Importer collisions déjà peintes dans la map",command=self.import_map_collisions).pack(fill="x",pady=2)
        self.scene_info=tk.StringVar(value="Aucune map. Charge un .dmap ou .dmapproj.")
        ttk.Label(src,textvariable=self.scene_info,style="Sub.TLabel",wraplength=285,justify="left").pack(anchor="w",pady=(6,0))

        prof=ttk.LabelFrame(left,text="2. Préréglage de la prochaine zone",padding=8); prof.pack(fill="x",pady=(8,0))
        row=ttk.Frame(prof); row.pack(fill="x")
        cb=ttk.Combobox(row,textvariable=self.profile_var,values=PROFILS,state="readonly"); cb.pack(side="left",fill="x",expand=True)
        cb.bind("<<ComboboxSelected>>",lambda e:self.apply_profile())
        ttk.Button(row,text="?",width=3,command=self.show_profile_help).pack(side="left",padx=(5,0))
        self.help_profile=tk.StringVar(value="")
        ttk.Label(prof,textvariable=self.help_profile,style="Sub.TLabel",wraplength=285,justify="left").pack(anchor="w",pady=(6,0))

        draw=ttk.LabelFrame(left,text="3. Dessiner une zone",padding=8); draw.pack(fill="x",pady=(8,0))
        self._combo(draw,"Type",self.type_var,TYPES)
        self._combo(draw,"Forme",self.forme_var,FORMES)
        self._entry(draw,"Nom",self.nom_var)
        self._entry(draw,"Groupe",self.groupe_var)

        targets=ttk.LabelFrame(left,text="Cibles",padding=8); targets.pack(fill="x",pady=(8,0))
        labels={
            "joueur":"Joueur","ennemis":"Ennemis","projectiles_joueur":"Projectiles joueur",
            "projectiles_ennemis":"Projectiles ennemis","objets":"Objets"
        }
        for k,v in self.target_vars.items():
            ttk.Checkbutton(targets,text=labels[k],variable=v).pack(anchor="w")

        grid=ttk.LabelFrame(left,text="Grille / précision",padding=8); grid.pack(fill="x",pady=(8,0))
        ttk.Checkbutton(grid,text="Aimantation",variable=self.snap_var).pack(anchor="w")
        line=ttk.Frame(grid); line.pack(fill="x",pady=(4,0))
        ttk.Label(line,text="Pas").pack(side="left")
        gc=ttk.Combobox(line,textvariable=self.grid_var,values=[1,2,4,8,16,32],state="readonly",width=6); gc.pack(side="left",padx=5)
        gc.bind("<<ComboboxSelected>>",lambda e:self.on_grid_change())
        ttk.Checkbutton(line,text="Afficher grille",variable=self.show_grid,command=self.redraw_full).pack(side="left")
        ttk.Checkbutton(grid,text="Auto-reload map / tileset",variable=self.auto_reload,command=self._on_auto_reload_toggle).pack(anchor="w",pady=(5,0))

        test=ttk.LabelFrame(left,text="Testeur",padding=8); test.pack(fill="x",pady=(8,0))
        ttk.Checkbutton(test,text="Activer boîte test",variable=self.test_mode,command=self.redraw_overlays).pack(anchor="w")
        line=ttk.Frame(test); line.pack(fill="x",pady=(5,0))
        ttk.Label(line,text="Taille").pack(side="left")
        ttk.Entry(line,textvariable=self.test_w,width=5).pack(side="left",padx=3)
        ttk.Label(line,text="×").pack(side="left")
        ttk.Entry(line,textvariable=self.test_h,width=5).pack(side="left",padx=3)

        # ------------------------------------------------------------------
        # CENTRE - LA MAP. C'est volontairement le plus gros élément de l'app.
        # ------------------------------------------------------------------
        tools=ttk.Frame(center); tools.pack(fill="x",pady=(0,6))
        self.left_toggle_btn=ttk.Button(tools,text="◀ Outils",command=self.toggle_left_panel,width=10); self.left_toggle_btn.pack(side="left",padx=(0,8))
        ttk.Label(tools,text="ZOOM MAP",font=("Segoe UI",9,"bold")).pack(side="left")
        ttk.Button(tools,text="−",width=3,command=lambda:self.change_zoom(-1)).pack(side="left",padx=(5,1))
        z=ttk.Combobox(tools,textvariable=self.zoom_var,values=[f"{i}×" for i in range(1,9)],state="readonly",width=5); z.pack(side="left",padx=2)
        z.bind("<<ComboboxSelected>>",lambda e:self.set_zoom())
        ttk.Button(tools,text="+",width=3,command=lambda:self.change_zoom(1)).pack(side="left",padx=(1,5))
        ttk.Label(tools,text="molette = zoom sous le curseur",style="Sub.TLabel").pack(side="left",padx=(3,10))
        ttk.Checkbutton(tools,text="Afficher map",variable=self.show_map,command=self.redraw_full).pack(side="left",padx=4)
        ttk.Checkbutton(tools,text="Noms zones",variable=self.show_labels,command=self.redraw_overlays).pack(side="left",padx=4)
        ttk.Button(tools,text="↶",width=3,command=self.undo).pack(side="left",padx=(8,2))
        ttk.Button(tools,text="↷",width=3,command=self.redo).pack(side="left",padx=2)
        self.right_toggle_btn=ttk.Button(tools,text="Inspecteur ▶",command=self.toggle_right_panel,width=12); self.right_toggle_btn.pack(side="right")

        fr=ttk.LabelFrame(center,text="MAP - VUE DE TRAVAIL (collisions par-dessus)",padding=5); fr.pack(fill="both",expand=True)
        holder=ttk.Frame(fr); holder.pack(fill="both",expand=True)
        self.canvas=tk.Canvas(holder,bg="#20242a",highlightthickness=0,takefocus=True)
        hs=ttk.Scrollbar(holder,orient="horizontal",command=self.canvas.xview)
        vs=ttk.Scrollbar(holder,orient="vertical",command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=hs.set,yscrollcommand=vs.set)
        self.canvas.grid(row=0,column=0,sticky="nsew"); vs.grid(row=0,column=1,sticky="ns"); hs.grid(row=1,column=0,sticky="ew")
        holder.columnconfigure(0,weight=1); holder.rowconfigure(0,weight=1)
        self.canvas.bind("<Button-1>",self.mouse_down)
        self.canvas.bind("<B1-Motion>",self.mouse_drag)
        self.canvas.bind("<ButtonRelease-1>",self.mouse_up)
        self.canvas.bind("<Button-3>",self.right_click)
        self.canvas.bind("<Motion>",self.mouse_move)
        self.canvas.bind("<Double-Button-1>",self.double_click)
        self.canvas.bind("<MouseWheel>",self.mousewheel_zoom)
        self.canvas.bind("<Button-4>",self.mousewheel_zoom)
        self.canvas.bind("<Button-5>",self.mousewheel_zoom)

        info=ttk.Frame(center); info.pack(fill="x",pady=(5,0))
        self.tile_click_text=tk.StringVar(value="Clic sur une tuile = aperçu ; double-clic = loupe. Glisser = dessiner la collision.")
        ttk.Label(info,textvariable=self.tile_click_text,style="Sub.TLabel").pack(side="left")
        self.hit_text=tk.StringVar(value="")
        ttk.Label(info,textvariable=self.hit_text).pack(side="right")

        # ------------------------------------------------------------------
        # DROITE - inspecteur compact, jamais la moitié de l'écran.
        # ------------------------------------------------------------------
        right_top=ttk.Frame(right); right_top.pack(fill="x",pady=(0,4))
        ttk.Label(right_top,text="Inspecteur",font=("Segoe UI",9,"bold")).pack(side="left")
        ttk.Button(right_top,text="Fermer ▶",width=9,command=self.toggle_right_panel).pack(side="right")
        self.nb=ttk.Notebook(right); self.nb.pack(fill="both",expand=True)
        tabz=ttk.Frame(self.nb,padding=8); tabp=ttk.Frame(self.nb,padding=8); taba=ttk.Frame(self.nb,padding=8)
        tabt=ttk.Frame(self.nb,padding=8); tabe=ttk.Frame(self.nb,padding=8)
        self.nb.add(tabz,text="Zones"); self.nb.add(tabp,text="Propriétés"); self.nb.add(taba,text="Action"); self.nb.add(tabt,text="Tuile"); self.nb.add(tabe,text="Export")
        self.tile_tab=tabt

        self.tree=ttk.Treeview(tabz,columns=("type","forme"),show="tree headings",selectmode="browse")
        self.tree.heading("#0",text="Zone"); self.tree.heading("type",text="Type"); self.tree.heading("forme",text="Forme")
        self.tree.column("#0",width=110); self.tree.column("type",width=105); self.tree.column("forme",width=75)
        self.tree.pack(fill="both",expand=True)
        self.tree.bind("<<TreeviewSelect>>",lambda e:self.tree_select())
        bar=ttk.Frame(tabz); bar.pack(fill="x",pady=(6,0))
        ttk.Button(bar,text="Dupliquer",command=self.duplicate).pack(side="left")
        ttk.Button(bar,text="Supprimer",command=self.delete).pack(side="right")

        self.p_nom=tk.StringVar(); self.p_type=tk.StringVar(value="SOLIDE"); self.p_group=tk.StringVar(value="MONDE"); self.p_active=tk.BooleanVar(value=True); self.p_note=tk.StringVar()
        self._entry(tabp,"Nom",self.p_nom); self._combo(tabp,"Type",self.p_type,TYPES); self._entry(tabp,"Groupe",self.p_group)
        ttk.Checkbutton(tabp,text="Zone active",variable=self.p_active).pack(anchor="w",pady=(7,0))
        self._entry(tabp,"Note",self.p_note)
        geo=ttk.LabelFrame(tabp,text="Position / taille",padding=6); geo.pack(fill="x",pady=(9,2))
        self.p_x=tk.IntVar(value=0); self.p_y=tk.IntVar(value=0); self.p_w=tk.IntVar(value=0); self.p_h=tk.IntVar(value=0)
        r1=ttk.Frame(geo); r1.pack(fill="x")
        ttk.Label(r1,text="X").pack(side="left"); ttk.Entry(r1,textvariable=self.p_x,width=7).pack(side="left",padx=(3,7))
        ttk.Label(r1,text="Y").pack(side="left"); ttk.Entry(r1,textvariable=self.p_y,width=7).pack(side="left",padx=3)
        r2=ttk.Frame(geo); r2.pack(fill="x",pady=(4,0))
        ttk.Label(r2,text="L").pack(side="left"); ttk.Entry(r2,textvariable=self.p_w,width=7).pack(side="left",padx=(3,7))
        ttk.Label(r2,text="H").pack(side="left"); ttk.Entry(r2,textvariable=self.p_h,width=7).pack(side="left",padx=3)
        ttk.Label(tabp,text="Cibles",font=("Segoe UI",10,"bold")).pack(anchor="w",pady=(10,0))
        self.p_targets={}
        for k,label in labels.items():
            v=tk.BooleanVar(); self.p_targets[k]=v
            ttk.Checkbutton(tabp,text=label,variable=v).pack(anchor="w")
        ttk.Button(tabp,text="Appliquer",command=self.apply_props,style="Accent.TButton").pack(fill="x",pady=(12,4))

        self.a_active=tk.BooleanVar(); self.a_name=tk.StringVar(value="AUCUNE"); self.a_a=tk.StringVar(); self.a_b=tk.StringVar(); self.a_once=tk.BooleanVar()
        ttk.Checkbutton(taba,text="Action active",variable=self.a_active).pack(anchor="w")
        self._combo(taba,"Action",self.a_name,["AUCUNE","CHANGER_SCENE","CHECKPOINT","SET_FLAG","JOUER_SFX","JOUER_MUSIQUE","SPAWN","PALETTE","PERSONNALISE"])
        self._entry(taba,"Paramètre A",self.a_a); self._entry(taba,"Paramètre B",self.a_b)
        ttk.Checkbutton(taba,text="Une seule fois",variable=self.a_once).pack(anchor="w",pady=(7,0))
        ttk.Button(taba,text="Appliquer l'action",command=self.apply_action,style="Accent.TButton").pack(fill="x",pady=(12,4))

        # Tuile : aperçu beaucoup plus lisible, résultat flip inclus.
        self.tile_coord_var=tk.StringVar(value="Aucune tuile sélectionnée")
        ttk.Label(tabt,textvariable=self.tile_coord_var,font=("Segoe UI",10,"bold")).pack(anchor="w")
        row=ttk.Frame(tabt); row.pack(fill="x",pady=(8,4))
        ttk.Label(row,text="Couche").pack(side="left")
        self.tile_layer_var=tk.StringVar(value="BG A")
        lc=ttk.Combobox(row,textvariable=self.tile_layer_var,values=["BG A","BG B"],state="readonly",width=8); lc.pack(side="left",padx=6)
        lc.bind("<<ComboboxSelected>>",lambda e:self.refresh_tile_inspector())
        ttk.Button(row,text="OUVRIR LOUPE",command=self.open_tile_loupe,style="Accent.TButton").pack(side="right")
        ttk.Label(tabt,text="Tuile brute",style="Sub.TLabel").pack(anchor="w",pady=(8,2))
        self.tile_raw_preview=tk.Label(tabt,bg="#242830",width=128,height=128,bd=1,relief="solid"); self.tile_raw_preview.pack(anchor="w")
        ttk.Label(tabt,text="Résultat réellement affiché dans la map",style="Sub.TLabel").pack(anchor="w",pady=(10,2))
        self.tile_render_preview=tk.Label(tabt,bg="#242830",width=128,height=128,bd=1,relief="solid"); self.tile_render_preview.pack(anchor="w")
        flips=ttk.Frame(tabt); flips.pack(fill="x",pady=(7,4))
        self.flip_x_label=tk.Label(flips,text="FLIP X : non",bg="#292d34",fg="#8d97a3",padx=7,pady=4)
        self.flip_y_label=tk.Label(flips,text="FLIP Y : non",bg="#292d34",fg="#8d97a3",padx=7,pady=4)
        self.flip_x_label.pack(side="left",padx=(0,4)); self.flip_y_label.pack(side="left")
        self.tile_detail_var=tk.StringVar(value="Clique dans la map pour inspecter une cellule.")
        ttk.Label(tabt,textvariable=self.tile_detail_var,style="Sub.TLabel",wraplength=265,justify="left").pack(anchor="w",pady=(6,0))

        ttk.Button(tabe,text="Exporter .dcoll",command=self.export_action,style="Accent.TButton").pack(fill="x",pady=3)
        ttk.Button(tabe,text="Exporter bundle GDK",command=self.export_bundle).pack(fill="x",pady=3)
        ttk.Button(tabe,text="Sauver projet .dcollproj",command=self.save_project).pack(fill="x",pady=(14,3))
        self.report=tk.Text(tabe,bg="#202329",fg="#e8e8e8",relief="flat",wrap="word")
        self.report.pack(fill="both",expand=True,pady=(8,0)); self.report.configure(state="disabled")

        bottom=ttk.Frame(self,padding=(12,0,12,10)); bottom.pack(fill="x")
        self.status=ttk.Label(bottom,text="Charge une map DMS pour commencer."); self.status.pack(side="left")
        self.cursor=tk.StringVar(value="x- y-"); ttk.Label(bottom,textvariable=self.cursor,style="Sub.TLabel").pack(side="right")

    def _combo(self,parent,label,var,vals):
        ttk.Label(parent,text=label).pack(anchor="w",pady=(5,0))
        ttk.Combobox(parent,textvariable=var,values=vals,state="readonly").pack(fill="x")

    def _entry(self,parent,label,var):
        ttk.Label(parent,text=label).pack(anchor="w",pady=(5,0))
        ttk.Entry(parent,textvariable=var).pack(fill="x")

    def _bindings(self):
        # Les raccourcis globaux ne doivent jamais voler Suppr/Ctrl+Z à un champ texte.
        self.bind_all("<Control-z>",self._shortcut_undo)
        self.bind_all("<Control-y>",self._shortcut_redo)
        self.bind_all("<Escape>",lambda e:self.cancel())
        self.canvas.bind("<Delete>",lambda e:self.delete())
        self.tree.bind("<Delete>",lambda e:self.delete())
        self.canvas.bind("<Return>",lambda e:self.finish_polygon())

    @staticmethod
    def _is_text_editor(widget):
        return isinstance(widget,(tk.Entry,tk.Text,ttk.Entry,ttk.Combobox,tk.Spinbox))

    def _shortcut_undo(self,e):
        if self._is_text_editor(e.widget):return None
        self.undo(); return "break"

    def _shortcut_redo(self,e):
        if self._is_text_editor(e.widget):return None
        self.redo(); return "break"

    def _set_initial_panes(self):
        """Panneaux latéraux compacts ; la map reçoit l'essentiel de la fenêtre."""
        try:
            self.update_idletasks()
            w=max(1000,self.body.winfo_width())
            panes=self.body.panes()
            if len(panes)>=3:
                self.body.sashpos(0,min(300,max(250,int(w*0.18))))
                self.body.sashpos(1,max(620,w-292))
            elif len(panes)==2:
                # Selon le panneau masqué, on garde le panneau restant compact.
                if self.left_panel_visible:
                    self.body.sashpos(0,min(300,max(250,int(w*0.18))))
                else:
                    self.body.sashpos(0,max(620,w-292))
        except Exception:
            pass

    def toggle_left_panel(self):
        try:
            panes=set(self.body.panes())
            present=str(self.left_panel) in panes
            if present:
                self.body.forget(self.left_panel)
                self.left_panel_visible=False
                self.left_toggle_btn.configure(text="Outils ▶")
            else:
                self.body.insert(0,self.left_panel,weight=0)
                self.left_panel_visible=True
                self.left_toggle_btn.configure(text="◀ Outils")
                self.after_idle(self._set_initial_panes)
        except Exception as exc:
            self.status.configure(text=f"Panneau outils : {exc}")

    def toggle_right_panel(self):
        try:
            panes=set(self.body.panes())
            present=str(self.right_panel) in panes
            if present:
                self.body.forget(self.right_panel)
                self.right_panel_visible=False
                self.right_toggle_btn.configure(text="Inspecteur ◀")
            else:
                self.body.add(self.right_panel,weight=0)
                self.right_panel_visible=True
                self.right_toggle_btn.configure(text="Inspecteur ▶")
                self.after_idle(self._set_right_pane_width)
        except Exception as exc:
            self.status.configure(text=f"Panneau : {exc}")

    def _set_right_pane_width(self):
        try:
            self.update_idletasks(); w=self.body.winfo_width()
            if len(self.body.panes())>=3:
                self.body.sashpos(1,max(620,w-292))
        except Exception:
            pass

    def show_profile_help(self):
        messagebox.showinfo(
            "Préréglage de création",
            "Le profil de jeu n'est PAS une règle moteur et ne modifie jamais les zones déjà dessinées.\n\n"
            "Il sert uniquement à préparer les valeurs les plus probables pour la PROCHAINE zone : "
            "type, forme et cibles.\n\n"
            "Exemple : Plateforme prépare SOLIDE + RECTANGLE + Joueur/Ennemis. "
            "Shoot'em up prépare DANGER + RECTANGLE + Joueur.\n\n"
            "Choisis Personnalisé si tu veux conserver tous tes réglages manuellement."
        )

    def on_grid_change(self):
        self._static_dirty=True
        self.redraw()
        self.status.configure(text=f"Grille : pas {self.grid_var.get()} px.")

    def apply_profile(self):
        aliases={"PLATEFORME":"Plateforme","SHOOT_EM_UP":"Shoot'em up","SHOOT'EM UP":"Shoot'em up","VUE_DU_DESSUS":"Vue du dessus","ACTION_AVENTURE":"Action / aventure","PERSONNALISE":"Personnalisé"}
        p=aliases.get(str(self.profile_var.get()).upper(),self.profile_var.get())
        if p not in PROFILS:p="Personnalisé"
        self.profile_var.set(p); self.scene.profil=p
        presets={
            "Plateforme":("SOLIDE","RECTANGLE",(True,True,False,False,False),"Prépare les nouvelles zones de sol/mur. Tu peux ensuite choisir pente, échelle, danger, eau, etc."),
            "Shoot'em up":("DANGER","RECTANGLE",(True,False,False,False,False),"Prépare une zone de danger joueur. Utile aussi pour limites, triggers de spawn et sorties."),
            "Vue du dessus":("SOLIDE","POLYGONE",(True,True,False,False,False),"Prépare des obstacles polygonaux pour joueur et ennemis : murs, eau, portes, volumes irréguliers."),
            "Action / aventure":("SOLIDE","RECTANGLE",(True,True,False,False,True),"Prépare une zone physique mixte pouvant aussi concerner les objets."),
        }
        if p=="Personnalisé":
            self.help_profile.set("Aucun réglage n'est changé. Le profil n'agit que sur la création des prochaines zones.")
            return
        typ,forme,targets,help_text=presets.get(p,presets["Plateforme"])
        self.type_var.set(typ); self.forme_var.set(forme)
        for key,val in zip(("joueur","ennemis","projectiles_joueur","projectiles_ennemis","objets"),targets):
            self.target_vars[key].set(val)
        self.help_profile.set(help_text+" Les zones existantes ne sont jamais modifiées.")

    # ------------------------- HISTORY -------------------------

    def push(self):
        self.undo_stack.append((deepcopy(self.zones),self.next_id))
        if len(self.undo_stack)>40:self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:return
        self.redo_stack.append((deepcopy(self.zones),self.next_id))
        self.zones,self.next_id=self.undo_stack.pop()
        self.selected_id=None; self.refresh_tree(); self.redraw(); self.update_report()

    def redo(self):
        if not self.redo_stack:return
        self.undo_stack.append((deepcopy(self.zones),self.next_id))
        self.zones,self.next_id=self.redo_stack.pop()
        self.selected_id=None; self.refresh_tree(); self.redraw(); self.update_report()

    # ------------------------- IMPORT -------------------------

    def _sync_source_labels(self):
        mp=Path(self.scene.source_map).name if self.scene.source_map else "Aucune map chargée"
        tp=Path(self.scene.source_tileset).name if self.scene.source_tileset else "-"
        self.map_source_var.set(mp)
        self.tileset_source_var.set(f"Tileset : {tp}")
        if self.auto_reload.get() and (self.scene.source_map or self.scene.source_tileset or self.reference_path):
            self.live_status_var.set("LIVE : surveillance active")
        elif self.auto_reload.get():
            self.live_status_var.set("LIVE : en attente d’une source")
        else:
            self.live_status_var.set("LIVE : désactivé")

    def choose_tileset(self):
        p=filedialog.askopenfilename(title="Choisir le tileset de la map",filetypes=[("Tileset DMS / PNG","*.dimg *.png *.gif"),("Tous","*.*")])
        if not p:return
        self.scene.source_tileset=str(Path(p))
        if self.load_tileset(announce=True):
            self.redraw_full()
        self._sync_source_labels()

    def reload_sources_now(self):
        ok=False
        if self.scene.source_map and Path(self.scene.source_map).exists():
            ok=self._reload_map_external(Path(self.scene.source_map)) or ok
        elif self.scene.source_tileset:
            ok=self.load_tileset(announce=True) or ok
            self.redraw_full()
        if self.reference_path and Path(self.reference_path).exists():
            try:
                self.reference=tk.PhotoImage(file=self.reference_path); self._reference_zoom_cache={}; self._static_dirty=True; ok=True
            except Exception:pass
        self._sync_source_labels()
        if ok:self.status.configure(text="Sources rechargées depuis le disque.")

    def import_map(self):
        p=filedialog.askopenfilename(title="Importer map DMS",filetypes=[("Maps DMS","*.dmap *.dmapproj")])
        if not p:return
        try:
            if p.lower().endswith(".dmapproj"):
                self.scene,self.map_data=lire_dmapproj(p)
            else:
                self.scene,self.map_data=lire_dmap(p)
            self.scene.profil=self.profile_var.get()
            self.reference=None; self.reference_path=""; self._reference_zoom_cache={}
            self._invalidate_map_cache()
            tiles_ok=self.load_tileset(announce=False)
            self._remember_watch(p)
            self._sync_source_labels()
            self.scene_info.set(f"{self.scene.nom}\n{self.scene.largeur_px}×{self.scene.hauteur_px}px • cellule {self.scene.tile_size}px\nMap : {Path(p).name}\nTileset : {Path(self.scene.source_tileset).name if self.scene.source_tileset else 'MANQUANT'}")
            self.redraw()
            if tiles_ok:
                self.status.configure(text=f"Map prête : {Path(p).name} • {self.tilesource.count} tuiles.")
            else:
                self.status.configure(text=f"Map chargée, mais tileset introuvable. Clique « Choisir tileset… ».")
            self.refresh_tile_inspector(); self.after(50,self._set_initial_panes)
        except Exception as e:
            messagebox.showerror("Import map",str(e))

    def import_reference(self):
        p=filedialog.askopenfilename(title="Image référence",filetypes=[("PNG/GIF","*.png *.gif"),("Tous","*.*")])
        if not p:return
        try:
            self.reference=tk.PhotoImage(file=p); self.reference_path=p
            self._remember_watch(p)
            self._reference_zoom_cache={}
            self._static_dirty=True
            if not self.scene.source_map:
                self.scene.largeur_px=self.reference.width(); self.scene.hauteur_px=self.reference.height(); self.scene.nom=Path(p).stem
            self.scene_info.set(f"{self.scene.nom}\n{self.scene.largeur_px}×{self.scene.hauteur_px}px\nRéférence : {Path(p).name}")
            self._sync_source_labels(); self.redraw()
        except Exception as e: messagebox.showerror("Référence",str(e))

    def _resolve_tileset_path(self):
        p=self.scene.source_tileset
        if not p:
            return None
        pp=Path(p)
        if not pp.is_absolute() and self.scene.source_map:
            pp=Path(self.scene.source_map).parent/pp
        return pp

    def load_tileset(self,announce=True):
        """Charge en staging puis remplace le tileset visible seulement après succès."""
        pp=self._resolve_tileset_path()
        if pp is None:
            self._sync_source_labels()
            if announce:self.status.configure(text="Cette map ne référence aucun tileset. Utilise « Choisir tileset… ».")
            return False
        if not pp.exists():
            self._sync_source_labels()
            if announce:self.status.configure(text=f"Tileset introuvable : {pp}. Dernier tileset valide conservé.")
            return False
        try:
            staged=CollisionTileset(self); count=staged.load(pp,tile_size=self.scene.tile_size)
            self.tilesource=staged;self.tileset=staged.image;self.tiles=staged.tiles_base
            self._invalidate_map_cache();self._remember_watch(pp)
            if staged.tile_size!=self.scene.tile_size:
                self.status.configure(text=f"Attention : map {self.scene.tile_size}px / tileset {staged.tile_size}px.")
            elif announce:self.status.configure(text=f"Tileset chargé : {pp.name} • {count} tuiles • {staged.source_kind}")
            self._sync_source_labels();self.refresh_tile_inspector();return True
        except Exception as exc:
            self._sync_source_labels()
            if announce:self.status.configure(text=f"Échec tileset {pp.name} : {exc} • dernier tileset valide conservé")
            return False

    @staticmethod
    def _file_stamp(path):
        try:
            st=Path(path).stat()
            return (int(getattr(st,"st_mtime_ns",int(st.st_mtime*1e9))),int(st.st_size))
        except Exception:
            return None

    def _remember_watch(self,path):
        if not path:return
        try:self._watch_stamps[str(Path(path).resolve())]=self._file_stamp(path)
        except Exception:pass

    def _on_auto_reload_toggle(self):
        self._sync_source_labels()
        if self.auto_reload.get():self._schedule_file_watch()

    def destroy(self):
        self._closing=True
        if self._watch_job is not None:
            try:self.after_cancel(self._watch_job)
            except Exception:pass
            self._watch_job=None
        try:super().destroy()
        except tk.TclError:pass

    def _schedule_file_watch(self):
        if not self._closing and self._watch_job is None:
            self._watch_job=self.after(700,self._poll_files)

    def _poll_files(self):
        self._watch_job=None
        try:
            if self.auto_reload.get():
                # 1) la map elle-même : flips/cellules modifiés dans Map Builder apparaissent sans réimport.
                if self.scene.source_map:
                    mp=Path(self.scene.source_map)
                    key=str(mp.resolve())
                    stamp=self._file_stamp(mp); old=self._watch_stamps.get(key)
                    if stamp is not None and old is not None and stamp!=old:
                        if self._reload_map_external(mp):
                            self._watch_stamps[key]=stamp
                # 2) le tileset : workflow Piskel -> réexport au même chemin.
                tp=self._resolve_tileset_path()
                if tp is not None:
                    key=str(tp.resolve()); stamp=self._file_stamp(tp); old=self._watch_stamps.get(key)
                    if stamp is not None and old is not None and stamp!=old:
                        if self.load_tileset(announce=False):
                            self._watch_stamps[key]=stamp
                            self.redraw_full()
                            self._sync_source_labels(); self.status.configure(text=f"Tileset actualisé automatiquement : {tp.name}")
                # 3) image de référence éventuelle.
                if self.reference_path:
                    rp=Path(self.reference_path); key=str(rp.resolve()); stamp=self._file_stamp(rp); old=self._watch_stamps.get(key)
                    if stamp is not None and old is not None and stamp!=old:
                        try:
                            newref=tk.PhotoImage(file=str(rp))
                            self.reference=newref; self._reference_zoom_cache={}; self._static_dirty=True
                            self._watch_stamps[key]=stamp; self.redraw()
                            self.status.configure(text=f"Image de référence actualisée : {rp.name}")
                        except Exception:
                            pass
        finally:
            if not self._closing:
                self._watch_job=self.after(700,self._poll_files)

    def _reload_map_external(self,path):
        try:
            prof=self.scene.profil
            if str(path).lower().endswith(".dmapproj"):
                scene,data=lire_dmapproj(path)
            else:
                scene,data=lire_dmap(path)
            scene.profil=prof
            outside=[]
            for z in self.zones:
                x0,y0,x1,y1=z.bounds()
                if x0<0 or y0<0 or x1>scene.largeur_px or y1>scene.hauteur_px: outside.append(z.nom)
            self.scene=scene; self.map_data=data
            self._invalidate_map_cache(); self.load_tileset(announce=False)
            if outside:
                self.after(0,lambda names=outside: messagebox.showwarning("Map redimensionnée",f"{len(names)} zone(s) de collision sont maintenant partiellement ou totalement hors map :\n\n"+"\n".join(names[:12])))
            self.scene_info.set(f"{self.scene.nom}\n{self.scene.largeur_px}×{self.scene.hauteur_px}px • cellule {self.scene.tile_size}px\nActualisée depuis {Path(path).name}")
            self.redraw(); self.refresh_tile_inspector()
            self._sync_source_labels(); self.status.configure(text=f"Map actualisée automatiquement : {Path(path).name}")
            return True
        except Exception:
            # Sauvegarde externe potentiellement en cours : on réessaiera au prochain cycle.
            return False

    def import_map_collisions(self):
        if not self.map_data:
            messagebox.showinfo("Import","Charge d'abord une map DMS.")
            return
        if self.map_data.get("format")=="DMS_MAP_PROJECT":
            grid=self.map_data.get("collisions",[])
        else:
            grid=self.map_data.get("layers",{}).get("COLLISION",[])
        rects=fusionner_grille_collisions(grid,self.scene.tile_size)
        if not rects:
            messagebox.showinfo("Import","Aucune collision simple à importer.")
            return
        self.push()
        for typ,x,y,w,h in rects:
            ztyp=convertir_type_map(typ)
            z=Zone(
                id=self.next_id,nom=f"{ztyp}_{self.next_id}",type_zone=ztyp,forme="RECTANGLE",
                points=[(x,y),(x+w,y+h)],
                joueur=True,
                ennemis=ztyp in ("SOLIDE","PLATEFORME_1_SENS","ECHELLE","EAU","RALENTISSEMENT"),
                groupe="IMPORT_MAP"
            )
            self.zones.append(z); self.next_id+=1
        self.refresh_tree(); self.redraw(); self.update_report()
        self.status.configure(text=f"{len(rects)} zones fusionnées depuis la map.")

    # ------------------------- DRAW -------------------------

    def _invalidate_map_cache(self):
        self._map_base_cache=None
        self._map_zoom_cache={}
        self._static_dirty=True

    def _normalize_cell(self,c):
        if isinstance(c,dict):
            return {
                "tile_id":int(c.get("tile_id",-1)),
                "palette":int(c.get("palette",0)),
                "flip_x":bool(c.get("flip_x",False)),
                "flip_y":bool(c.get("flip_y",False)),
                "priority_code":int(c.get("priority_code",0)),
            }
        try:
            return {"tile_id":int(c),"palette":0,"flip_x":False,"flip_y":False,"priority_code":0}
        except Exception:
            return {"tile_id":-1,"palette":0,"flip_x":False,"flip_y":False,"priority_code":0}

    def _layer_grid(self,layer):
        if not self.map_data:return []
        if self.map_data.get("format")=="DMS_MAP_PROJECT":
            return self.map_data.get("bg_a" if layer=="BG A" else "bg_b",[])
        layers=self.map_data.get("layers",{})
        return layers.get("BG_A" if layer=="BG A" else "BG_B",[])

    def _cell_at(self,tx,ty,layer):
        grid=self._layer_grid(layer)
        if ty<0 or ty>=len(grid):return None
        row=grid[ty]
        if tx<0 or tx>=len(row):return None
        return self._normalize_cell(row[tx])

    def _compose_map_cache(self):
        if self._map_base_cache is not None:
            return self._map_base_cache
        if not self.map_data or self.tilesource.count<=0:
            return None
        w=max(1,int(self.scene.largeur_px)); h=max(1,int(self.scene.hauteur_px)); ts=int(self.scene.tile_size)
        # Fond de l'éditeur. BG B puis BG A ; les pixels transparents laissent voir la couche dessous.
        bg=(37,42,48)
        buf=bytearray(bytes(bg)*(w*h))
        for grid in (self._layer_grid("BG B"),self._layer_grid("BG A")):
            for ty,row in enumerate(grid):
                py0=ty*ts
                if py0>=h:break
                for tx,raw in enumerate(row):
                    px0=tx*ts
                    if px0>=w:break
                    c=self._normalize_cell(raw); tid=c["tile_id"]
                    if not (0<=tid<self.tilesource.count):continue
                    pix=self.tilesource.pixels(tid,c["flip_x"],c["flip_y"],c["palette"])
                    if pix is None:continue
                    for iy,prow in enumerate(pix):
                        py=py0+iy
                        if py>=h:break
                        off=(py*w+px0)*3
                        for ix,rgb in enumerate(prow):
                            if px0+ix>=w:break
                            if rgb is not None:
                                j=off+ix*3; buf[j]=rgb[0]; buf[j+1]=rgb[1]; buf[j+2]=rgb[2]
        ppm=b"P6\n%d %d\n255\n"%(w,h)+bytes(buf)
        self._map_base_cache=tk.PhotoImage(master=self,data=ppm,format="PPM")
        self._map_zoom_cache={1:self._map_base_cache}
        return self._map_base_cache

    def _apply_zoom(self,new_zoom,event=None):
        requested=max(1,min(8,int(new_zoom)))
        # Un PhotoImage agrandi couvre toute la map : on évite les allocations géantes
        # sur les niveaux de plusieurs milliers de pixels tout en gardant 8× sur une scène standard.
        area=max(1,int(self.scene.largeur_px)*int(self.scene.hauteur_px))
        safe=max(1,min(8,int(math.sqrt(24_000_000/area))))
        new_zoom=min(requested,safe)
        old_zoom=max(1,int(self.zoom))
        if new_zoom==old_zoom:
            self.zoom_var.set(f"{new_zoom}×"); return
        world_x=world_y=None
        if event is not None:
            try:
                world_x=self.canvas.canvasx(event.x)/old_zoom
                world_y=self.canvas.canvasy(event.y)/old_zoom
            except Exception:
                world_x=world_y=None
        else:
            try:
                cx=self.canvas.winfo_width()/2; cy=self.canvas.winfo_height()/2
                world_x=self.canvas.canvasx(cx)/old_zoom; world_y=self.canvas.canvasy(cy)/old_zoom
                event=type("ZoomAnchor",(),{"x":cx,"y":cy})()
            except Exception:
                pass
        self.zoom=new_zoom; self.zoom_var.set(f"{new_zoom}×")
        self._static_dirty=True; self.redraw()
        if world_x is not None and event is not None:
            try:
                self.update_idletasks()
                total_w=max(1,self.scene.largeur_px*new_zoom); total_h=max(1,self.scene.hauteur_px*new_zoom)
                left=world_x*new_zoom-event.x; top=world_y*new_zoom-event.y
                self.canvas.xview_moveto(max(0.0,min(1.0,left/total_w)))
                self.canvas.yview_moveto(max(0.0,min(1.0,top/total_h)))
            except Exception:
                pass
        if requested!=new_zoom:
            self.status.configure(text=f"Zoom limité à {new_zoom}× pour garder cette grande map fluide.")
        else:
            self.status.configure(text=f"Zoom {new_zoom}×")

    def set_zoom(self):
        self._apply_zoom(int(self.zoom_var.get().replace("×","")))

    def change_zoom(self,delta):
        self._apply_zoom(self.zoom+int(delta))

    def mousewheel_zoom(self,e):
        delta=getattr(e,"delta",0)
        num=getattr(e,"num",0)
        direction=1 if delta>0 or num==4 else -1
        self._apply_zoom(self.zoom+direction,e)
        return "break"

    def snapped(self,v):
        if not self.snap_var.get():return int(round(v))
        g=max(1,int(self.grid_var.get())); return int(round(v/g)*g)

    def event_pos(self,e):
        x=self.canvas.canvasx(e.x)/self.zoom; y=self.canvas.canvasy(e.y)/self.zoom
        return (max(0,min(self.scene.largeur_px,self.snapped(x))),max(0,min(self.scene.hauteur_px,self.snapped(y))))

    def inspect_tile_event(self,e):
        if not self.map_data or self.tilesource.count<=0:return
        x=self.canvas.canvasx(e.x)/self.zoom; y=self.canvas.canvasy(e.y)/self.zoom
        ts=max(1,int(self.scene.tile_size)); tx=int(x//ts); ty=int(y//ts)
        if tx<0 or ty<0 or x>=self.scene.largeur_px or y>=self.scene.hauteur_px:return
        self.selected_tile_cell=(tx,ty)
        ca=self._cell_at(tx,ty,"BG A"); cb=self._cell_at(tx,ty,"BG B")
        if ca and ca.get("tile_id",-1)>=0:self.tile_layer_var.set("BG A")
        elif cb and cb.get("tile_id",-1)>=0:self.tile_layer_var.set("BG B")
        self.refresh_tile_inspector()

    def refresh_tile_inspector(self):
        if not hasattr(self,"tile_coord_var"):return
        if not self.selected_tile_cell or not self.map_data or self.tilesource.count<=0:
            self.tile_coord_var.set("Aucune tuile sélectionnée")
            self.tile_detail_var.set("Clique dans la map pour inspecter une cellule.")
            self.tile_raw_preview.configure(image=""); self.tile_render_preview.configure(image="")
            self._tile_preview_images=[]
            return
        tx,ty=self.selected_tile_cell; layer=self.tile_layer_var.get(); c=self._cell_at(tx,ty,layer)
        if c is None:
            self.tile_coord_var.set(f"Cellule {tx}, {ty} • {layer}")
            self.tile_detail_var.set("Aucune donnée sur cette cellule.")
            return
        tid=c["tile_id"]; fx=c["flip_x"]; fy=c["flip_y"]; pal=c["palette"]; prio=c["priority_code"]
        self.tile_coord_var.set(f"Cellule {tx}, {ty} • {layer}")
        if tid<0 or tid>=self.tilesource.count:
            self.tile_detail_var.set(f"Tuile vide / ID {tid}")
            self.tile_raw_preview.configure(image=""); self.tile_render_preview.configure(image="")
            self._tile_preview_images=[]
            return
        z=max(1,min(20,120//max(1,self.tilesource.tile_size)))
        raw=self.tilesource.display_tile(tid,False,False,z,pal)
        rendered=self.tilesource.display_tile(tid,fx,fy,z,pal)
        self._tile_preview_images=[raw,rendered]
        self.tile_raw_preview.configure(image=raw); self.tile_render_preview.configure(image=rendered)
        self.flip_x_label.configure(text=f"FLIP X : {'OUI' if fx else 'non'}",fg="#ffd166" if fx else "#8d97a3")
        self.flip_y_label.configure(text=f"FLIP Y : {'OUI' if fy else 'non'}",fg="#ffd166" if fy else "#8d97a3")
        self.tile_detail_var.set(
            f"Tuile #{tid} • palette {pal} • priorité {prio}\n"
            f"Source : {Path(self.tilesource.path).name} ({self.tilesource.source_kind})\n"
            f"Taille : {self.tilesource.tile_size}×{self.tilesource.tile_size}px"
        )
        flips=[]
        if fx:flips.append("Flip X")
        if fy:flips.append("Flip Y")
        self.tile_click_text.set(f"Tuile #{tid} • {layer}"+(" • "+" + ".join(flips) if flips else " • sans flip"))

    def open_tile_loupe(self):
        if not self.selected_tile_cell:
            messagebox.showinfo("Loupe tuile","Clique d'abord une tuile dans la map.")
            return
        tx,ty=self.selected_tile_cell; layer=self.tile_layer_var.get(); c=self._cell_at(tx,ty,layer)
        if not c or not (0<=c["tile_id"]<self.tilesource.count):
            messagebox.showinfo("Loupe tuile","Cette cellule ne contient pas de tuile valide.")
            return
        if self._tile_loupe is not None:
            try:self._tile_loupe.destroy()
            except Exception:pass
        win=tk.Toplevel(self); self._tile_loupe=win
        win.title(f"Loupe tuile #{c['tile_id']} - {layer} ({tx},{ty})"); win.geometry("520x330"); win.configure(bg="#17191d")
        ttk.Label(win,text=f"Tuile #{c['tile_id']} • cellule {tx},{ty} • {layer}",font=("Segoe UI",13,"bold")).pack(pady=(12,6))
        frame=ttk.Frame(win); frame.pack(fill="both",expand=True,padx=12,pady=6)
        z=max(1,min(28,192//max(1,self.tilesource.tile_size)))
        raw=self.tilesource.display_tile(c["tile_id"],False,False,z,c["palette"])
        rendered=self.tilesource.display_tile(c["tile_id"],c["flip_x"],c["flip_y"],z,c["palette"])
        win._images=[raw,rendered]
        for title,img in (("Brute",raw),("Rendu réel",rendered)):
            col=ttk.Frame(frame); col.pack(side="left",fill="both",expand=True,padx=8)
            ttk.Label(col,text=title).pack()
            tk.Label(col,image=img,bg="#242830",bd=1,relief="solid").pack(pady=6)
        ttk.Label(win,text=f"Flip X : {'OUI' if c['flip_x'] else 'non'}   •   Flip Y : {'OUI' if c['flip_y'] else 'non'}   •   Palette {c['palette']}   •   Priorité {c['priority_code']}").pack(pady=(0,12))

    def redraw_full(self):
        self._static_dirty=True
        self.redraw()

    def _draw_static(self):
        # Décor et grille sont séparés des overlays de collision.
        # C'est le point clé de la V0.2 stable : un drag ne touche jamais au décor.
        self.canvas.delete("static")
        w=self.scene.largeur_px*self.zoom; h=self.scene.hauteur_px*self.zoom
        self.canvas.configure(scrollregion=(0,0,w,h))
        self.canvas.create_rectangle(0,0,w,h,fill="#252a30",outline="",tags=("static",))
        if self.show_map.get(): self.draw_map(tag="static")
        # État explicite : jamais une zone grise ambiguë.
        if not self.map_data and self.reference is None:
            cw=max(620,self.canvas.winfo_width()); ch=max(420,self.canvas.winfo_height())
            self.canvas.create_text(cw//2,ch//2-28,text="AUCUNE MAP CHARGÉE",fill="#f2f2f2",font=("Segoe UI",20,"bold"),tags=("static",))
            self.canvas.create_text(cw//2,ch//2+10,text="Clique « CHARGER MAP » puis choisis un .dmap ou .dmapproj",fill="#aeb7c2",font=("Segoe UI",11),tags=("static",))
        elif self.map_data and self.tilesource.count<=0 and self.reference is None:
            cw=max(620,self.canvas.winfo_width()); ch=max(420,self.canvas.winfo_height())
            self.canvas.create_text(cw//2,ch//2-28,text="MAP CHARGÉE - TILESET MANQUANT",fill="#ffd166",font=("Segoe UI",18,"bold"),tags=("static",))
            self.canvas.create_text(cw//2,ch//2+10,text="Utilise « Choisir tileset… » en haut. La map réapparaîtra immédiatement.",fill="#d6dbe1",font=("Segoe UI",11),tags=("static",))
        if self.show_grid.get():
            step=max(1,int(self.grid_var.get()))*self.zoom
            if step>=4:
                for x in range(0,int(w)+1,int(step)):
                    self.canvas.create_line(x,0,x,h,fill="#3e444d",tags=("static",))
                for y in range(0,int(h)+1,int(step)):
                    self.canvas.create_line(0,y,w,y,fill="#3e444d",tags=("static",))
        self._static_dirty=False

    def redraw_overlays(self):
        self._overlay_redraw_pending=False
        self.canvas.delete("overlay")
        for z in self.zones:self.draw_zone(z,z.id==self.selected_id,tag="overlay")
        if self.drawing:self.draw_temp(tag="overlay")
        if self.test_mode.get():self.draw_tester(tag="overlay")

    def schedule_overlay_redraw(self):
        # Coalesce les rafales <B1-Motion> : au plus un redraw overlay par tour Tk.
        if self._overlay_redraw_pending:
            return
        self._overlay_redraw_pending=True
        self.after_idle(self.redraw_overlays)

    def redraw(self):
        if self._static_dirty:
            self._draw_static()
        self.redraw_overlays()

    def draw_map(self,tag="static"):
        if self.reference is not None:
            if self.zoom not in self._reference_zoom_cache:
                self._reference_zoom_cache[self.zoom]=(
                    self.reference.zoom(self.zoom,self.zoom) if self.zoom>1 else self.reference
                )
            im=self._reference_zoom_cache[self.zoom]
            self.canvas.create_image(0,0,image=im,anchor="nw",tags=(tag,))
            return
        base=self._compose_map_cache()
        if base is None:
            return
        if self.zoom not in self._map_zoom_cache:
            self._map_zoom_cache[self.zoom]=base.zoom(self.zoom,self.zoom) if self.zoom>1 else base
        im=self._map_zoom_cache[self.zoom]
        self.canvas.create_image(0,0,image=im,anchor="nw",tags=(tag,))

    def draw_zone(self,z,sel=False,tag="overlay"):
        col=COULEURS.get(z.type_zone,"#fff"); width=4 if sel else 2
        pts=[(x*self.zoom,y*self.zoom) for x,y in z.points]
        if z.forme=="RECTANGLE" and len(pts)>=2:
            self.canvas.create_rectangle(pts[0][0],pts[0][1],pts[1][0],pts[1][1],outline=col,width=width,fill=col,stipple="gray50",tags=(tag,))
        elif z.forme in ("SEGMENT","PENTE") and len(pts)>=2:
            self.canvas.create_line(pts[0][0],pts[0][1],pts[1][0],pts[1][1],fill=col,width=max(3,width),tags=(tag,))
        elif z.forme=="POLYGONE" and len(pts)>=3:
            flat=[v for p in pts for v in p]; self.canvas.create_polygon(flat,outline=col,fill=col,stipple="gray50",width=width,tags=(tag,))
        elif z.forme=="POINT" and pts:
            x,y=pts[0]; self.canvas.create_oval(x-7,y-7,x+7,y+7,fill=col,outline="#fff",width=width,tags=(tag,))
        if self.show_labels.get() and pts:
            b=z.bounds(); self.canvas.create_text(b[0]*self.zoom+3,b[1]*self.zoom+3,text=f"{z.nom} [{z.type_zone}]",anchor="nw",fill="#fff",font=("Segoe UI",7,"bold"),tags=(tag,))

    def draw_temp(self,tag="overlay"):
        col=COULEURS.get(self.type_var.get(),"#fff")
        if self.forme_var.get()=="POLYGONE":
            pts=self.poly+([self.current] if self.current else [])
            if len(pts)>=2:
                flat=[]
                for p in pts:flat.extend((p[0]*self.zoom,p[1]*self.zoom))
                self.canvas.create_line(*flat,fill=col,width=2,dash=(4,2),tags=(tag,))
        elif self.start and self.current:
            x0,y0=self.start; x1,y1=self.current
            if self.forme_var.get()=="RECTANGLE":
                self.canvas.create_rectangle(x0*self.zoom,y0*self.zoom,x1*self.zoom,y1*self.zoom,outline=col,width=2,dash=(4,2),tags=(tag,))
            else:
                self.canvas.create_line(x0*self.zoom,y0*self.zoom,x1*self.zoom,y1*self.zoom,fill=col,width=2,dash=(4,2),tags=(tag,))

    def draw_tester(self,tag="overlay"):
        w=max(1,int(self.test_w.get())); h=max(1,int(self.test_h.get()))
        rect=(self.test_x,self.test_y,self.test_x+w,self.test_y+h)
        hits=[z for z in self.zones if z.joueur and zone_touche_rect(z,rect)]
        self.hit_text.set("Aucune collision" if not hits else "Touche : "+", ".join(z.nom for z in hits[:5]))
        self.canvas.create_rectangle(rect[0]*self.zoom,rect[1]*self.zoom,rect[2]*self.zoom,rect[3]*self.zoom,outline="#fff",width=3,fill="#fff",stipple="gray50",tags=(tag,))

    # ------------------------- MOUSE -------------------------

    def mouse_down(self,e):
        self.inspect_tile_event(e)
        p=self.event_pos(e)
        if self.test_mode.get():
            self.test_x=p[0]-max(1,int(self.test_w.get()))//2; self.test_y=p[1]-max(1,int(self.test_h.get()))//2; self.schedule_overlay_redraw(); return
        if self.forme_var.get()=="POLYGONE":
            if not self.drawing:self.drawing=True; self.poly=[]
            self.poly.append(p); self.current=p; self.schedule_overlay_redraw(); return
        if self.forme_var.get()=="POINT":
            self.push(); self.create_zone([p],"POINT"); return
        self.drawing=True; self.start=p; self.current=p; self.schedule_overlay_redraw()

    def mouse_drag(self,e):
        p=self.event_pos(e)
        if self.test_mode.get():
            self.test_x=p[0]-max(1,int(self.test_w.get()))//2; self.test_y=p[1]-max(1,int(self.test_h.get()))//2; self.schedule_overlay_redraw(); return
        if self.drawing and self.forme_var.get()!="POLYGONE":
            self.current=p; self.schedule_overlay_redraw()

    def mouse_up(self,e):
        if self.test_mode.get() or not self.drawing or self.forme_var.get()=="POLYGONE":return
        p=self.event_pos(e)
        forme=self.forme_var.get()
        start=self.start
        if start and p!=start:
            if forme=="RECTANGLE" and not rectangle_non_nul([start,p]):
                self.status.configure(text="Rectangle refusé : largeur et hauteur doivent être supérieures à 0.")
                messagebox.showwarning("Zone vide","Rectangle non créé : largeur et hauteur doivent être supérieures à 0.")
                self.cancel(); return
            # Fin du dessin AVANT create_zone : un seul redraw, sans overlay temporaire résiduel.
            self.drawing=False; self.start=None; self.current=None; self.poly=[]
            self.push(); self.create_zone([start,p],forme)
            return
        self.cancel()
        if forme not in ("POLYGONE","POINT") and self.selected_tile_cell:
            self.open_tile_loupe()

    def double_click(self,e):
        if self.drawing and self.forme_var.get()=="POLYGONE":
            self.finish_polygon(); return
        self.inspect_tile_event(e)
        self.open_tile_loupe()

    def finish_polygon(self):
        if self.drawing and self.forme_var.get()=="POLYGONE" and len(self.poly)>=3:
            self.push(); self.create_zone(list(self.poly),"POLYGONE")
        self.cancel()

    def cancel(self):
        self.drawing=False; self.start=None; self.current=None; self.poly=[]; self.redraw_overlays()

    def mouse_move(self,e):
        p=self.event_pos(e); self.cursor.set(f"x{p[0]} y{p[1]}")
        if self.drawing and self.forme_var.get()=="POLYGONE":
            self.current=p; self.schedule_overlay_redraw()

    def right_click(self,e):
        self.inspect_tile_event(e)
        p=self.event_pos(e)
        for z in reversed(self.zones):
            if self.hit_point_zone(p,z):
                self.select(z.id); break

    def hit_point_zone(self,p,z):
        x,y=p
        if z.forme=="RECTANGLE":return point_dans_rect(x,y,z.bounds())
        if z.forme=="POLYGONE":return point_dans_polygone(x,y,z.points)
        if z.forme in ("SEGMENT","PENTE") and len(z.points)>=2:return distance_point_segment(x,y,z.points[0],z.points[1])<=5
        if z.forme=="POINT" and z.points:return math.hypot(x-z.points[0][0],y-z.points[0][1])<=8
        return False

    # ------------------------- ZONES -------------------------

    def create_zone(self,pts,forme):
        if forme=="RECTANGLE" and not rectangle_non_nul(pts):
            self.status.configure(text="Rectangle ignoré : géométrie nulle.")
            return False
        if forme=="RECTANGLE" and len(pts)>=2:
            x0=min(pts[0][0],pts[1][0]); y0=min(pts[0][1],pts[1][1]); x1=max(pts[0][0],pts[1][0]); y1=max(pts[0][1],pts[1][1])
            pts=[(x0,y0),(x1,y1)]
        z=Zone(
            id=self.next_id,
            nom=(self.nom_var.get().strip() or "Zone")+f"_{self.next_id}",
            type_zone=self.type_var.get(),
            forme=forme,
            points=[(int(x),int(y)) for x,y in pts],
            joueur=self.target_vars["joueur"].get(),
            ennemis=self.target_vars["ennemis"].get(),
            projectiles_joueur=self.target_vars["projectiles_joueur"].get(),
            projectiles_ennemis=self.target_vars["projectiles_ennemis"].get(),
            objets=self.target_vars["objets"].get(),
            groupe=self.groupe_var.get().strip() or "MONDE"
        )
        self.zones.append(z); self.next_id+=1
        self.refresh_tree(); self.select(z.id); self.update_report()
        return True

    def refresh_tree(self):
        for i in self.tree.get_children():self.tree.delete(i)
        for z in self.zones:self.tree.insert("", "end", iid=str(z.id), text=z.nom, values=(z.type_zone,z.forme))

    def tree_select(self):
        sel=self.tree.selection()
        if sel:self.select(int(sel[0]))

    def select(self,zid):
        self.selected_id=zid
        if self.tree.exists(str(zid)) and self.tree.selection()!= (str(zid),):
            self.tree.selection_set(str(zid))
        z=self.selected()
        if z:
            self.p_nom.set(z.nom); self.p_type.set(z.type_zone); self.p_group.set(z.groupe); self.p_active.set(z.active); self.p_note.set(z.note)
            x0,y0,x1,y1=z.bounds(); self.p_x.set(int(x0)); self.p_y.set(int(y0)); self.p_w.set(int(x1-x0)); self.p_h.set(int(y1-y0))
            for k,v in self.p_targets.items():v.set(getattr(z,k))
            self.a_active.set(z.action.active); self.a_name.set(z.action.action); self.a_a.set(z.action.parametre_a); self.a_b.set(z.action.parametre_b); self.a_once.set(z.action.une_fois)
        self.redraw_overlays()

    def selected(self):
        return next((z for z in self.zones if z.id==self.selected_id),None)

    def apply_props(self):
        z=self.selected()
        if not z:return
        self.push()
        z.nom=self.p_nom.get().strip() or z.nom; z.type_zone=self.p_type.get(); z.groupe=self.p_group.get().strip() or "MONDE"; z.active=self.p_active.get(); z.note=self.p_note.get().strip()
        if z.forme=="RECTANGLE":
            try:
                x=int(self.p_x.get()); y=int(self.p_y.get()); w=max(1,int(self.p_w.get())); h=max(1,int(self.p_h.get()))
                z.points=[(x,y),(x+w,y+h)]
            except Exception as exc:
                self.undo();messagebox.showerror("Propriétés",f"Géométrie invalide : {exc}");return
        for k,v in self.p_targets.items():setattr(z,k,v.get())
        self.refresh_tree(); self.select(z.id); self.update_report()

    def apply_action(self):
        z=self.selected()
        if not z:return
        self.push()
        z.action=ActionZone(self.a_active.get(),self.a_name.get(),self.a_a.get().strip(),self.a_b.get().strip(),self.a_once.get())
        self.update_report()

    def duplicate(self):
        z=self.selected()
        if not z:return
        self.push(); nz=deepcopy(z); nz.id=self.next_id; nz.nom=z.nom+"_copie"; nz.points=[(x+8,y+8) for x,y in nz.points]
        self.next_id+=1; self.zones.append(nz); self.refresh_tree(); self.select(nz.id); self.update_report()

    def delete(self):
        z=self.selected()
        if not z:return
        self.push(); self.zones=[x for x in self.zones if x.id!=z.id]; self.selected_id=None; self.refresh_tree(); self.redraw(); self.update_report()

    # ------------------------- PROJECT -------------------------

    def project_dict(self,path):
        sm=self.scene.source_map; st=self.scene.source_tileset; ref=self.reference_path
        try:
            base=Path(path).parent
            if sm:
                sm=os.path.relpath(sm,base) if Path(sm).is_absolute() else sm
            if st:
                st=os.path.relpath(st,base) if Path(st).is_absolute() else st
            if ref:
                ref=os.path.relpath(ref,base) if Path(ref).is_absolute() else ref
        except Exception:pass
        scene_data=asdict(self.scene); scene_data["source_map"]=sm; scene_data["source_tileset"]=st
        return {
            "format":"DMS_COLLISION_PROJECT","version":1,"app_version":APP_VERSION,
            "scene":scene_data,
            "reference_image":ref,
            "zones":[asdict(z) for z in self.zones],
            "next_id":self.next_id,
            "view":{
                "zoom":self.zoom,"grid_px":self.grid_var.get(),"snap":self.snap_var.get(),
                "show_grid":self.show_grid.get(),"show_map":self.show_map.get(),"show_labels":self.show_labels.get(),
                "auto_reload":self.auto_reload.get(),"left_panel":self.left_panel_visible,"right_panel":self.right_panel_visible
            }
        }

    def save_project(self):
        p=self.project_path or filedialog.asksaveasfilename(title="Sauver projet",defaultextension=".dcollproj",initialfile=self.scene.nom+".dcollproj",filetypes=[("DMS Collision Project","*.dcollproj")])
        if not p:return
        try:
            target=Path(p);tmp=target.with_name(target.name+".tmp");tmp.write_text(json.dumps(self.project_dict(target),indent=2,ensure_ascii=False),encoding="utf-8");os.replace(tmp,target)
            self.project_path=str(target);self.status.configure(text=f"Projet sauvé : {target.name}")
        except Exception as exc:
            try:tmp.unlink(missing_ok=True)
            except Exception:pass
            messagebox.showerror("Sauvegarde",str(exc))

    def open_project(self):
        p=filedialog.askopenfilename(title="Ouvrir projet",filetypes=[("DMS Collision Project","*.dcollproj"),("JSON","*.json")])
        if not p:return
        try:
            data=json.loads(Path(p).read_text(encoding="utf-8"))
            if data.get("format")!="DMS_COLLISION_PROJECT":raise ValueError("Format invalide.")
            sd=dict(data.get("scene",{}))
            for key in ("source_map","source_tileset"):
                val=sd.get(key,"")
                if val:
                    pp=Path(val)
                    if not pp.is_absolute():pp=Path(p).parent/pp
                    sd[key]=str(pp)
            self.scene=Scene(**sd)
            self.profile_var.set(self.scene.profil); self.apply_profile()
            self.zones=[]
            for source_zd in data.get("zones",[]):
                zd=dict(source_zd); ad=zd.pop("action",{})
                zd["points"]=[tuple(x) for x in zd.get("points",[])]
                self.zones.append(Zone(action=ActionZone(**ad),**zd))
            self.next_id=int(data.get("next_id",1))
            self.reference=None; self.reference_path=""; self.map_data=None
            ref=data.get("reference_image","")
            if ref:
                rp=Path(ref)
                if not rp.is_absolute():rp=Path(p).parent/rp
                if rp.exists():
                    self.reference=tk.PhotoImage(file=str(rp)); self.reference_path=str(rp); self._remember_watch(rp)
            if self.scene.source_map and Path(self.scene.source_map).exists():
                try:
                    if self.scene.source_map.lower().endswith(".dmapproj"):_,self.map_data=lire_dmapproj(self.scene.source_map)
                    else:_,self.map_data=lire_dmap(self.scene.source_map)
                    self._remember_watch(self.scene.source_map); self.load_tileset(announce=False)
                except Exception:pass
            elif self.scene.source_tileset:
                self.load_tileset(announce=False)
            vw=data.get("view",{})
            self.zoom=max(1,min(8,int(vw.get("zoom",2)))); self.zoom_var.set(f"{self.zoom}×")
            self.grid_var.set(int(vw.get("grid_px",8))); self.snap_var.set(bool(vw.get("snap",True)))
            self.show_grid.set(bool(vw.get("show_grid",True))); self.show_map.set(bool(vw.get("show_map",True))); self.show_labels.set(bool(vw.get("show_labels",True)))
            self.auto_reload.set(bool(vw.get("auto_reload",True)))
            want_left=bool(vw.get("left_panel",True))
            if want_left!=self.left_panel_visible:self.toggle_left_panel()
            want_right=bool(vw.get("right_panel",True))
            if want_right!=self.right_panel_visible:self.toggle_right_panel()
            self._invalidate_map_cache(); self._reference_zoom_cache={}; self._static_dirty=True
            self.project_path=p; self.refresh_tree(); self.redraw(); self.update_report(); self.refresh_tile_inspector()
            self._sync_source_labels()
            self.scene_info.set(f"{self.scene.nom}\n{self.scene.largeur_px}×{self.scene.hauteur_px}px • cellule {self.scene.tile_size}px\nMap : {Path(self.scene.source_map).name if self.scene.source_map else 'AUCUNE'}\nTileset : {Path(self.scene.source_tileset).name if self.scene.source_tileset else 'MANQUANT'}")
            self.status.configure(text=f"Projet ouvert : {Path(p).name}"); self.after(50,self._set_initial_panes)
        except Exception as e:messagebox.showerror("Ouverture",str(e))

    # ------------------------- EXPORT -------------------------

    def export_action(self):
        p=filedialog.asksaveasfilename(title="Exporter DCOLL",defaultextension=".dcoll",initialfile=self.scene.nom+".dcoll",filetypes=[("DMS Collision Resource","*.dcoll")])
        if not p:return None
        try:
            export_dcoll(p,self.scene,self.zones)
            self.status.configure(text=f"DCOLL exporté : {Path(p).name}")
            messagebox.showinfo("Export",f"{len(self.zones)} zones exportées.")
            return p
        except Exception as e:messagebox.showerror("Export",str(e)); return None

    def export_bundle(self):
        folder=filedialog.askdirectory(title="Bundle DMS-GDK")
        if not folder:return
        safe="".join(c if c.isalnum() else "_" for c in self.scene.nom.upper()).strip("_") or "DMS_SCENE"
        dcoll=Path(folder)/f"{safe.lower()}.dcoll"
        try:
            export_dcoll(dcoll,self.scene,self.zones)
        except Exception as exc:
            messagebox.showerror("Bundle GDK",str(exc));return
        h=[
            "#pragma once","",
            f"/* Generated by {APP_NAME} {APP_VERSION} */",
            "#define DMS_COLL_TARGET_PLAYER 0x01",
            "#define DMS_COLL_TARGET_ENEMIES 0x02",
            "#define DMS_COLL_TARGET_PLAYER_SHOTS 0x04",
            "#define DMS_COLL_TARGET_ENEMY_SHOTS 0x08",
            "#define DMS_COLL_TARGET_OBJECTS 0x10","",
            f"#define {safe}_COLLISION_ZONE_COUNT {len(self.zones)}",
        ]
        (Path(folder)/f"{safe.lower()}_collision.h").write_text("\n".join(h),encoding="utf-8")
        (Path(folder)/f"{safe.lower()}_collision_report.txt").write_text(rapport(self.scene,self.zones),encoding="utf-8")
        self.status.configure(text="Bundle GDK exporté.")

    def update_report(self):
        self.report.configure(state="normal"); self.report.delete("1.0","end"); self.report.insert("1.0",rapport(self.scene,self.zones)); self.report.configure(state="disabled")


if __name__=="__main__":
    App().mainloop()
