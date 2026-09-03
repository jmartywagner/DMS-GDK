from __future__ import annotations
import importlib.util, tempfile
from pathlib import Path

SRC=Path(__file__).resolve().parents[1]/'tools'/'dms_full68000_locator.py'
spec=importlib.util.spec_from_file_location('locator',SRC)
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
with tempfile.TemporaryDirectory() as td:
    base=Path(td)
    current=base/'DMS_GDK_P1_2_5_GCC_CORE'; sibling=base/'DMS_GDK_P1_2_4_GCC_CORE'
    current.mkdir(); (sibling/'RUNTIME'/'build').mkdir(parents=True)
    src=sibling/'RUNTIME'/'build'/'dms1_m68k.dll'; src.write_bytes(b'X'*100000)
    mod.ROOT=current
    found=mod.locate(copy_local=True)
    expected=current/'RUNTIME'/'build'/'dms1_m68k.dll'
    assert found==expected and expected.exists() and expected.stat().st_size==100000
print('PASS: sibling 68000 core discovery/copy contract')
