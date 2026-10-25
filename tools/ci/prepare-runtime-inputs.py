#!/usr/bin/env python3
"""Export audited, reusable runtime inputs. Does not read a live phone or its data."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import struct
import zlib

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('base_builder', ROOT / 'tools/lemonade-build-base.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
digest = base.digest
CHUNK = 1500 * 1024 * 1024


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    args = parser.parse_args()
    output = ROOT / 'out/ci-inputs' / args.tag
    output.mkdir(parents=True, exist_ok=False)
    tree = base.base.contents(args.base / 'root.apfs')
    base.validate_tree(tree)
    for path, (mode, uid, gid, data) in tree.items():
        if path.startswith('var/') and stat.S_ISREG(mode):
            public = subprocess.check_output(['bash', 'common/ugit', 'show', 'HEAD:userland/' + path], cwd=ROOT)
            if data != public:
                raise SystemExit('Non-public preference in template: ' + path)
        if any(word in path.lower() for word in ('keychain', 'authorized_keys', 'ssh_host', 'lemonade-network')):
            raise SystemExit('Private state in template: ' + path)
    if b'.ssh' in tree['etc/var.skel'][3]:
        raise SystemExit('SSH material referenced by skeleton')
    system = args.base / 'system.apfs'
    expected = json.loads((ROOT / 'local/lemonade/images.json').read_text())['userland/system.apfs']['sha256']
    if digest(system) != expected:
        raise SystemExit('System differs from clean image baseline')
    dtb = ROOT / 'local/lemonade/boot.dtb'
    dts = subprocess.check_output(['dtc', '-I', 'dtb', '-O', 'dts', str(dtb)], stderr=subprocess.DEVNULL, text=True)
    forbidden = ('serial-number =', 'mac-address =', 'bluetooth-address =', 'androidboot.serial', 'chosen {', '__symbols__ {')
    if any(value in dts for value in forbidden):
        raise SystemExit('Device identity or runtime boot data remains in DTB')
    folder = output / 'runtime-inputs'
    folder.mkdir()
    sources = {
        'root.apfs': args.base / 'root.apfs', 'boot.dtb': dtb,
        'BootShim.bin': ROOT / 'Mu-Silicium/BootShim/BootShim.bin',
        'SILICIUM_UEFI.fd': ROOT / 'Mu-Silicium/Build/lemonadePkg/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd',
        'ramdisk': ROOT / 'Mu-Silicium/Resources/ramdisk',
        'mkbootimg.py': ROOT / 'Mu-Silicium/Resources/Scripts/mkbootimg.py',
    }
    boot = (args.base / 'boot.img').read_bytes()
    size = struct.unpack_from('<I', boot, 8)[0]
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    unpacked = decoder.decompress(boot[4096:4096 + size]) + decoder.flush()
    prefix = sources['BootShim.bin'].read_bytes() + sources['SILICIUM_UEFI.fd'].read_bytes()
    if not unpacked.startswith(prefix) or decoder.unused_data != dtb.read_bytes():
        raise SystemExit('UEFI, shim or DTB differs from the validated local base')
    for name, source in sources.items():
        shutil.copyfile(source, folder / name)
    inventory = {p: {'mode': mode, 'uid': uid, 'gid': gid, 'sha256': __import__('hashlib').sha256(data).hexdigest()}
                 for p, (mode, uid, gid, data) in sorted(tree.items())}
    receipt = {
        'profile': 'lemonade-runtime-inputs-v1', 'tag': args.tag,
        'system_sha256': expected, 'system_bytes': system.stat().st_size,
        'userland_commit': subprocess.check_output(['bash', 'common/ugit', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'uefi_commit': (ROOT / 'uefi/BASE').read_text().strip(),
        'uefi_preparation': 'tools/lemonade-prepare.py; XNU_NO_PERSISTENT_LOGS=1; payload 0xF0000000/64MiB',
        'root_inventory': inventory,
        'files': {name: {'sha256': digest(folder / name), 'bytes': (folder / name).stat().st_size} for name in sources},
        'wifi_credentials_included': False, 'wifi_history_included': False,
        'personal_device_identity_included': False,
    }
    (folder / 'inputs.json').write_text(json.dumps(receipt, indent=2) + '\n')
    archive = output / 'runtime-inputs.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(folder, arcname='runtime-inputs')
    assets = [archive]
    with system.open('rb') as source:
        index = 0
        while data := source.read(CHUNK):
            part = output / f'system.apfs.part-{index:02d}'
            part.write_bytes(data)
            assets.append(part)
            index += 1
    lock = {
        'profile': receipt['profile'], 'tag': args.tag,
        'repository': 'b1ackm4x/hackios-xnu-kernel',
        'system_sha256': expected, 'system_bytes': system.stat().st_size,
        'assets': {p.name: {'sha256': digest(p), 'bytes': p.stat().st_size} for p in assets},
    }
    (ROOT / 'tools/ci/runtime-inputs.json').write_text(json.dumps(lock, indent=2) + '\n')
    (output / 'SHA256SUMS').write_text(''.join(lock['assets'][p.name]['sha256'] + '  ' + p.name + '\n' for p in assets))
    shutil.rmtree(folder)
    print('Audited public inputs:', output, '(no credentials, history, or device binding)')


if __name__ == '__main__':
    main()
