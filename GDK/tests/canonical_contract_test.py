from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
assert sorted(p.name for p in ROOT.glob('*.bat'))==['DMS_GDK.bat','RUN_ROM.bat']
for d in ['GDK','RUNTIME','TOOLS','PROJECTS','SAMPLES','TEMPLATES','ADMIN','DOCS_REPORTS','ARCHIVE']: assert (ROOT/d).is_dir(),d
assert not list((ROOT/'PROJECTS').rglob('BUILD_AND_RUN.bat'))
assert not list((ROOT/'SAMPLES').rglob('BUILD_AND_RUN.bat'))
assert (ROOT/'GDK/tools/dms_project_runner.py').is_file()
assert (ROOT/'GDK/tools/dms_run_rom.py').is_file()
assert (ROOT/'GDK/tools/dms_addon_install.py').is_file()
print('PASS canonical DMS-GDK contract')
