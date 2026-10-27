#!/usr/bin/env python3
"""Build a separate, verified RAM overlay for native Bluetooth interface discovery."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import plistlib

ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load('bluetooth_base_builder', ROOT / 'devices/oneplus-9/tools/lemonade-build-base.py')
digest = builder.digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path,
                        default=ROOT / 'out/releases/lemonade-2026.10.26-base.2')
    parser.add_argument('--output', type=Path, required=True, help='new directory; never overwritten')
    args = parser.parse_args()
    os.umask(0o077)
    source = args.base.resolve()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit('Refusing to replace an existing probe directory')
    if not output.is_relative_to(ROOT / 'out'):
        raise SystemExit('Probe output must be inside the ignored out/ directory')
    manifest = json.loads((source / 'manifest.json').read_text())
    for name in ('kernel', 'root.apfs', 'system.apfs'):
        entry = manifest['files'][name]
        if (source.joinpath(name).stat().st_size != entry['bytes'] or
                digest(source / name) != entry['sha256']):
            raise SystemExit('Base identity mismatch: ' + name)
    receipt = json.loads((ROOT / 'out/lemonade-bt-trace/build.json').read_text())
    if receipt.get('profile') != 'lemonade-bluetooth-trace-v1' or not receipt.get('observation_only'):
        raise SystemExit('Invalid trace library build receipt')
    for name, value in receipt['sources'].items():
        if digest(ROOT / name) != value:
            raise SystemExit('Trace source changed after its build: ' + name)
    library = ROOT / 'out/lemonade-bt-trace/liblemonade-bt-trace.dylib'
    if digest(library) != receipt['sha256']:
        raise SystemExit('Trace library differs from its build receipt')
    before = builder.base.contents(source / 'root.apfs')
    entries = dict(before)
    entries['lib/liblemonade-bt-trace.dylib'] = (0o100755, 0, 0, library.read_bytes())
    mode, uid, gid, conf = entries['etc/inject.conf']
    entries['etc/inject.conf'] = (mode, uid, gid, conf.rstrip(b'\n') +
        b'\n/redwood/lib/liblemonade-bt-trace.dylib bluetoothd BlueTool\n')
    stock = ROOT / 'sysimage/rootfs'
    services = {}
    container = builder.base.apfs.Container(str(source / 'system.apfs'))
    try:
        volume = builder.base.apfs.pick_volume(container, None)
        for name, program in (
            ('com.apple.bluetoothd', '/usr/sbin/bluetoothd'),
            ('com.apple.BlueTool', '/usr/sbin/BlueTool'),
            ('com.apple.bluetoothuserd', '/usr/libexec/bluetoothuserd'),
        ):
            plist = stock / 'System/Library/LaunchDaemons' / (name + '.plist')
            job = plistlib.loads(plist.read_bytes())
            if job.get('Label') != name or (job.get('Program') or job['ProgramArguments'][0]) != program:
                raise SystemExit('Unexpected native service definition: ' + name)
            found = volume.lookup(program)
            if not found or volume.inode(found[0]).contents() != (stock / program.lstrip('/')).read_bytes():
                raise SystemExit('Native Bluetooth executable missing or changed: ' + name)
            # Only diagnostic launch/output policy changes. Keep native identity,
            # arguments, MachServices, transactions and dependencies intact.
            job['StandardOutPath'] = job['StandardErrorPath'] = '/dev/console'
            job['RunAtLoad'] = True
            job['ThrottleInterval'] = 10
            entries['LaunchDaemons/' + name + '.plist'] = (0o100644, 0, 0, plistlib.dumps(job))
            services[name] = {'program': program, 'program_sha256': digest(stock / program.lstrip('/')),
                              'stock_job_sha256': digest(plist)}
    finally:
        container.close()
    output.mkdir(parents=True)
    builder.pack_tree(entries, output / 'root.apfs')
    readback = builder.base.contents(output / 'root.apfs')
    if readback != entries:
        raise SystemExit('Probe image failed complete entry readback')
    changed = sorted(k for k in entries if entries[k] != before.get(k))
    allowed = {'etc/inject.conf', 'lib/liblemonade-bt-trace.dylib',
               *('LaunchDaemons/' + n + '.plist' for n in services)}
    if set(changed) != allowed or any(k not in entries for k in before):
        raise SystemExit('Unexpected probe image changes')
    record = {'profile': 'lemonade-bluetooth-interface-probe-v1', 'hardware_verified': False,
              'base': str(source.relative_to(ROOT)), 'base_manifest_sha256': digest(source / 'manifest.json'),
              'base_root_sha256': manifest['files']['root.apfs']['sha256'],
              'kernel_sha256': manifest['files']['kernel']['sha256'],
              'system_sha256': manifest['files']['system.apfs']['sha256'],
              'root_sha256': digest(output / 'root.apfs'), 'bytes': (output / 'root.apfs').stat().st_size,
              'trace_receipt': receipt, 'services': services, 'changed_entries': changed,
              'all_other_entries_verified': True, 'normal_base_unchanged': True}
    for name in ('root.apfs', 'system.apfs', 'kernel'):
        if digest(source / name) != manifest['files'][name]['sha256']:
            raise SystemExit('Original base changed during probe image build')
    (output / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    print('Native Bluetooth probe image built and fully read back:', output.relative_to(ROOT))
    print('Changed entries:', ', '.join(changed))


if __name__ == '__main__':
    main()
