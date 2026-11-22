#!/usr/bin/env python3
"""Publica una versión de TouchMouse: build → zip → commit → release → store Decky.

Uso: python3 scripts/publish.py "mensaje de changelog"
La versión sale de package.json (subirla antes de correr este script).
Requiere: npm, gh autenticado, git con remote origin.
"""

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO)
NAME = 'TouchMouse'
ZIP = 'decky-touchmouse.zip'
LEDGER = 'scripts/versions.json'
ARTIFACT = 'https://github.com/ax-zwk/decky-touchmouse/releases/download/{tag}/decky-touchmouse.zip'


def run(*cmd, capture=False):
    r = subprocess.run(cmd, check=True, capture_output=capture, text=True)
    return r.stdout if capture else None


def main():
    notes = sys.argv[1] if len(sys.argv) > 1 else 'correcciones'
    ver = json.load(open('package.json'))['version']
    tag = f'v{ver}'

    # build + zip
    run('npm', 'run', 'build')
    if os.path.exists(ZIP):
        os.remove(ZIP)
    with zipfile.ZipFile(ZIP, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in ('plugin.json', 'main.py', 'package.json', 'README.md',
                  'UPDATES.md', 'LICENSE'):
            z.write(f, f'decky-touchmouse/{f}')
        z.write('dist/index.js', 'decky-touchmouse/dist/index.js')
        z.write('py_modules/touchmouse.py', 'decky-touchmouse/py_modules/touchmouse.py')
    sha = hashlib.sha256(open(ZIP, 'rb').read()).hexdigest()

    # tests antes de publicar
    run(sys.executable, 'py_modules/touchmouse.py', '--self-test')
    run(sys.executable, 'test_backend.py')

    # ledger de versiones (hash por release, sin re-descargar assets viejos)
    ledger = json.load(open(LEDGER)) if os.path.exists(LEDGER) else {}
    ledger[ver] = {'hash': sha, 'created': datetime.now(timezone.utc).isoformat()}
    os.makedirs('scripts', exist_ok=True)
    json.dump(ledger, open(LEDGER, 'w'), indent=2)

    # store estático para Decky: lista (index.html servido en /store) + /store/versions
    versions = sorted(ledger.items(), key=lambda kv: tuple(map(int, kv[0].split('.'))), reverse=True)
    store = [{
        'name': NAME,
        'author': 'ax-zwk',
        'description': 'Touchscreen como mouse de solo movimiento (nunca clicks). Sin daemon ni InputPlumber.',
        'tags': ['utility', 'root'],
        'visible': True,
        'versions': [
            {'name': v, 'hash': d['hash'], 'created': d['created'],
             'artifact': ARTIFACT.format(tag=f'v{v}')}
            for v, d in versions
        ],
    }]
    latest_v, latest_d = versions[0]
    os.makedirs('docs/store', exist_ok=True)
    json.dump(store, open('docs/store/index.html', 'w'), indent=1)
    json.dump({NAME: [latest_v, latest_d['hash'], ARTIFACT.format(tag=f'v{latest_v}')]},
              open('docs/store/versions', 'w'))

    # commit + push + release
    run('git', 'add', '-A')
    run('git', 'commit', '--allow-empty', '-m', f'release {tag}: {notes}')
    run('git', 'push')
    run('gh', 'release', 'create', tag, ZIP, '--title', tag, '--notes', notes)
    print(f'publicado {tag} sha256={sha}')
    print('store: https://ax-zwk.github.io/decky-touchmouse/store')


if __name__ == '__main__':
    main()
