#!/usr/bin/env python3
from __future__ import annotations
import argparse,shutil,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def main():
 ap=argparse.ArgumentParser();ap.add_argument('name');a=ap.parse_args();safe=re.sub(r'[^A-Za-z0-9_-]+','_',a.name).strip('_') or 'MY_GAME';dest=ROOT/'PROJECTS'/safe
 if dest.exists():raise SystemExit(f'ERREUR: {dest} existe déjà')
 src=ROOT/'TEMPLATES'/'STARTER_GAME';shutil.copytree(src,dest)
 print(f'Projet créé : {dest}');print(f'Projet pret : {dest}')
if __name__=='__main__':main()
