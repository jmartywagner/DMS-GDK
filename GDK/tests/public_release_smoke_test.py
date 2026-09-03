from pathlib import Path
import hashlib, json, re

ROOT = Path(__file__).resolve().parents[2]

for rel in [
    'README.md', 'LICENSE.md', 'PUBLIC_MANIFEST.json', 'VERSION.txt',
    'RUNTIME/build/dms1_rt_audio.exe', 'RUNTIME/build/dms1emu.exe',
    'SAMPLES/07_PLATFORM_DEMO/07_PLATFORM_DEMO_PREBUILT.dmc',
]:
    assert (ROOT / rel).is_file(), rel

for rel in ['GDK', 'RUNTIME', 'TOOLS', 'PROJECTS', 'SAMPLES', 'TEMPLATES', 'ADMIN', 'DOCS', 'DOCS_REPORTS', 'ARCHIVE']:
    assert (ROOT / rel).is_dir(), rel

json.loads((ROOT / 'PUBLIC_MANIFEST.json').read_text(encoding='utf-8'))

stamp = (ROOT / 'RUNTIME/build/dms_audio_core_v080.stamp').read_text(encoding='utf-8')
def expected(key):
    m = re.search(rf'^{key}=([0-9a-f]{{64}})$', stamp, re.M)
    assert m, key
    return m.group(1)
def sha(rel):
    return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
assert sha('RUNTIME/build/dms1_rt_audio.exe') == expected('bridge_sha256')
assert sha('RUNTIME/build/dms1emu.exe') == expected('renderer_sha256')

for p in ROOT.rglob('*'):
    if p.is_file():
        assert '__pycache__' not in p.parts
        assert p.suffix.lower() not in {'.pyc', '.log', '.tmp', '.bak', '.o', '.obj', '.elf', '.lst'}

print('PASS DMS-GDK Public V1 smoke test')
