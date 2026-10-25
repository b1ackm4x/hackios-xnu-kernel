#!/usr/bin/env python3
"""Fetch and verify immutable public runtime inputs."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    lock = json.loads((ROOT / 'tools/ci/runtime-inputs.json').read_text())
    output = ROOT / '.cache/lemonade-runtime'
    output.mkdir(parents=True, exist_ok=True)
    for name, expected in lock['assets'].items():
        path = output / name
        if not path.exists():
            url = f"https://github.com/{lock['repository']}/releases/download/{lock['tag']}/{name}"
            temporary = path.with_suffix('.partial')
            subprocess.run(['curl', '--fail', '--location', '--retry', '3', '--silent', '--show-error', '-o', str(temporary), url], check=True)
            temporary.replace(path)
        if path.stat().st_size != expected['bytes'] or digest(path) != expected['sha256']:
            raise SystemExit('Runtime input checksum mismatch: ' + name)
    with tarfile.open(output / 'runtime-inputs.tar.gz') as tar:
        for entry in tar.getmembers():
            if (entry.name.startswith('/') or '..' in Path(entry.name).parts or
                    not (entry.isfile() or entry.isdir())):
                raise SystemExit('Invalid input archive entry')
        tar.extractall(output, filter='data')
    with (output / 'system.apfs').open('wb') as target:
        for name in sorted(n for n in lock['assets'] if n.startswith('system.apfs.part-')):
            with (output / name).open('rb') as source:
                shutil.copyfileobj(source, target)
    if digest(output / 'system.apfs') != lock['system_sha256']:
        raise SystemExit('Assembled system image checksum mismatch')
    for name in lock['assets']:
        if name.startswith('system.apfs.part-'):
            (output / name).unlink()
    receipt = json.loads((output / 'runtime-inputs/inputs.json').read_text())
    for name, expected in receipt['files'].items():
        if digest(output / 'runtime-inputs' / name) != expected['sha256']:
            raise SystemExit('Template checksum mismatch: ' + name)
    print('Verified clean system, root template, firmware and generic UEFI inputs')


if __name__ == '__main__':
    main()
