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


def find_release(repository, tag):
    # Draft tags need not exist as Git refs, so /releases/tags/<tag> can
    # return 404 even after every asset has uploaded successfully.
    page = 1
    while True:
        releases = json.loads(gh('api', f'repos/{repository}/releases?per_page=100&page={page}'))
        for release in releases:
            if release['tag_name'] == tag:
                return release
        if len(releases) < 100:
            return None
        page += 1


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
    repository = os.environ['GH_REPO']
    release = find_release(repository, tag)
    if release and not release['draft']:
        with tempfile.TemporaryDirectory() as temporary:
            gh('release', 'download', tag, '--pattern', 'SHA256SUMS', '--dir', temporary)
            verify_remote(release, (Path(temporary) / 'SHA256SUMS').read_text())
        print('Preserved the previously published, complete release: ' + release['html_url'])
        return
    if release is None:
        notes = f'''Generic OnePlus 9 ARM64 kernel and driver components from {commit}.

Includes a fresh XNU kernel, QCA6490 driver, GLES/Metal libraries, startup runtime, generic EFI loader, source snapshots and build receipts. No Apple system images, phone firmware, device DTB/UEFI, Wi-Fi credentials/history or user accounts are included.

Download the .tar.gz, SHA256SUMS, README.md and manifest.json. Verify and extract as described in README.md, then supply your own local system/device files. This is a component prerelease, not a standalone flashable ROM. CI compilation and host tests do not replace device acceptance.

Build: {os.environ['RUN_URL']}
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md') as source:
            source.write(notes)
            source.flush()
            gh('release', 'create', tag, '--target', commit, '--draft', '--prerelease',
               '--title', 'OnePlus 9 CI ' + commit[:12], '--notes-file', source.name)
        release = find_release(repository, tag)
        if release is None:
            raise SystemExit('Cannot locate the created draft; leaving it unpublished')
    endpoint = f'repos/{repository}/releases/{release["id"]}'
    gh('release', 'upload', tag, *assets, '--clobber')
    verify_remote(json.loads(gh('api', endpoint)), checksums)
    gh('release', 'edit', tag, '--draft=false')
    published = json.loads(gh('api', endpoint))
    if published['draft']:
        raise SystemExit('Release remains a draft')
    print(published['html_url'])


if __name__ == '__main__':
    main()
