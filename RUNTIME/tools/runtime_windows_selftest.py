#!/usr/bin/env python3
"""DMS-1 Debugger V0.1b Windows runtime dependency self-test."""
from __future__ import annotations
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"


def fail(message: str) -> int:
    print("[runtime-selftest] ERROR:", message, file=sys.stderr)
    return 1


def main() -> int:
    if os.name != "nt":
        print("[runtime-selftest] Windows-only check skipped")
        return 0
    missing = [p.name for p in (
        BUILD / "dms1_m68k.dll",
        BUILD / "dms1_vdp_render.dll",
        BUILD / "dms1_native_host.exe",
        BUILD / "dms1_rt_audio.exe",
    ) if not p.is_file()]
    if missing:
        return fail("missing built files: " + ", ".join(missing))

    dll_handle = None
    if hasattr(os, "add_dll_directory"):
        try:
            dll_handle = os.add_dll_directory(str(BUILD.resolve()))
        except OSError as exc:
            return fail(f"cannot register RUNTIME\\build as DLL directory: {exc}")

    for name in ("dms1_m68k.dll", "dms1_vdp_render.dll"):
        try:
            ctypes.CDLL(str(BUILD / name))
            print(f"[runtime-selftest] LOAD OK: {name}")
        except OSError as exc:
            return fail(f"{name} cannot load: {exc}")

    env = os.environ.copy()
    env["PATH"] = str(BUILD) + os.pathsep + env.get("PATH", "")
    host = None
    try:
        host = subprocess.Popen(
            [str(BUILD / "dms1_native_host.exe")], cwd=ROOT, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(0.60)
        rc = host.poll()
        if rc is not None:
            err = b""
            try:
                err = host.stderr.read(4096) if host.stderr else b""
            except Exception:
                pass
            return fail(f"dms1_native_host.exe stopped immediately (code {rc}): " + err.decode(errors="replace")[:500])
        print("[runtime-selftest] START OK: dms1_native_host.exe")
    except OSError as exc:
        return fail(f"dms1_native_host.exe cannot start: {exc}")
    finally:
        if host is not None and host.poll() is None:
            try:
                host.terminate(); host.wait(timeout=2)
            except Exception:
                try: host.kill()
                except Exception: pass
        # Keep handle alive until all DLL loads / host checks are complete.
        _ = dll_handle

    print("[runtime-selftest] PASS: Windows runtime dependencies resolved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
