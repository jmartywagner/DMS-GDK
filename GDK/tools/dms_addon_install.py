#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, shutil, tempfile, zipfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
REPORT = ROOT / 'DOCS_REPORTS/current/ADDON_LAST_INSTALL.txt'
SCHEMA = 'dms-addon-v1'


class AddonError(RuntimeError):
    pass


def safe_rel(text):
    p = Path(str(text).replace('\\', '/'))
    if p.is_absolute() or '..' in p.parts or not p.parts:
        raise AddonError(f'chemin add-on interdit : {text}')
    return p


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('zip', type=Path)
    a = ap.parse_args()
    zpath = a.zip.resolve()
    lines = ['DMS ADD-ON INSTALLER', f'Archive : {zpath}']
    try:
        if not zpath.is_file() or not zipfile.is_zipfile(zpath):
            raise AddonError('ZIP add-on invalide')
        with zipfile.ZipFile(zpath) as z:
            try:
                m = json.loads(z.read('dms_addon_manifest.json').decode('utf-8'))
            except Exception as exc:
                raise AddonError(f'dms_addon_manifest.json absent/invalide : {exc}')
            if m.get('schema') != SCHEMA:
                raise AddonError('schema add-on non supporte')
            aid = str(m.get('id') or '').strip()
            ver = str(m.get('version') or '').strip()
            files = m.get('files') or []
            if not aid or not ver or not isinstance(files, list) or not files:
                raise AddonError('id/version/files requis')

            entries = []
            listed = set()
            for row in files:
                if not isinstance(row, dict):
                    raise AddonError('entree manifeste invalide')
                rel = safe_rel(row.get('path', ''))
                action = str(row.get('action', 'add')).lower()
                if rel in listed:
                    raise AddonError(f'chemin duplique dans le manifeste : {rel}')
                listed.add(rel)
                entries.append((rel, action, row))

            arc = {safe_rel(n) for n in z.namelist() if not n.endswith('/') and n != 'dms_addon_manifest.json'}
            if arc != listed:
                raise AddonError('manifeste/fichiers incoherents')

            # Validate the payload and the exact local base before touching the GDK.
            for rel, action, row in entries:
                dest = ROOT / rel
                if action not in {'add', 'replace'}:
                    raise AddonError(f'action inconnue {action} pour {rel}')
                if action == 'add' and dest.exists():
                    raise AddonError(f'REFUS : {rel} existe deja ; aucun ecrasement silencieux')
                if action == 'replace' and not dest.exists():
                    raise AddonError(f'REFUS : cible a remplacer absente : {rel}')

                expected_new = str(row.get('sha256') or '').strip().lower()
                if expected_new:
                    actual_new = sha256_bytes(z.read(rel.as_posix()))
                    if actual_new != expected_new:
                        raise AddonError(f'ZIP corrompu : SHA-256 invalide pour {rel}')

                expected_old = str(row.get('previous_sha256') or '').strip().lower()
                if action == 'replace' and expected_old:
                    actual_old = sha256_file(dest)
                    if actual_old != expected_old:
                        raise AddonError(
                            f'REFUS : {rel} ne correspond pas a la base attendue. '
                            'Le fichier local a probablement deja ete modifie par un autre add-on.'
                        )

            backup = ROOT / 'ARCHIVE/ADDON_BACKUPS' / f'{aid}_{ver}_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
            replaced = 0
            installed = 0
            committed = []
            with tempfile.TemporaryDirectory(prefix='dms_addon_') as td:
                tmp = Path(td)
                z.extractall(tmp)

                # Backup every replacement before the first write.
                for rel, action, _row in entries:
                    if action == 'replace':
                        src_old = ROOT / rel
                        b = backup / rel
                        b.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_old, b)
                        replaced += 1

                try:
                    for rel, action, _row in entries:
                        src = tmp / rel
                        dest = ROOT / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        stage = dest.with_name(dest.name + '.dms_addon_tmp')
                        try:
                            shutil.copy2(src, stage)
                            os.replace(stage, dest)
                        finally:
                            try:
                                stage.unlink(missing_ok=True)
                            except Exception:
                                pass
                        committed.append((rel, action))
                        installed += 1
                except Exception:
                    # Roll back everything already committed. Added files are removed;
                    # replaced files are restored from the backup made above.
                    for rel, action in reversed(committed):
                        dest = ROOT / rel
                        if action == 'add':
                            try:
                                dest.unlink(missing_ok=True)
                            except Exception:
                                pass
                        else:
                            old = backup / rel
                            if old.exists():
                                dest.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(old, dest)
                    raise

            lines += [
                f'PASS : {aid} {ver}',
                f'Fichiers installes : {installed}',
                f'Remplacements sauvegardes : {replaced}',
            ]
            if replaced:
                lines.append(f'Backup : {backup}')
    except Exception as exc:
        lines.append('ECHEC : ' + str(exc))
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        print('\n'.join(lines))
        return 2
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
