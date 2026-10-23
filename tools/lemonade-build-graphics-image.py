#!/usr/bin/env python3
"""Package a verified, temporary graphics test root without replacing normal images."""
import hashlib
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lemonade_image', ROOT / 'tools/lemonade-build-wifi-image.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--system-metal', action='store_true')
    args = parser.parse_args()
    built = ROOT / 'out/lemonade-metal'
    output = ROOT / ('out/lemonade-metal-system' if args.system_metal else 'out/lemonade-metal')
    output.mkdir(exist_ok=True)
    record = json.loads((built / 'build.json').read_text())
    if record.get('arch') != 'arm64' or record.get('profile') != 'metal-baseline-v1':
        raise SystemExit('Unexpected Metal build profile')
    for name, expected in record['sources'].items():
        if digest(ROOT / name) != expected:
            raise SystemExit('Metal source changed after build: ' + name)
    for name, info in record['artifacts'].items():
        if digest(built / name) != info['sha256']:
            raise SystemExit('Metal binary differs from the build receipt: ' + name)
    original = ROOT / 'userland/root.apfs'
    original_hash = digest(original)
    images = json.loads((ROOT / 'local/lemonade/images.json').read_text())
    if original_hash != images['userland/root.apfs']['sha256']:
        raise SystemExit('Normal root image differs from its verified manifest')
    before = base.contents(original)
    changes = {}
    for name in record['artifacts']:
        if record['artifacts'][name].get('system_only') and not args.system_metal:
            continue
        relative = ('lib/' if name.endswith('.dylib') or '.bundle/' in name else 'share/' if name.endswith('.metallib') else 'bin/') + name
        changes[relative] = (0o100644 if name.endswith(('.plist', '.metallib')) else 0o100755, 0, 0, (built / name).read_bytes())
    changes['lib/RedwoodMetal.bundle'] = (0o40755, 0, 0, b'')
    jobs = ROOT / ('common/devices/lemonade-metal-system.rc.jobs' if args.system_metal else 'common/devices/lemonade-metal.rc.jobs')
    changes['etc/rc.jobs'] = (0o100644, 0, 0, jobs.read_bytes())
    if args.system_metal:
        mode, uid, gid, rc = before['etc/rc']
        marker = b'exec /sbin/launchd.ios'
        if rc.count(marker) != 1: raise SystemExit('Unexpected normal root init script')
        changes['etc/rc'] = (mode, uid, gid, rc.replace(marker, b'/redwood/bin/metal_props on\n' + marker))
    after = dict(before)
    after.update(changes)
    with tempfile.TemporaryDirectory(prefix='pack-', dir=output) as work:
        work = Path(work)
        tree = work / 'root'
        tree.mkdir()
        metadata = []
        for relative, (mode, uid, gid, data) in sorted(after.items()):
            target = tree / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if stat.S_ISDIR(mode):
                target.mkdir(exist_ok=True)
            elif stat.S_ISLNK(mode):
                target.symlink_to(os.fsdecode(data))
            else:
                target.write_bytes(data)
            if not stat.S_ISLNK(mode):
                target.chmod(stat.S_IMODE(mode))
            metadata.append(f'{mode:o}\t{uid}\t{gid}\t{relative}\n')
        manifest = work / 'metadata.tsv'
        manifest.write_text(''.join(metadata))
        candidate = work / 'root.apfs'
        subprocess.run(['python3', str(ROOT / 'tools/apfs/mkapfs.py'), '-z', '--plain', 'fw',
                        '-o', str(candidate), f'redwood={tree},manifest={manifest}'],
                       check=True, stdout=subprocess.DEVNULL)
        if base.contents(candidate) != after or digest(original) != original_hash:
            raise SystemExit('Graphics image readback differs from the selected changes')
        shutil.copy2(candidate, output / 'root.apfs')
    if args.system_metal:
        shutil.copy2(built / 'build.json', output / 'build.json')
    report = {'device': 'lemonade', 'profile': 'metal-system-v1' if args.system_metal else record['profile'],
              'base_root_sha256': original_hash, 'sha256': digest(output / 'root.apfs'),
              'bytes': (output / 'root.apfs').stat().st_size,
              'build_receipt_sha256': digest(output / 'build.json'),
              'jobs_sha256': digest(jobs), 'network_private': False,
              'normal_root_unchanged': True, 'other_file_contents_and_modes_verified': True}
    (output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
