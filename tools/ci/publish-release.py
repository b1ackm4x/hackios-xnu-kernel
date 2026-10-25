#!/usr/bin/env python3
"""Publish verified complete assets; safely resume an interrupted draft upload."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def gh(*args):
    return subprocess.check_output(['gh', *args], text=True)


def verify_remote(release, checksums):
    expected = {line.split()[1]: line.split()[0] for line in checksums.splitlines() if line.strip()}
    expected['SHA256SUMS'] = hashlib.sha256(checksums.encode()).hexdigest()
    assets = {entry['name']: entry for entry in release['assets']}
    if set(assets) != set(expected):
        raise SystemExit('Release asset inventory is incomplete or unexpected')
    for name, digest in expected.items():
        asset = assets[name]
        if asset['state'] != 'uploaded' or asset['size'] <= 0 or asset.get('digest') != 'sha256:' + digest:
            raise SystemExit('GitHub asset verification failed: ' + name)


def main():
    commit = os.environ['COMMIT']
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise SystemExit('Expected full source commit SHA')
    output = ROOT / 'out/ci-release'
    os.chdir(output)
    subprocess.run(['sha256sum', '--check', 'SHA256SUMS'], check=True)
    checksums = (output / 'SHA256SUMS').read_text()
    assets = [line.split()[1] for line in checksums.splitlines() if line.strip()] + ['SHA256SUMS']
    tag = 'ci-lemonade-' + commit[:12]
    endpoint = 'repos/' + os.environ['GH_REPO'] + '/releases/tags/' + tag
    result = subprocess.run(['gh', 'api', endpoint], capture_output=True, text=True)
    if result.returncode and '(HTTP 404)' not in result.stderr:
        raise SystemExit('Cannot inspect release state; refusing to assume it is absent')
    release = json.loads(result.stdout) if result.returncode == 0 else None
    if release and not release['draft']:
        with tempfile.TemporaryDirectory() as temporary:
            gh('release', 'download', tag, '--pattern', 'SHA256SUMS', '--dir', temporary)
            verify_remote(release, (Path(temporary) / 'SHA256SUMS').read_text())
        print('Preserved the previously published, complete release: ' + release['html_url'])
        return
    if release is None:
        notes = f'''Complete experimental OnePlus 9 LE2110 / 8 GiB / AMB655X RAM runtime from {commit}.

Includes the system image, clean runtime, GPU/Wi-Fi firmware, fresh kernel, GLES/Metal libraries and generic loader boot.img. No saved Wi-Fi credentials, connection history, accounts or per-phone binding. Uses temporary fastboot boot; no partition flashing.

Download every .tar.gz.part-* asset, SHA256SUMS, README.md and manifest.json from this release. Verify, concatenate and extract as described in README.md. Requires rooted Android and an unlocked bootloader. CI builds have no per-binary hardware acceptance; start requires --experimental. Static system/root-template/UEFI inputs are hash-pinned and reused.

Build: {os.environ['RUN_URL']}
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md') as source:
            source.write(notes)
            source.flush()
            gh('release', 'create', tag, '--target', commit, '--draft', '--prerelease',
               '--title', 'OnePlus 9 CI ' + commit[:12], '--notes-file', source.name)
    gh('release', 'upload', tag, *assets, '--clobber')
    verify_remote(json.loads(gh('api', endpoint)), checksums)
    gh('release', 'edit', tag, '--draft=false')
    published = json.loads(gh('api', endpoint))
    if published['draft']:
        raise SystemExit('Release remains a draft')
    print(published['html_url'])


if __name__ == '__main__':
    main()
