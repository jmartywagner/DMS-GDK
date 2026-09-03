#!/usr/bin/env python3
"""Build P1.0.9 Windows runtimes with an installed MinGW/MSYS2 compiler.

Builds:
- dms1emu.exe: deterministic DMR renderer/player engine
- dms1_rt_audio.exe: real-time DMS-1 audio bridge (waveOut)
- dms1_vdp_render.dll: reference native VDP raster accelerator
- dms1_native_host.exe: realtime-isolated Win32 video/input/HUD host (no Tk/Tcl)

The DMS-1 hardware semantics remain in the existing CPU/VDP/audio model. P1.0.9
changes only the PC-side realtime host/transport architecture.
"""
from __future__ import annotations
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMU_OUT = ROOT / "build" / "dms1emu.exe"
AUDIO_OUT = ROOT / "build" / "dms1_rt_audio.exe"
VDP_OUT = ROOT / "build" / "dms1_vdp_render.dll"
HOST_OUT = ROOT / "build" / "dms1_native_host.exe"
M68K_OUT = ROOT / "build" / "dms1_m68k.dll"
MUSASHI_DIR = next((ROOT / "third_party" / "musashi").glob("Musashi-*"), None)
M68K_COMPILE_SOURCES = ([
    ROOT / "native_cpu" / "dms1_m68k_bridge.c",
    MUSASHI_DIR / "m68kcpu.c",
    MUSASHI_DIR / "m68kops.c",
    MUSASHI_DIR / "softfloat" / "softfloat.c",
] if MUSASHI_DIR else [])
M68K_DEPENDS = M68K_COMPILE_SOURCES + ([MUSASHI_DIR / "m68kconf.h", MUSASHI_DIR / "m68k.h"] if MUSASHI_DIR else [])
EMU_SOURCES = [
    ROOT / "src" / "dms1emu.cpp",
    ROOT / "src" / "dms1_core.cpp",
    ROOT / "src" / "dms1_output_stage.cpp",
    ROOT / "third_party" / "ymfm" / "src" / "ymfm_adpcm.cpp",
    ROOT / "third_party" / "ymfm" / "src" / "ymfm_misc.cpp",
    ROOT / "third_party" / "ymfm" / "src" / "ymfm_opz.cpp",
    ROOT / "third_party" / "ymfm" / "src" / "ymfm_ssg.cpp",
]
AUDIO_SOURCES = [
    ROOT / "frontends" / "runtime" / "dms1_rt_audio.cpp",
    ROOT / "src" / "dms1_core.cpp",
    ROOT / "src" / "dms1_output_stage.cpp",
    ROOT / "third_party" / "ymfm" / "src" / "ymfm_adpcm.cpp",
    ROOT / "third_party" / "ymfm" / "src" / "ymfm_misc.cpp",
    ROOT / "third_party" / "ymfm" / "src" / "ymfm_opz.cpp",
    ROOT / "third_party" / "ymfm" / "src" / "ymfm_ssg.cpp",
]
VDP_SOURCES = [ROOT / "frontends" / "runtime" / "dms1_vdp_render.cpp"]
HOST_SOURCES = [
    ROOT / "frontends" / "runtime" / "dms1_native_host.cpp",
    ROOT / "frontends" / "runtime" / "dms1_vdp_render.cpp",
]


def compiler_candidates() -> list[Path]:
    candidates: list[Path] = []
    for env_name in ("DMS1_CXX", "CXX"):
        if os.environ.get(env_name):
            candidates.append(Path(os.environ[env_name]))
    for path in (
        r"C:\msys64\ucrt64\bin\g++.exe",
        r"C:\msys64\mingw64\bin\g++.exe",
        r"C:\msys64\clang64\bin\clang++.exe",
        r"C:\ucrt64\bin\g++.exe",
    ):
        candidates.append(Path(path))
    # Keep the Windows runtime deterministic: GCC/MinGW only.  Clang64 uses a
    # different libc++ DLL family and made portable debugger builds fragile.
    for name in ("g++.exe", "g++"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    unique=[]; seen=set()
    for c in candidates:
        key=str(c).lower()
        if key not in seen and c.exists():
            seen.add(key); unique.append(c)
    return unique


def needs_build(out: Path, sources: list[Path]) -> bool:
    if not out.exists(): return True
    stamp=out.stat().st_mtime
    return any(p.stat().st_mtime > stamp for p in sources)


def run_build(cxx: Path, out: Path, cmd_tail: list[str], label: str) -> tuple[bool,str]:
    cmd=[str(cxx), *cmd_tail, "-o", str(out.relative_to(ROOT))]
    result=subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode == 0 and out.exists():
        print(f"[runtime] {label} built: {out}")
        return True, ""
    return False, result.stdout


def build_with_candidates(compilers: list[Path], out: Path, sources: list[Path], tail_factory, label: str) -> tuple[bool,str]:
    if not needs_build(out, sources):
        print(f"[runtime] {label} OK: {out.name} already current")
        return True, ""
    logs=[]
    for cxx in compilers:
        print(f"[runtime] {label} compiler:", cxx)
        ok,log=run_build(cxx,out,tail_factory(cxx),label)
        if ok:
            return True, ""
        logs.append(f"--- {cxx} ---\n{log}")
    return False, "\n".join(logs)


RUNTIME_DLL_NAMES = (
    "libgcc_s_seh-1.dll", "libgcc_s_sjlj-1.dll", "libgcc_s_dw2-1.dll",
    "libstdc++-6.dll", "libwinpthread-1.dll", "libssp-0.dll",
    "libquadmath-0.dll", "libgomp-1.dll", "zlib1.dll",
)

def copy_compiler_runtime_dlls(cxx: Path) -> None:
    """Copy the exact MinGW runtime DLLs next to our binaries as a safety net.

    Normal V0.1 builds are linked statically.  This copy exists for MinGW
    variants that still leave one runtime import in a DLL.
    """
    src = cxx.parent
    destinations = [ROOT / "build"]
    audio_lab = ROOT.parent / "TOOLS" / "AUDIO" / "DMS1_AUDIO_LAB" / "build"
    if audio_lab.exists():
        destinations.append(audio_lab)
    copied = []
    for name in RUNTIME_DLL_NAMES:
        candidate = src / name
        if not candidate.is_file():
            continue
        for dst in destinations:
            dst.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(candidate, dst / name)
            except OSError:
                pass
        copied.append(name)
    if copied:
        print("[runtime] compiler DLL safety net:", ", ".join(copied))

def main() -> int:
    if os.name != "nt":
        print("[runtime] Windows build skipped on non-Windows host")
        return 0
    compilers=compiler_candidates()
    if not compilers:
        print("[runtime] ERROR: MinGW/MSYS2 C++ compiler not found.", file=sys.stderr)
        return 2
    # Ignore installations whose g++.exe exists but cannot start because its own
    # MSYS2/MinGW runtime is broken.  Then lock the whole DMS runtime build to
    # one compiler family so binaries and fallback DLLs always match.
    working=[]
    for cxx in compilers:
        try:
            probe=subprocess.run([str(cxx), "--version"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, timeout=5)
            if probe.returncode == 0:
                working.append(cxx)
        except (OSError, subprocess.SubprocessError):
            pass
    if not working:
        print("[runtime] ERROR: g++.exe found but none can start. Repair/install MSYS2 UCRT64.", file=sys.stderr)
        return 2
    compilers=[working[0]]
    print("[runtime] locked compiler:", compilers[0])
    AUDIO_OUT.parent.mkdir(parents=True, exist_ok=True)
    failures=[]

    ok,log=build_with_candidates(compilers,EMU_OUT,EMU_SOURCES,lambda _cxx:[
        "-std=c++20","-O3","-DNDEBUG","-Isrc","-Ithird_party/ymfm/src",
        *[str(p.relative_to(ROOT)) for p in EMU_SOURCES],
        "-static","-static-libgcc","-static-libstdc++",
    ],"dmr-engine")
    if not ok: failures.append(("dmr-engine",log))

    if not MUSASHI_DIR or not all(p.exists() for p in M68K_COMPILE_SOURCES):
        failures.append(("m68k", "Musashi pinned sources incomplete; run INSTALL_FULL_68000_CORE.bat"))
    else:
        ok,log=build_with_candidates(compilers,M68K_OUT,M68K_DEPENDS,lambda _cxx:[
            "-x","c","-std=c99","-O3","-DNDEBUG","-shared","-static",
            "-static-libgcc","-static-libstdc++",
            "-I"+str(MUSASHI_DIR),
            *[str(p.relative_to(ROOT)) for p in M68K_COMPILE_SOURCES],
            "-lm",
        ],"m68k-debug")
        if not ok: failures.append(("m68k",log))

    ok,log=build_with_candidates(compilers,AUDIO_OUT,AUDIO_SOURCES,lambda _cxx:[
        "-std=c++20","-O3","-DNDEBUG","-Isrc","-Ithird_party/ymfm/src",
        *[str(p.relative_to(ROOT)) for p in AUDIO_SOURCES],
        "-pthread","-static","-static-libgcc","-static-libstdc++","-lwinmm",
    ],"audio")
    if not ok: failures.append(("audio",log))

    ok,log=build_with_candidates(compilers,VDP_OUT,VDP_SOURCES,lambda _cxx:[
        "-std=c++20","-O3","-DNDEBUG","-shared","-static",
        *[str(p.relative_to(ROOT)) for p in VDP_SOURCES],
        "-static-libgcc","-static-libstdc++","-lgdi32","-luser32",
    ],"VDP")
    if not ok: failures.append(("VDP",log))

    ok,log=build_with_candidates(compilers,HOST_OUT,HOST_SOURCES,lambda _cxx:[
        "-std=c++20","-O3","-DNDEBUG",
        *[str(p.relative_to(ROOT)) for p in HOST_SOURCES],
        "-pthread","-static","-static-libgcc","-static-libstdc++","-lgdi32","-luser32","-lwinmm",
    ],"native-host")
    if not ok: failures.append(("native-host",log))

    if failures:
        for label,log in failures:
            print(f"[runtime] ERROR building {label}:\n{log}", file=sys.stderr)
        return 3
    # Use the same compiler family that successfully built the runtime.
    # Copying its DLLs is harmless for static builds and fixes older MinGW
    # variants that keep one dynamic dependency.
    copy_compiler_runtime_dlls(compilers[0])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
