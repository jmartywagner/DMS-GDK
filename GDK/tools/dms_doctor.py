#!/usr/bin/env python3
from __future__ import annotations
import ctypes, json, os, subprocess, sys, tempfile
from pathlib import Path
HERE=Path(__file__).resolve(); ROOT=HERE.parents[2]; REPORT=ROOT/'DOCS_REPORTS/current/DMS_DOCTOR_LAST.txt'

def _exe(b:Path,n:str)->Path:
    p=b/(n+'.exe'); return p if p.exists() else b/n

def _libgcc(tc:Path)->Path|None:
    exact=tc/'lib/gcc/m68k-elf/14.2.0/libgcc.a'
    if exact.is_file(): return exact
    base=tc/'lib/gcc/m68k-elf'
    hits=sorted(base.glob('*/libgcc.a')) if base.is_dir() else []
    return hits[-1] if hits else None

def main()->int:
    lines=['DMS-GDK CANONICAL CLEAN - DOCTOR','='*52,f'INFO  : Racine : {ROOT}']; ok=True
    lines.append(f'PASS  : Python {sys.version.split()[0]}')
    for d in ['GDK','RUNTIME','TOOLCHAIN','TOOLS','PROJECTS','SAMPLES','TEMPLATES','ADMIN','DOCS_REPORTS']:
        good=(ROOT/d).is_dir(); ok &= good; lines.append(('PASS  : ' if good else 'ECHEC : ')+f'dossier {d}')
    bats=list(ROOT.glob('*.bat')); good=len(bats)==2; ok &= good; lines.append(('PASS  : ' if good else 'ECHEC : ')+f'racine propre : {len(bats)} lanceurs .bat')

    lines += ['','[TOOLCHAIN 68000 LOCALE]']; tc=ROOT/'TOOLCHAIN/m68k-elf'; bindir=tc/'bin'
    req=['bin/m68k-elf-gcc.exe','bin/m68k-elf-as.exe','bin/m68k-elf-ld.exe','bin/m68k-elf-objcopy.exe','bin/m68k-elf-objdump.exe','libexec/gcc/m68k-elf/14.2.0/cc1.exe','lib/gcc/m68k-elf/14.2.0/libgcc.a']
    for rel in req:
        good=(tc/rel).is_file(); ok &= good; lines.append(('PASS  : ' if good else 'ECHEC : ')+f'TOOLCHAIN/m68k-elf/{rel}')
    # The shipped GCC may request unprefixed as/ld; DMS no longer depends on those aliases.
    lines.append('INFO  : pipeline DMS robuste : GCC -S -> m68k-elf-as -> m68k-elf-ld')

    gcc=_exe(bindir,'m68k-elf-gcc'); assembler=_exe(bindir,'m68k-elf-as'); linker=_exe(bindir,'m68k-elf-ld'); libgcc=_libgcc(tc)
    if os.name=='nt' and gcc.is_file() and assembler.is_file() and linker.is_file() and libgcc:
        try:
            env=os.environ.copy(); old=env.get('PATH',''); env['PATH']=str(bindir)+(os.pathsep+old if old else ''); env['PREFIX']='m68k-elf-'
            cp=subprocess.run([str(gcc),'--version'],capture_output=True,text=True,timeout=15,env=env)
            good=cp.returncode==0; ok &= good; lines.append(('PASS  : ' if good else 'ECHEC : ')+'demarrage m68k-elf-gcc.exe')
            with tempfile.TemporaryDirectory(prefix='dms68k_doctor_') as td:
                td=Path(td); src=td/'probe.c'; asm=td/'probe.s'; obj=td/'probe.o'; start=td/'startup.o'; elf=td/'probe.elf'
                src.write_text('int main(void){return 0;}\n',encoding='ascii')
                steps=[
                    ('GCC -> assembleur texte',[str(gcc),'-m68000','-Os','-ffreestanding','-fno-builtin','-S',str(src),'-o',str(asm)],asm),
                    ('m68k-elf-as -> objet C',[str(assembler),'-m68000',str(asm),'-o',str(obj)],obj),
                    ('m68k-elf-as -> startup',[str(assembler),'-m68000',str(ROOT/'GDK/crt0/startup.s'),'-o',str(start)],start),
                    ('m68k-elf-ld -> ELF',[str(linker),'-T',str(ROOT/'GDK/linker/dms1.ld'),'--gc-sections','-o',str(elf),str(start),str(obj),str(libgcc)],elf),
                ]
                for label,cmd,out in steps:
                    cp=subprocess.run(cmd,capture_output=True,text=True,timeout=45,env=env)
                    good=cp.returncode==0 and out.is_file() and out.stat().st_size>0; ok &= good
                    lines.append(('PASS  : ' if good else 'ECHEC : ')+label)
                    if not good:
                        detail=((cp.stdout or '')+(cp.stderr or '')).strip().replace('\r','')
                        if detail: lines.append('DETAIL: '+detail[:1500].replace('\n',' | '))
                        break
        except Exception as exc:
            ok=False; lines.append('ECHEC : smoke toolchain 68000 : '+str(exc))
    else:
        lines.append('INFO  : smoke test executable 68000 reserve a Windows')

    lines += ['','[RUNTIME WINDOWS PRECOMPILE]']; rb=ROOT/'RUNTIME/build'; runt=['dms1_m68k.dll','dms1_native_host.exe','dms1_rt_audio.exe','dms1_vdp_render.dll','dms1emu.exe','libwinpthread-1.dll']
    for name in runt:
        p=rb/name; good=p.is_file() and p.stat().st_size>0; ok &= good; lines.append(('PASS  : ' if good else 'ECHEC : ')+f'{name} ({p.stat().st_size if p.is_file() else 0} octets)')
    if os.name == 'nt' and (rb/'dms1_m68k.dll').is_file():
        dll_dir_handle=None
        try:
            if hasattr(os, 'add_dll_directory'):
                try: dll_dir_handle=os.add_dll_directory(str(rb.resolve()))
                except OSError: pass
            L=ctypes.CDLL(str((rb/'dms1_m68k.dll').resolve()))
            core_exports=[
                'dms68k_init','dms68k_load_rom','dms68k_reset','dms68k_run',
                'dms68k_set_pad','dms68k_set_vblank','dms68k_set_irq',
                'dms68k_set_mailbox','dms68k_get_mailbox','dms68k_set_ram','dms68k_get_ram',
                'dms68k_set_vram','dms68k_set_cram','dms68k_set_vdp_reg','dms68k_set_mode_rejected',
                'dms68k_event_count','dms68k_event_overflow','dms68k_event_address',
                'dms68k_event_value','dms68k_event_kind','dms68k_events_clear','dms68k_get_pc','dms68k_get_sr',
            ]
            missing_core=[name for name in core_exports if not hasattr(L,name)]
            good=not missing_core; ok &= good
            lines.append(('PASS  : ' if good else 'ECHEC : ') + ('ABI 68000 runtime core compatible' if good else 'ABI 68000 runtime core incomplet : '+', '.join(missing_core)))
            debug_exports=['dms68k_get_d','dms68k_get_a','dms68k_profile_reset','dms68k_profile_total_cycles','dms68k_profile_wait_cycles','dms68k_profile_clear_wait_ranges','dms68k_profile_set_wait_range']
            missing_debug=[name for name in debug_exports if not hasattr(L,name)]
            if missing_debug:
                lines.append('PASS  : DLL 68000 legacy acceptee - jeu complet, debugger/profiling avances en mode compatibilite')
            else:
                lines.append('PASS  : ABI 68000 debug/profiling complet')
        except OSError as exc:
            ok=False; lines.append('ECHEC : chargement ABI dms1_m68k.dll : '+str(exc))
        finally:
            if dll_dir_handle is not None:
                try: dll_dir_handle.close()
                except Exception: pass
    lines += ['','[COMPILATEUR HOTE - OPTIONNEL]','INFO  : MSYS2 ne sert plus au lancement normal des ROM.','','[PROJETS / OUTILS]']
    projects=[p for base in (ROOT/'PROJECTS',ROOT/'SAMPLES') if base.is_dir() for p in base.iterdir() if p.is_dir() and (p/'src/main.c').is_file()]
    lines.append(f'PASS  : {len(projects)} projets GCC detectes')
    scattered=list((ROOT/'PROJECTS').rglob('BUILD_AND_RUN.bat'))+list((ROOT/'SAMPLES').rglob('BUILD_AND_RUN.bat')); good=not scattered; ok &= good; lines.append(('PASS  : ' if good else 'ECHEC : ')+('aucun BUILD_AND_RUN.bat disperse dans PROJECTS/SAMPLES' if good else f'{len(scattered)} BUILD_AND_RUN.bat encore disperses'))
    tools=[]
    for mf in (ROOT/'TOOLS').rglob('dms_tool.json'):
        try:
            d=json.loads(mf.read_text(encoding='utf-8')); launch=mf.parent/str(d.get('launcher',''))
            if d.get('schema')=='dms-tool-v1' and d.get('active',True) and launch.exists(): tools.append(mf)
        except Exception: pass
    lines.append(f'PASS  : {len(tools)} lanceurs outils actifs')
    lines += ['',f'INFO  : Rapport : {REPORT}',('RESULTAT : PASS - base canonique structurellement saine.' if ok else 'RESULTAT : ECHEC - voir les lignes ECHEC ci-dessus.')]
    REPORT.parent.mkdir(parents=True,exist_ok=True); REPORT.write_text('\n'.join(lines)+'\n',encoding='utf-8'); print('\n'.join(lines)); return 0 if ok else 2
if __name__=='__main__': raise SystemExit(main())
