"""Refresh hashes after intentional public-source changes (not a trust check)."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def refresh(path, base):
    value = json.loads(path.read_text(encoding='utf-8'))
    for entry in value['files']:
        target = (base / entry['path']).resolve()
        if not target.is_relative_to(base.resolve()) or not target.is_file() or target.is_symlink():
            raise ValueError('Missing/invalid listed file: ' + entry['path'])
        entry['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
        entry['bytes'] = target.stat().st_size
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

if __name__ == '__main__':
    skill = ROOT / 'skills' / 'timing-studio'
    refresh(skill / 'install-manifest.json', skill)
    refresh(ROOT / 'package-manifest.json', ROOT)
    print('Refreshed existing file lists. New files must first be added to the manifests.')
