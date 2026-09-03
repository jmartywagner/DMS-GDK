#!/usr/bin/env python3
from __future__ import annotations
import subprocess, sys
from datetime import datetime
from pathlib import Path
HERE=Path(__file__).resolve(); ROOT=HERE.parents[2]
REPORT=ROOT/'DOCS_REPORTS/current/TEST_ALL_PROJECTS_LAST.txt'

def projects():
    out=[]
    for base_name in ('SAMPLES','PROJECTS'):
        base=ROOT/base_name
        if not base.is_dir(): continue
        for p in sorted(base.iterdir(),key=lambda x:x.name.lower()):
            if p.is_dir() and (p/'src/main.c').is_file(): out.append(p)
    return out

def main()->int:
    ps=projects(); lines=[
        'DMS-GDK - TEST GLOBAL DES PROJETS',
        '='*60,
        f'Date : {datetime.now().isoformat(timespec="seconds")}',
        f'Racine : {ROOT}',
        f'Projets detectes : {len(ps)}',''
    ]
    passed=[]; failed=[]
    runner=ROOT/'GDK/tools/dms_project_runner.py'
    for i,p in enumerate(ps,1):
        rel=p.relative_to(ROOT)
        print(f'\n[{i}/{len(ps)}] BUILD {rel}')
        cp=subprocess.run([sys.executable,str(runner),str(p),'build'],cwd=str(p),capture_output=True,text=True,errors='replace')
        text=((cp.stdout or '')+(cp.stderr or '')).replace('\r','')
        if cp.returncode==0:
            passed.append(str(rel)); print('PASS :',rel)
            lines.append(f'PASS  : {rel}')
        else:
            failed.append(str(rel)); print('ECHEC:',rel)
            lines += [f'ECHEC : {rel}','-'*60,text.strip(),'-'*60]
    lines += ['',f'RESUME : {len(passed)} PASS / {len(failed)} ECHEC / {len(ps)} TOTAL']
    if failed:
        lines += ['','PROJETS EN ECHEC :',*['- '+x for x in failed]]
    else:
        lines += ['','RESULTAT : PASS - tous les projets GCC detectes se construisent.']
    REPORT.parent.mkdir(parents=True,exist_ok=True); REPORT.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('\n'+'='*60); print(f'{len(passed)} PASS / {len(failed)} ECHEC'); print('Rapport :',REPORT)
    return 0 if not failed else 2
if __name__=='__main__': raise SystemExit(main())
