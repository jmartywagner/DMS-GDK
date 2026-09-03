#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path
HERE=Path(__file__).resolve(); ROOT=HERE.parents[2]
class RunnerError(RuntimeError): pass

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
def run(cmd,cwd):
    cmd=[str(x) for x in cmd]; print('>',' '.join(cmd))
    flags=subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') and hasattr(subprocess,'CREATE_NO_WINDOW') else 0
    r=subprocess.run(cmd,cwd=str(cwd),text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf-8',errors='replace',creationflags=flags)
    if r.stdout: _emit(r.stdout)
    if r.returncode:raise RunnerError(f'commande echouee ({r.returncode})')
def expand(text,project,rom):
    return str(text).replace('{root}',str(ROOT)).replace('{project}',str(project)).replace('{rom}',str(rom))
def run_step(step,project,rom):
    label=step.get('label','Etape'); print(f'[{label}]')
    script=Path(expand(step['script'],project,rom))
    if not script.is_absolute(): script=project/script
    args=[expand(x,project,rom) for x in step.get('args',[])]
    run([sys.executable,script,*args],project)
def project_rom(project): return project/'build'/(project.name+'.dmc')
def build(project):
    project=project.resolve();
    if not (project/'src/main.c').is_file(): raise RunnerError('src/main.c absent : '+str(project))
    cfg={}; mf=project/'dms_project.json'
    if mf.is_file():
        cfg=json.loads(mf.read_text(encoding='utf-8-sig'))
        if cfg.get('schema') not in ('dms-project-pipeline-v1','dms-project-pipeline-v2'):
            raise RunnerError('dms_project.json : schema non supporté (attendu v1 ou v2)')
    rom=project_rom(project)
    for rel in cfg.get('required',[]):
        if not (project/rel).exists(): raise RunnerError('ressource requise absente : '+rel+' ; restaurer le fichier ou corriger required[]')
    for step in cfg.get('prebuild',[]): run_step(step,project,rom)
    run([sys.executable,ROOT/'GDK/tools/dmsgcc_build.py',project],project)
    if not rom.is_file(): raise RunnerError('build termine sans ROM : '+str(rom))
    for step in cfg.get('postbuild',[]): run_step(step,project,rom)
    print('PASS BUILD :',rom); return rom
def validate(project):
    print('[DÉTECTION AUTOMATIQUE]')
    run([sys.executable,ROOT/'GDK/tools/dmsautobuild.py',project,'--validate-only'],project)
    report=project/'build/autogen/dms_autobuild_report.json';manifest=None
    if report.is_file():manifest=json.loads(report.read_text(encoding='utf-8')).get('resource_manifest')
    if not manifest: print('INFO : aucune ressource compilable détectée.'); return
    run([sys.executable,ROOT/'GDK/tools/dmsres.py',manifest,'--validate-only'],project)
def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('project',type=Path); ap.add_argument('action',choices=['validate','build','build-run','run']); a=ap.parse_args(); project=a.project.resolve()
    try:
        if a.action=='validate': validate(project); return 0
        rom=project_rom(project)
        if a.action in ('build','build-run'): rom=build(project)
        if a.action in ('run','build-run'):
            if not rom.is_file(): raise RunnerError('ROM inexistante : '+str(rom))
            run([sys.executable,ROOT/'GDK/tools/dms_run_rom.py',rom],ROOT)
        return 0
    except Exception as exc:
        _emit(f'ERREUR DMS PROJECT : {exc}\n', sys.stderr); return 2
if __name__=='__main__': raise SystemExit(main())
