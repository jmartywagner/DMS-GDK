#!/usr/bin/env python3
"""Build ONLY dms1_native_host.exe for DMS Display Profiles V1.1.

No package install, no DLL download, no audio/68000/VDP rebuild.  The canonical
GDK report records C:\\msys64\\mingw64\\bin as the previously working host
compiler, so that compiler is preferred and UCRT64 is intentionally not probed.
"""
from __future__ import annotations
import ctypes, os, shutil, subprocess, sys, time
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
GDK_ROOT=ROOT.parent
BUILD=ROOT/'build'
OUT=BUILD/'dms1_native_host.exe'
TMP=BUILD/'dms1_native_host.display_v11.new.exe'
SOURCES=[ROOT/'frontends/runtime/dms1_native_host.cpp', ROOT/'frontends/runtime/dms1_vdp_render.cpp']


def quiet_windows_errors():
    if os.name == 'nt':
        try:
            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
        except Exception:
            pass


def compiler_env(cxx: Path) -> dict[str, str]:
    """Expose the already-installed MSYS2 runtime DLLs to GCC helper tools.

    Calling g++.exe by absolute path is not enough on Windows: cc1plus/as/ld
    inherit PATH and need mingw64/bin (plus msys64/usr/bin) there.
    Nothing is copied into the GDK.
    """
    env=os.environ.copy()
    dirs=[cxx.parent]
    try:
        msys_root=cxx.parent.parent.parent
        usr_bin=msys_root/'usr'/'bin'
        if usr_bin.is_dir(): dirs.append(usr_bin)
    except Exception:
        pass
    existing=env.get('PATH','')
    env['PATH']=os.pathsep.join([str(x) for x in dirs]+([existing] if existing else []))
    return env


def candidates():
    out=[]
    explicit=os.environ.get('DMS1_CXX','').strip()
    if explicit: out.append(Path(explicit))
    # This exact family is documented as working in this canonical GDK.
    out.append(Path(r'C:\msys64\mingw64\bin\g++.exe'))
    found=shutil.which('g++.exe') or shutil.which('g++')
    if found: out.append(Path(found))
    uniq=[]; seen=set()
    for p in out:
        k=str(p).lower()
        if k not in seen and p.is_file(): seen.add(k); uniq.append(p)
    return uniq


def compiler_looks_complete(cxx:Path)->bool:
    # Avoid launching an obviously incomplete MinGW tree and triggering Windows
    # "DLL missing" dialog storms.  These are the core companions used by the
    # canonical host compiler family.
    if 'msys64' not in str(cxx).lower(): return True
    d=cxx.parent
    required=('libgcc_s_seh-1.dll','libstdc++-6.dll','libwinpthread-1.dll')
    missing=[n for n in required if not (d/n).is_file()]
    if missing:
        print('[display-host] SKIP incomplete compiler:', cxx)
        print('[display-host] Missing beside compiler:', ', '.join(missing))
        return False
    return True


def works(cxx:Path)->bool:
    if not compiler_looks_complete(cxx): return False
    try:
        r=subprocess.run([str(cxx),'--version'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, timeout=8, env=compiler_env(cxx),
                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if r.returncode==0:
            print('[display-host] compiler:', cxx)
            return True
    except Exception as e:
        print('[display-host] compiler unusable:', cxx, '-', e)
    return False


def assert_portable(exe:Path):
    data=exe.read_bytes().lower()
    forbidden=(b'libgcc_s_',b'libstdc++-6.dll',b'libwinpthread-1.dll')
    found=[x.decode('ascii','ignore') for x in forbidden if x in data]
    if found:
        raise RuntimeError('host not fully static; unwanted runtime imports: '+', '.join(found))


def smoke(exe:Path):
    p=subprocess.Popen([str(exe)], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        deadline=time.time()+3.0
        while time.time()<deadline:
            if p.poll() is not None:
                err=(p.stderr.read(2000) if p.stderr else b'').decode(errors='replace')
                raise RuntimeError(f'new host stopped immediately ({p.returncode}): {err}')
            # Host is intentionally long-lived. Surviving startup proves Windows
            # resolved its imports; READY itself is covered by normal runtime use.
            time.sleep(.10)
            if time.time()+2.2 >= deadline: break
    finally:
        if p.poll() is None:
            p.terminate()
            try: p.wait(timeout=2)
            except Exception: p.kill()


def main():
    if os.name!='nt':
        print('[display-host] Windows-only build skipped')
        return 0
    quiet_windows_errors()
    cs=[c for c in candidates() if works(c)]
    if not cs:
        print('[display-host] STOP: aucun compilateur hote deja fonctionnel trouve.')
        print('[display-host] Rien ne sera installe et aucun DLL ne sera demande.')
        print('[display-host] Le runtime RAW existant reste utilisable (protocole compatible).')
        return 2
    cxx=cs[0]
    BUILD.mkdir(parents=True,exist_ok=True)
    if TMP.exists(): TMP.unlink()
    cmd=[str(cxx),'-std=c++20','-O3','-DNDEBUG',
         *[str(s.relative_to(ROOT)) for s in SOURCES],
         '-pthread','-static','-static-libgcc','-static-libstdc++','-lgdi32','-luser32','-lwinmm',
         '-o',str(TMP.relative_to(ROOT))]
    print('[display-host] build ONLY:', OUT.name)
    env=compiler_env(cxx)
    print('[display-host] PATH compiler:', cxx.parent)
    r=subprocess.run(cmd,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=env,
                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if r.returncode!=0 or not TMP.is_file():
        print(r.stdout)
        print('[display-host] ECHEC: ancien host conserve, aucun autre runtime touche.')
        return 3
    assert_portable(TMP)
    smoke(TMP)
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    backup=GDK_ROOT/'ARCHIVE'/'ADDON_BACKUPS'/f'DMS_DISPLAY_HOST_PREBUILD_{stamp}'/'RUNTIME'/'build'
    backup.mkdir(parents=True,exist_ok=True)
    if OUT.is_file(): shutil.copy2(OUT,backup/OUT.name)
    os.replace(TMP,OUT)
    print('[display-host] PASS - host CRT installe:', OUT)
    print('[display-host] PASS - liaison statique: aucune DLL MinGW requise par le host.')
    print('[display-host] Backup:', backup)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
