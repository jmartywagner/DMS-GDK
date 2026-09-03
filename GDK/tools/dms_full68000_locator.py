#!/usr/bin/env python3
from __future__ import annotations
import argparse, os, shutil, subprocess
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
DLL_REL = Path('RUNTIME') / 'build' / 'dms1_m68k.dll'
MIN_DLL_BYTES = 100_000

def _home() -> Path:
    return Path(os.environ.get('USERPROFILE') or Path.home())

def search_bases() -> list[Path]:
    """Emplacements DMS frequents. La liste reste bornee et lisible dans les diagnostics."""
    home = _home()
    raw = [
        ROOT,
        ROOT.parent,
        ROOT.parent / 'DMS',
        home / 'Desktop',
        home / 'Desktop' / 'DMS',
        home / 'Documents',
        home / 'Documents' / 'DMS',
    ]
    cur = ROOT.parent
    for _ in range(3):
        raw += [cur, cur / 'DMS']
        if cur.parent == cur:
            break
        cur = cur.parent
    out=[]; seen=set()
    for p in raw:
        try: key=p.resolve()
        except Exception: key=p
        if key in seen: continue
        seen.add(key); out.append(p)
    return out

def _candidate_gdk_dirs(base: Path):
    patterns = [
        'DMS_GDK_P1_*',
        'DMS*/DMS_GDK_P1_*',
        '*/DMS_GDK_P1_*',
        '*/*/DMS_GDK_P1_*',
    ]
    for pat in patterns:
        try:
            for p in base.glob(pat):
                if p.is_dir():
                    yield p
        except Exception:
            pass


def _direct_dll_candidates(base: Path):
    """Fallback P1.2.10: cherche le nom exact de la DLL dans quelques niveaux.

    Cela couvre les anciennes installations DMS-GDK meme si leur dossier ne suit pas
    exactement DMS_GDK_P1_x_y_GCC_CORE. On n'explore pas AppData ni tout le disque.
    """
    if not base.exists() or not base.is_dir():
        return
    try:
        base_depth=len(base.resolve().parts)
    except Exception:
        base_depth=len(base.parts)
    skip={'AppData','.git','node_modules','venv','.venv','Windows','$Recycle.Bin'}
    try:
        for dirpath, dirnames, filenames in os.walk(base):
            p=Path(dirpath)
            try: depth=len(p.resolve().parts)-base_depth
            except Exception: depth=len(p.parts)-base_depth
            dirnames[:] = [d for d in dirnames if d not in skip]
            if depth >= 5:
                dirnames[:] = []
            if 'dms1_m68k.dll' in filenames:
                yield p/'dms1_m68k.dll'
    except Exception:
        return


def _valid(dll: Path) -> bool:
    try:
        return dll.is_file() and dll.stat().st_size >= MIN_DLL_BYTES
    except Exception:
        return False


def candidates():
    seen=set(); ordered=[ROOT]
    for base in search_bases():
        ordered.extend(_candidate_gdk_dirs(base))
    head=ordered[:1]; tail=ordered[1:]
    try:
        tail.sort(key=lambda p:p.stat().st_mtime if p.exists() else 0, reverse=True)
    except Exception:
        pass
    for base in head+tail:
        try: key=base.resolve()
        except Exception: key=base
        if key in seen: continue
        seen.add(key)
        dll=base/DLL_REL
        if _valid(dll):
            yield dll

    # Fallback plus tolerant: Desktop et Desktop/DMS seulement, profondeur bornee.
    # On le fait apres les chemins rapides pour ne pas ralentir le cas normal.
    home=_home()
    for base in (home/'Desktop', home/'Desktop'/'DMS', home/'Documents'/'DMS'):
        for dll in _direct_dll_candidates(base) or ():
            try: key=dll.resolve()
            except Exception: key=dll
            if key in seen: continue
            seen.add(key)
            if _valid(dll):
                yield dll


def _local() -> Path:
    return ROOT/DLL_REL


def _copy_local(found: Path) -> Path:
    current=_local()
    try: same=found.resolve()==current.resolve()
    except Exception: same=False
    if not same:
        current.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(found,current)
    if not _valid(current):
        raise OSError('copie du coeur 68000 incomplete')
    return current


def locate(copy_local: bool=False) -> Path:
    current=_local()
    if _valid(current): return current
    raise FileNotFoundError('dms1_m68k.dll locale absente/invalide : '+str(current)+' ; aucune autre version du GDK ne sera utilisee automatiquement')


def repair() -> Path:
    """Reconstruit automatiquement le coeur dans CE GDK si aucune ancienne DLL n'est disponible."""
    ps1=ROOT/'GDK'/'toolchain'/'install_full_68000_core.ps1'
    if not ps1.is_file():
        raise FileNotFoundError('install_full_68000_core.ps1 absent')
    print('INFO : aucun coeur 68000 reutilisable trouve.')
    print('INFO : reconstruction automatique locale du coeur Musashi...')
    cmd=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ps1)]
    try:
        r=subprocess.run(cmd,cwd=str(ROOT))
    except FileNotFoundError:
        cmd[0]='powershell'
        r=subprocess.run(cmd,cwd=str(ROOT))
    if r.returncode != 0:
        raise RuntimeError(f'installation automatique Musashi echouee (code {r.returncode})')
    current=_local()
    if not _valid(current):
        raise RuntimeError('installateur termine mais dms1_m68k.dll locale absente/invalide')
    return current


def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--copy-local',action='store_true')
    ap.add_argument('--repair',action='store_true',help='reconstruit Musashi localement si aucun coeur existant n est trouve')
    ap.add_argument('--quiet',action='store_true')
    a=ap.parse_args()
    try:
        try:
            dll=locate(a.copy_local)
        except FileNotFoundError:
            if not a.repair: raise
            dll=repair()
    except Exception as exc:
        if not a.quiet:
            print('ERREUR : coeur 68000 complet introuvable ou non reparable.')
            print('DETAIL :',exc)
        return 2
    if not a.quiet:
        print('PASS : coeur 68000 disponible :',dll)
        if a.copy_local or a.repair:
            print('PASS : coeur 68000 local pour cette version du GDK.')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
