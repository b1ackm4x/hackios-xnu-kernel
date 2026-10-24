#!/usr/bin/env python3
"""Archive one verified Metal image with its matching sources and binaries."""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('label')
    parser.add_argument('--system-metal', action='store_true')
    parser.add_argument('--widgets', action='store_true')
    args = parser.parse_args()
    if args.widgets and not args.system_metal:
        parser.error('--widgets requires --system-metal')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', args.label):
        parser.error('Use a short lowercase archive label')
    image_dir = ROOT / ('out/lemonade-metal-widgets' if args.widgets else 'out/lemonade-metal-system' if args.system_metal else 'out/lemonade-metal')
    profile = 'metal-widgets-v1' if args.widgets else 'metal-system-v1' if args.system_metal else 'metal-baseline-v1'
    jobs = ROOT / ('common/devices/lemonade-metal-system.rc.jobs' if args.system_metal else 'common/devices/lemonade-metal.rc.jobs')
    manifest = json.loads((image_dir / 'manifest.json').read_text())
    build = json.loads((image_dir / 'build.json').read_text())
    if (manifest.get('profile') != profile or
            manifest.get('sha256') != digest(image_dir / 'root.apfs') or
            manifest.get('build_receipt_sha256') != digest(image_dir / 'build.json') or
            manifest.get('jobs_sha256') != digest(jobs)):
        raise SystemExit('Refusing an image whose profile, receipt, or jobs do not match')
    for relative, expected in build['sources'].items():
        if digest(ROOT / relative) != expected:
            raise SystemExit('Source differs from image: ' + relative)
    binaries = ROOT / 'out/lemonade-metal'
    for relative, expected in build['artifacts'].items():
        if digest(binaries / relative) != expected['sha256']:
            raise SystemExit('Binary differs from image: ' + relative)
    destination = ROOT / 'local/lemonade/graphics-archives' / args.label
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    for filename in ('root.apfs', 'manifest.json', 'build.json'):
        shutil.copy2(image_dir / filename, destination / filename)
    shutil.copy2(jobs, destination / 'rc.jobs')
    for group, source, entries in [('sources', ROOT, build['sources']), ('artifacts', binaries, build['artifacts'])]:
        for relative in entries:
            target = destination / group / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, target)
    print(json.dumps({'archive': str(destination.relative_to(ROOT)), 'profile': profile,
                      'image_sha256': manifest['sha256'], 'sources_and_artifacts_verified': True}))

if __name__ == '__main__':
    main()
