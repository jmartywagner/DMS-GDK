#!/usr/bin/env python3
"""Regression test: generated strong runtime tables must not be constant-folded away.

The fallback weak definitions must live in a translation unit separate from the
code that consumes them. With -Os GCC may otherwise replace lookups by the
fallback constant (for example dms_scene_resource_count == 0) before the linker
can override that symbol with generated project resources.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "GDK" / "lib" / "src"
INC = ROOT / "GDK" / "include"

scene = (SRC / "dms_scene.c").read_text(encoding="utf-8")
audio = (SRC / "dms_audio.c").read_text(encoding="utf-8")
flow = (SRC / "dms_flow.c").read_text(encoding="utf-8")
stubs = (SRC / "dms_stubs.c").read_text(encoding="utf-8")

# Source-level contract: fallback constants are NOT defined beside consumers.
assert "__attribute__((weak)) const DmsSceneResourceDesc" not in scene
assert "__attribute__((weak)) const uint16_t dms_scene_resource_count" not in scene
assert "__attribute__((weak)) const DmsSfxResourceDesc" not in audio
assert "__attribute__((weak)) const uint16_t dms_sfx_resource_count" not in audio
assert "__attribute__((weak)) const DmsMusicResourceDesc" not in audio
assert "__attribute__((weak)) const uint16_t dms_music_resource_count" not in audio
assert "__attribute__((weak)) const DmsFlowDefinition dms_flow_definition" not in flow

# They must exist in the dedicated fallback TU instead.
for symbol in (
    "dms_scene_resources",
    "dms_scene_resource_count",
    "dms_sfx_resources",
    "dms_sfx_resource_count",
    "dms_music_resources",
    "dms_music_resource_count",
    "dms_sfx_program",
    "dms_sfx_program_count",
    "dms_flow_definition",
):
    assert symbol in stubs, symbol

# Optimizer-level contract when a host GCC is available. We deliberately use
# -Os because that is the optimization level used by dmsgcc_build.py.
cc = shutil.which("gcc") or shutil.which("cc")
if cc:
    with tempfile.TemporaryDirectory(prefix="dms_weak_override_") as td:
        td = Path(td)
        scene_s = td / "scene.s"
        audio_s = td / "audio.s"
        common = [cc, "-Os", "-ffreestanding", "-fno-builtin", "-std=c11", "-I", str(INC), "-S"]
        subprocess.run(common + [str(SRC / "dms_scene.c"), "-o", str(scene_s)], check=True)
        subprocess.run(common + [str(SRC / "dms_audio.c"), "-o", str(audio_s)], check=True)
        s_scene = scene_s.read_text(encoding="utf-8", errors="replace")
        s_audio = audio_s.read_text(encoding="utf-8", errors="replace")
        assert "dms_scene_resource_count" in s_scene, "SCENE lookup was constant-folded away"
        assert "dms_sfx_resource_count" in s_audio, "SFX lookup was constant-folded away"
        assert "dms_music_resource_count" in s_audio, "MUSIC lookup was constant-folded away"

print("PASS runtime weak override regression: Scene/Music/SFX/Flow generated tables remain linker-overridable under -Os")
