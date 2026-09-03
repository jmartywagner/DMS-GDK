#!/usr/bin/env python3
"""DMS-GDK P1.2 genuine m68k-gcc cartridge builder.

Windows note: the canonical cross-GCC package can invoke unprefixed helper names
(`as`, `ld`) that are not present in some extracted layouts. To keep DMS-GDK
portable and deterministic, C compilation is split explicitly:
GCC -S -> m68k-elf-as -> m68k-elf-ld.
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from pathlib import Path

HERE=Path(__file__).resolve(); ROOT=HERE.parents[2]; GDK=ROOT/'GDK'; RUNTIME=ROOT/'RUNTIME'
sys.path.insert(0,str(RUNTIME/'tools'))
from dms_console90_format import save_image
from dms_console90_firmware import build_z80_native_driver
from dms_z80_native import build_native_commands, pack_banked_stream
from dmsres import compile_project, print_diagnostics
from dmsautobuild import prepare_project

class BuildError(RuntimeError): pass

def _emit(text:str, stream=None)->None:
    """Affiche une sortie capturée sans planter sur l'encodage console Windows."""
    stream = stream or sys.stdout
    enc = getattr(stream, 'encoding', None) or 'utf-8'
    try:
        stream.write(text)
    except UnicodeEncodeError:
        stream.write(text.encode(enc, errors='replace').decode(enc, errors='replace'))
    try:
        stream.flush()
    except Exception:
        pass

def find_toolchain() -> Path:
    base=ROOT/'TOOLCHAIN'/'m68k-elf'
    required=[
        base/'bin'/'m68k-elf-gcc.exe',
        base/'bin'/'m68k-elf-as.exe',
        base/'bin'/'m68k-elf-ld.exe',
        base/'bin'/'m68k-elf-objcopy.exe',
        base/'bin'/'m68k-elf-objdump.exe',
    ]
    # Permit extension-less executables on non-Windows/dev environments.
    for i,p in enumerate(required):
        if not p.exists():
            alt=p.with_suffix('')
            if not alt.exists():
                raise BuildError('toolchain locale absente/incomplete : '+str(base)+f' ; manque {p.name}')
    if os.name!='nt' and any(p.exists() and not p.with_suffix('').exists() for p in required):
        raise BuildError('toolchain 68000 Windows détectée mais non exécutable sur cet hôte ; lancer BUILD + RUN sous Windows ou installer les binaires m68k-elf natifs sans extension dans TOOLCHAIN/m68k-elf/bin')
    print('PASS : toolchain canonique locale :',base)
    return base

def exe(bin_dir:Path,name:str)->Path:
    p=bin_dir/(name+'.exe')
    if p.exists(): return p
    p=bin_dir/name
    if p.exists(): return p
    raise BuildError(f'{name} absent de la toolchain')

def find_libgcc(tc:Path)->Path:
    exact=tc/'lib'/'gcc'/'m68k-elf'/'14.2.0'/'libgcc.a'
    if exact.is_file(): return exact
    hits=sorted((tc/'lib'/'gcc'/'m68k-elf').glob('*/libgcc.a')) if (tc/'lib'/'gcc'/'m68k-elf').is_dir() else []
    if hits: return hits[-1]
    raise BuildError('libgcc.a absent de la toolchain canonique')

def make_tool_env(bin_dir:Path)->dict[str,str]:
    env=os.environ.copy(); old=env.get('PATH','')
    env['PATH']=str(bin_dir)+(os.pathsep+old if old else '')
    env['PREFIX']='m68k-elf-'
    return env

def run(cmd:list[str|Path], cwd:Path|None=None, capture:bool=False, env:dict[str,str]|None=None)->str:
    c=[str(x) for x in cmd]; print('  >',' '.join(c))
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' and hasattr(subprocess,'CREATE_NO_WINDOW') else 0
    try:
        r=subprocess.run(c,cwd=cwd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env,encoding='utf-8',errors='replace',creationflags=flags)
    except OSError as exc:raise BuildError(f'impossible d’exécuter {c[0]} : {exc}. Vérifier la plateforme et les droits de la toolchain.') from exc
    msg=r.stdout or ''
    if r.returncode:raise BuildError(f'commande échouée ({r.returncode}) : {c[0]}\n{msg}')
    if msg and not capture: _emit(msg)
    return msg if capture else ''


def select_project_sources(src:Path, generated_sources:list[Path], native_resource_pipeline:bool)->list[Path]:
    """Select project C sources without dropping valid legacy data or linking generated duplicates.

    - platform_data.c/platform_stream.c are ignored only when resources.dmsres is active,
      because only then the native resource pipeline can replace those historical helpers.
    - if dmsres generated a C file with the same basename as a file kept in src/, the
      generated copy is canonical for this build and the stale src copy is ignored.
    """
    legacy_helpers={'platform_data.c','platform_stream.c'}
    generated_names={p.name.lower() for p in generated_sources}
    selected=[]
    for csrc in sorted(src.glob('*.c')):
        name=csrc.name.lower()
        if native_resource_pipeline and name in legacy_helpers:
            print(f'AVERTISSEMENT : ancien helper ignore par le builder natif : {csrc.name}')
            continue
        if name in generated_names and name != 'resources.c':
            print(f'AVERTISSEMENT : source projet dupliquee par build/generated, copie generee prioritaire : {csrc.name}')
            continue
        selected.append(csrc)
    return selected

def clear_generated_products(generated:Path)->None:
    """Remove only dmsres build products so a deleted source cannot stay linked."""
    generated.mkdir(parents=True,exist_ok=True)
    allowed={'.c','.h','.bin','.json','.txt','.dmr'}
    for old in generated.iterdir():
        if old.is_file() and old.suffix.lower() in allowed:
            old.unlink()

def build(project_dir:Path, out:Path|None=None)->dict:
    project_dir=project_dir.resolve(); src=project_dir/'src'
    if not (src/'main.c').exists(): raise BuildError(f'{src / "main.c"} absent')
    tc=find_toolchain(); bindir=tc/'bin'
    gcc=exe(bindir,'m68k-elf-gcc'); assembler=exe(bindir,'m68k-elf-as'); linker=exe(bindir,'m68k-elf-ld')
    objcopy=exe(bindir,'m68k-elf-objcopy'); objdump=exe(bindir,'m68k-elf-objdump'); libgcc=find_libgcc(tc)
    tool_env=make_tool_env(bindir)
    builddir=project_dir/'build'; objdir=builddir/'obj'; asmdir=builddir/'asm'; generated=builddir/'generated'
    objdir.mkdir(parents=True,exist_ok=True); asmdir.mkdir(parents=True,exist_ok=True)
    for d in (objdir,asmdir):
        for old in d.glob('*'):
            if old.is_file(): old.unlink()

    print('[AUTO] Détection des réglages, scènes, acteurs, flow, audio, maps, collisions et graphismes')
    auto=prepare_project(project_dir)
    resource_result=None; manifest=auto.get('resource_manifest') or (project_dir/'resources.dmsres')
    if manifest and Path(manifest).exists():
        manifest=Path(manifest)
        print('[0/4] Compilation du manifeste ressources préparé -> build/generated')
        clear_generated_products(generated)
        resource_result=compile_project(manifest,generated); print_diagnostics(resource_result)
        if [d for d in resource_result.diagnostics if d.severity=='ERROR']:
            raise BuildError('resources.dmsres invalide; voir build/generated/DMSRES_REPORT.txt')

    cflags=['-m68000','-Os','-ffreestanding','-fno-builtin','-fomit-frame-pointer','-ffunction-sections','-fdata-sections','-Wall','-Wextra','-std=c11','-I',GDK/'include']
    cflags += ['-I',builddir/'autogen']
    if (builddir/'autogen'/'dms_game_settings_compat.h').is_file(): cflags += ['-include',builddir/'autogen'/'dms_game_settings_compat.h']
    if resource_result is not None: cflags += ['-I',generated]
    generated_sources=sorted(generated.glob('*.c')) if resource_result is not None else []
    generated_sources += [Path(x) for x in auto.get('generated_sources',[]) if Path(x).is_file()]
    project_sources=select_project_sources(
        src,
        generated_sources,
        native_resource_pipeline=(resource_result is not None),
    )
    sources=[GDK/'crt0'/'startup.s']+sorted((GDK/'lib'/'src').glob('*.c'))+generated_sources+project_sources

    objects=[]
    print('[1/4] Compilation explicite GCC -> AS -> objets Motorola 68000')
    for idx,s in enumerate(sources):
        o=objdir/f'{idx:02d}_{s.stem}.o'
        if s.suffix.lower() in ('.s','.asm'):
            run([assembler,'-m68000',s,'-o',o],env=tool_env)
        else:
            asm=asmdir/f'{idx:02d}_{s.stem}.s'
            run([gcc,*cflags,'-S',s,'-o',asm],env=tool_env)
            run([assembler,'-m68000',asm,'-o',o],env=tool_env)
        objects.append(o)

    elf=builddir/(project_dir.name+'.elf'); mapf=builddir/(project_dir.name+'.map')
    print('[2/4] Linkage explicite m68k-elf-ld')
    run([linker,'-T',GDK/'linker'/'dms1.ld','--gc-sections','-Map',mapf,'-o',elf,*objects,libgcc],env=tool_env)
    binary=builddir/(project_dir.name+'.bin')
    run([objcopy,'-O','binary',elf,binary],env=tool_env)
    disasm=run([objdump,'-d','-S',elf],capture=True,env=tool_env)
    (builddir/(project_dir.name+'.lst')).write_text(disasm,encoding='utf-8',errors='replace')
    m68k=binary.read_bytes()
    if len(m68k)>1024*1024: raise BuildError(f'programme 68000 trop grand: {len(m68k)} octets > 1 MiB')

    print('[3/4] Construction driver Z80 + catalogue DMR multi-musiques')
    music=None
    native_catalog=None
    if resource_result is not None and (generated/'audio_bus.dmr').is_file():
        music=generated/'audio_bus.dmr'
        if (generated/'audio_native.ndrv').is_file():
            native_catalog=generated/'audio_native.ndrv'
    if resource_result is not None:
        me=next((e for e in resource_result.entries if e.kind=='MUSIC'),None)
        if music is None and me is not None:
            music=me.path
    if music is None:
        music=project_dir/'res'/'music.dmr'
        if not music.exists(): music=RUNTIME/'MUSIQUES_DMR'/'01 - DMS1_Marble.dmr'
    dmr=music.read_bytes() if music.exists() else b''
    ndrv=native_catalog.read_bytes() if native_catalog is not None else b''
    if dmr and not ndrv:
        commands,_,_=build_native_commands(dmr)
        ndrv=pack_banked_stream(commands)
    def rel(path:Path)->str:
        return os.path.relpath(path.resolve(),project_dir).replace('\\','/')
    meta={
        'format':'DMS-GDK-P1.2-GCC-CORE','runtime':'DMS1 Console90 P1.0.9 + full 68000 core tier',
        'cpu_frontend':'gcc-m68k-musashi-p1.2','compiler':'m68k-elf-gcc 14.2 compatible',
        'source_project':'.','libdms_tier':'P1.2 + P0.4 DRES/DCOLL/DACTOR + native DMAP ring streaming/resource runtime',
        'resource_manifest':rel(Path(manifest)) if manifest and Path(manifest).exists() else None,
        'compile_pipeline':'gcc -S -> m68k-elf-as -> m68k-elf-ld'
    }
    chunks=[(b'M68K',m68k),(b'Z80 ',build_z80_native_driver()),(b'META',json.dumps(meta,indent=2,ensure_ascii=False).encode('utf-8'))]
    if resource_result is not None and (generated/'resources.bin').exists(): chunks.append((b'RSRC',(generated/'resources.bin').read_bytes()))
    if dmr: chunks += [(b'DMR0',dmr),(b'NDRV',ndrv)]
    out=(out or (builddir/(project_dir.name+'.dmc'))).resolve(); out.parent.mkdir(parents=True,exist_ok=True); save_image(out,chunks)
    report={'status':'PASS','rom':rel(out),'rom_bytes':out.stat().st_size,'m68k_binary_bytes':len(m68k),'elf':rel(elf),'map':rel(mapf),'listing':rel(builddir/(project_dir.name+'.lst')),'toolchain':rel(tc),'music_dmr_bytes':len(dmr),'audio_native_bytes':len(ndrv),
            'compile_pipeline':'gcc -S -> m68k-elf-as -> m68k-elf-ld','libgcc':rel(libgcc),
            'resource_count':len(resource_result.entries) if resource_result is not None else 0,'generated_dir':rel(generated) if resource_result is not None else None}
    (builddir/'BUILD_GCC_REPORT.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('[4/4] PASS : cartouche GCC DMS-1 ->',out); return report

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('project',type=Path); ap.add_argument('--out',type=Path,default=None); a=ap.parse_args()
    try: build(a.project,a.out); return 0
    except Exception as exc: _emit(f'ERREUR DMSGCC : {exc}\n', sys.stderr); return 2
if __name__=='__main__': raise SystemExit(main())
