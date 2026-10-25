#!/usr/bin/env python3
"""Install pinned build-only dependencies; never fetch phone or account data."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def main():
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        raise SystemExit('This build environment requires ARM64 Linux')
    theos = Path(os.environ['THEOS']).resolve()
    if theos.exists():
        raise SystemExit('Use a fresh THEOS directory; refusing to mix toolchain versions')
    cache = Path(os.environ.get('LEMONADE_DOWNLOAD_CACHE', ROOT / '.cache/lemonade-downloads'))
    cache.mkdir(parents=True, exist_ok=True)
    dependencies = json.loads((ROOT / 'tools/ci/dependencies.json').read_text())
    for name, record in dependencies.items():
        archive = cache / name
        if not archive.exists():
            temporary = archive.with_suffix('.partial')
            subprocess.run(['curl', '--fail', '--location', '--retry', '3', '--silent', '--show-error',
                            '--output', str(temporary), record['url']], check=True)
            temporary.replace(archive)
        with archive.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != record['sha256']:
            raise SystemExit('Dependency checksum mismatch: ' + name)
    for relative in ('toolchain', 'sdks'):
        (theos / relative).mkdir(parents=True)
    subprocess.run(['tar', '-xJf', str(cache / 'iOSToolchain-aarch64.tar.xz'),
                    '-C', str(theos / 'toolchain')], check=True)
    subprocess.run(['tar', '-xJf', str(cache / 'iPhoneOS16.5.sdk.tar.xz'),
                    '-C', str(theos / 'sdks')], check=True)
    subprocess.run(['tar', '-xJf', str(cache / 'MacOSX13.3.sdk.tar.xz'),
                    '-C', str(ROOT / 'xnu-deps'), '--wildcards',
                    '*MacOSX13.3.sdk/System/Library/Frameworks/Kernel.framework*'], check=True)
    print('Verified and installed ARM64 toolchain and SDK build inputs')


if __name__ == '__main__':
    main()
