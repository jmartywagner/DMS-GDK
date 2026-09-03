#!/usr/bin/env python3
"""DMS-GDK dependency-free PNG -> RGB333 palette + 4bpp tiles compiler.

Supported PNG input:
- RGB 8-bit (color type 2)
- RGBA 8-bit (color type 6)
- indexed/palettized PNG (color type 3), bit depths 1/2/4/8
- PLTE palette and optional tRNS alpha table
- indexed PNG without tRNS: source palette index 0 is transparent (DMS/SGDK convention)
- non-interlaced images

Output contract remains unchanged: RGB333, 4 bpp, max 16 colors,
transparent pixels mapped to index 0.
"""
from __future__ import annotations
import struct, zlib
from dataclasses import dataclass
from pathlib import Path

PNG_SIG=b"\x89PNG\r\n\x1a\n"

@dataclass(frozen=True)
class CompiledImage:
    width:int
    height:int
    palette:tuple[int,...]
    tiles:bytes


def _paeth(a:int,b:int,c:int)->int:
    p=a+b-c; pa=abs(p-a); pb=abs(p-b); pc=abs(p-c)
    return a if pa<=pb and pa<=pc else (b if pb<=pc else c)


def _unfilter_rows(dec:bytes, height:int, stride:int, bpp:int)->list[bytearray]:
    rows=[]; cursor=0; prev=bytearray(stride)
    expected=height*(stride+1)
    if len(dec) < expected:
        raise ValueError("PNG tronqué: données IDAT insuffisantes")
    for _y in range(height):
        f=dec[cursor]; cursor+=1
        cur=bytearray(dec[cursor:cursor+stride]); cursor+=stride
        for x in range(stride):
            a=cur[x-bpp] if x>=bpp else 0
            b=prev[x]
            c=prev[x-bpp] if x>=bpp else 0
            if f==1: cur[x]=(cur[x]+a)&255
            elif f==2: cur[x]=(cur[x]+b)&255
            elif f==3: cur[x]=(cur[x]+((a+b)//2))&255
            elif f==4: cur[x]=(cur[x]+_paeth(a,b,c))&255
            elif f!=0: raise ValueError(f"filtre PNG {f} non supporté")
        rows.append(cur); prev=cur
    return rows


def _unpack_indexed_row(row:bytes, width:int, bitdepth:int)->list[int]:
    if bitdepth==8:
        return list(row[:width])
    mask=(1<<bitdepth)-1
    per_byte=8//bitdepth
    out=[]
    for byte in row:
        for slot in range(per_byte):
            shift=8-bitdepth*(slot+1)
            out.append((byte>>shift)&mask)
            if len(out)>=width:
                return out
    if len(out)<width:
        raise ValueError("PNG indexé tronqué")
    return out


def read_png(path:Path)->tuple[int,int,list[tuple[int,int,int,int]]]:
    data=path.read_bytes()
    if not data.startswith(PNG_SIG):
        raise ValueError("PNG signature invalide")

    pos=8
    width=height=ctype=bitdepth=interlace=None
    raw=bytearray(); plte=None; trns=None
    while pos+12<=len(data):
        n=struct.unpack_from(">I",data,pos)[0]
        kind=data[pos+4:pos+8]
        payload=data[pos+8:pos+8+n]
        pos+=12+n
        if kind==b"IHDR":
            width,height,bitdepth,ctype,comp,filt,interlace=struct.unpack(">IIBBBBB",payload)
            if comp or filt or interlace:
                raise ValueError("PNG supporté: non entrelacé, compression/filtrage PNG standard")
            if ctype in (2,6):
                if bitdepth!=8:
                    raise ValueError("PNG RGB/RGBA: profondeur 8 bits requise")
            elif ctype==3:
                if bitdepth not in (1,2,4,8):
                    raise ValueError("PNG indexé: profondeur 1/2/4/8 bits requise")
            else:
                raise ValueError("PNG supporté: RGB, RGBA ou indexé/palettisé")
        elif kind==b"PLTE":
            if len(payload)%3:
                raise ValueError("PNG PLTE invalide")
            plte=[tuple(payload[i:i+3]) for i in range(0,len(payload),3)]
        elif kind==b"tRNS":
            trns=bytes(payload)
        elif kind==b"IDAT":
            raw.extend(payload)
        elif kind==b"IEND":
            break

    if width is None:
        raise ValueError("IHDR absent")
    if not raw:
        raise ValueError("IDAT absent")

    dec=zlib.decompress(bytes(raw))
    pixels=[]

    if ctype in (2,6):
        channels=3 if ctype==2 else 4
        stride=width*channels
        rows=_unfilter_rows(dec,height,stride,channels)
        for row in rows:
            for x in range(width):
                i=x*channels
                r,g,b=row[i:i+3]
                a=row[i+3] if channels==4 else 255
                pixels.append((r,g,b,a))
        return width,height,pixels

    # Indexed/palettized PNG.
    if not plte:
        raise ValueError("PNG indexé: chunk PLTE absent")
    stride=(width*bitdepth+7)//8
    # For PNG filters, bytes-per-pixel is 1 for sub-byte indexed formats.
    rows=_unfilter_rows(dec,height,stride,1)
    alpha=list(trns or b"")
    for row in rows:
        for idx in _unpack_indexed_row(row,width,bitdepth):
            if idx>=len(plte):
                raise ValueError(f"PNG indexé: index palette {idx} hors PLTE")
            r,g,b=plte[idx]
            # DMS/SGDK convention for indexed art: if no explicit tRNS alpha
            # table exists, source palette index 0 is the transparent slot.
            # If tRNS exists, it remains authoritative.
            if trns is None:
                a = 0 if idx == 0 else 255
            else:
                a = alpha[idx] if idx < len(alpha) else 255
            pixels.append((r,g,b,a))
    return width,height,pixels


def rgb333(r:int,g:int,b:int)->int:
    return ((round(r*7/255)&7)<<6)|((round(g*7/255)&7)<<3)|(round(b*7/255)&7)


def compile_png(path:Path)->CompiledImage:
    w,h,pixels=read_png(path)
    if w%8 or h%8:
        raise ValueError("dimensions PNG doivent être multiples de 8")

    # DMS color-key fallback for opaque RGB/RGBA tilesheets: if no source alpha
    # exists, the top-left source color is treated as the transparent slot.
    # Indexed PNG keeps its stricter index-0/tRNS behavior from read_png().
    has_transparency = any(a < 128 for _r,_g,_b,a in pixels)
    if not has_transparency and pixels:
        key_rgb = pixels[0][:3]
        key_count = sum(1 for r,g,b,_a in pixels if (r,g,b) == key_rgb)
        if key_count >= 2:
            pixels = [(r,g,b,0 if (r,g,b) == key_rgb else 255) for r,g,b,_a in pixels]

    palette=[0]
    mapping={None:0}
    indexed=[]
    for r,g,b,a in pixels:
        if a<128:
            indexed.append(0)
            continue
        c=rgb333(r,g,b)
        # Preserve the historical behavior that opaque black is distinct
        # from the transparent slot even though both have RGB333 word 0.
        key=(0,0,0) if c==0 else c
        if key not in mapping:
            if len(palette)>=16:
                raise ValueError("image >16 couleurs RGB333")
            mapping[key]=len(palette)
            palette.append(c)
        indexed.append(mapping[key])
    palette += [0]*(16-len(palette))
    out=bytearray()
    for ty in range(0,h,8):
        for tx in range(0,w,8):
            tile=[]
            for y in range(8):
                base=(ty+y)*w+tx
                tile.extend(indexed[base:base+8])
            for i in range(0,64,2):
                out.append((tile[i]<<4)|tile[i+1])
    return CompiledImage(w,h,tuple(palette),bytes(out))


def main()->int:
    import argparse, json
    ap=argparse.ArgumentParser()
    ap.add_argument("png",type=Path)
    ap.add_argument("--out",type=Path,required=True)
    a=ap.parse_args()
    c=compile_png(a.png)
    a.out.parent.mkdir(parents=True,exist_ok=True)
    meta={"width":c.width,"height":c.height,"palette_rgb333":list(c.palette),"tile_bytes":len(c.tiles)}
    a.out.with_suffix(".json").write_text(json.dumps(meta,indent=2),encoding="utf-8")
    a.out.write_bytes(c.tiles)
    print(f"DMS ASSET: {a.png.name} -> {c.width}x{c.height}, {len(c.tiles)//32} tiles, 4bpp, 16-col palette")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
