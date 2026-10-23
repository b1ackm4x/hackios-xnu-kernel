#!/usr/bin/env python3
"""Build a private RAM-only firmware overlay; preserve both normal APFS images."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import plistlib
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/apfs'))
import apfs


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def contents(image):
    container = apfs.Container(str(image))
    volume = apfs.pick_volume(container, None)
    root_inode = volume.inode(apfs.ROOT_DIR_INO_NUM)
    records = {'': (root_inode.mode, root_inode.owner, root_inode.group, b'')}
    pending = [(apfs.ROOT_DIR_INO_NUM, '')]
    while pending:
        parent, prefix = pending.pop()
        for name, fid, kind in volume.dir_entries(parent):
            if name in ('.', '..') or '/' in name or '\n' in name or '\t' in name:
                raise SystemExit('Unsafe path in source image')
            relative = prefix + name
            inode = volume.inode(fid)
            data = b'' if kind == 4 else inode.contents()
            records[relative] = (inode.mode, inode.owner, inode.group, data)
            if kind == 4:
                pending.append((fid, relative + '/'))
            elif kind not in (8, 10):
                raise SystemExit('Unsupported inode in source image')
    container.close()
    return records


def current_network():
    spec = importlib.util.spec_from_file_location('private_network', ROOT / 'tools/lemonade-network.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper.read_current(ROOT)


def network_services():
    """Use the stock iOS daemons already present in the verified System image."""
    programs = {'com.apple.configd': '/usr/libexec/configd',
                'com.apple.mDNSResponder': '/usr/sbin/mDNSResponder',
                'com.apple.mDNSResponderHelper': '/usr/sbin/mDNSResponderHelper'}
    container = apfs.Container(str(ROOT / 'userland/system.apfs'))
    try:
        volume = apfs.pick_volume(container, None)
        additions = {}
        for name, program in programs.items():
            source = ROOT / 'ios165/rootfs/System/Library/LaunchDaemons' / (name + '.plist')
            job = plistlib.loads(source.read_bytes())
            if (job.get('Program') or job.get('ProgramArguments', [None])[0]) != program or not volume.lookup(program):
                raise SystemExit('Missing or mismatched iOS network service: ' + name)
            job['StandardOutPath'] = job['StandardErrorPath'] = '/dev/console'
            job.pop('_LimitLoadToDeviceTree', None)
            additions['LaunchDaemons/' + name + '.plist'] = (0o100644, 0, 0, plistlib.dumps(job))
        return additions
    finally:
        container.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver-files', action='store_true', help='include verified original M3, board and regulatory data')
    parser.add_argument('--connect-current', action='store_true', help='RAM-only acceptance using this exact phone\'s current Android Wi-Fi')
    args = parser.parse_args()
    if args.connect_current and not args.driver_files:
        parser.error('--connect-current requires --driver-files')
    os.umask(0o077)
    output = ROOT / 'out/lemonade-wifi'
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    network = None
    probe = ROOT / 'out/lemonade-net-test'
    if args.connect_current:
        receipt_path = ROOT / 'out/lemonade-net-test.manifest.json'
        if not probe.is_file() or not receipt_path.is_file():
            raise SystemExit('Build the arm64 lemonade network acceptance program first')
        receipt = json.loads(receipt_path.read_text())
        if (receipt.get('arch') != 'arm64' or receipt.get('acceptance_profile') != 'apple80211-dhcp-http-reconnect-v1' or
                receipt.get('sha256') != digest(probe) or
                receipt.get('source_sha256') != digest(ROOT / 'tools/lemonade-net-test.m')):
            raise SystemExit('Acceptance program differs from its verified build')
        try:
            network = current_network()
        except Exception:
            raise SystemExit('Could not privately read one supported current network from the verified phone') from None
    images = json.loads((ROOT / 'local/lemonade/images.json').read_text())
    original = ROOT / 'userland/root.apfs'
    original_hash = digest(original)
    if original_hash != images['userland/root.apfs']['sha256']:
        raise SystemExit('Normal root image does not match its private manifest')
    firmware_dir = ROOT / 'local/lemonade/wifi/firmware'
    manifest = json.loads((firmware_dir / 'manifest.json').read_text())
    fw = firmware_dir / 'amss.bin'
    expected = manifest['files']['amss.bin']
    if (manifest.get('device') != 'lemonade' or not manifest.get('usb_identity_verified') or
            not expected.get('matches_device') or fw.stat().st_size != 5178848 or
            expected['sha256'] != '71e701a8cd8b5d37da939e5eccc4f7786c1f031eb92084b6a616bc0f6c1ed521' or
            digest(fw) != expected['sha256']):
        raise SystemExit('Firmware identity/hash differs from the verified QCA6490 v1 image')
    names = ['amss.bin'] + (['m3.bin', 'board.bin', 'regdb.bin'] if args.driver_files else [])
    verified = {}
    for name in names:
        source = firmware_dir / name
        entry = manifest['files'].get(name, {})
        if (not source.is_file() or source.is_symlink() or not entry.get('matches_device') or
                source.stat().st_size != entry.get('bytes') or digest(source) != entry.get('sha256')):
            raise SystemExit('Missing or unverified original firmware file: ' + name)
        verified[name] = {'sha256': entry['sha256'], 'bytes': entry['bytes']}
    with tempfile.TemporaryDirectory(prefix='build-', dir=output) as work:
        work = Path(work)
        tree = work / 'root'
        tree.mkdir()
        # The verified image is authoritative; an old staging tree may differ.
        before = contents(original)
        metadata = []
        for relative, (mode, uid, gid, data) in sorted(before.items()):
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
        metadata_path = work / 'metadata.tsv'
        metadata_path.write_text(''.join(metadata))
        def pack(destination):
            subprocess.run(['python3', str(ROOT / 'tools/apfs/mkapfs.py'), '-z', '--plain', 'fw',
                            '-o', str(destination), f'redwood={tree},manifest={metadata_path}'], check=True,
                           stdout=subprocess.DEVNULL)
        base = work / 'root.apfs'
        pack(base)
        if contents(base) != before:
            raise SystemExit('Reconstructed root differs in file contents or permissions')
        if 'fw' in before and not stat.S_ISDIR(before['fw'][0]):
            raise SystemExit('Firmware directory in the normal image is not a directory')
        (tree / 'fw').mkdir(exist_ok=True)
        if 'fw' not in before:
            (tree / 'fw').chmod(0o755)
        for name in names:
            destination_fw = tree / 'fw' / name
            if destination_fw.exists() or destination_fw.is_symlink():
                destination_fw.unlink()
            shutil.copy2(firmware_dir / name, destination_fw)
            destination_fw.chmod(0o644)
        additions = {}
        if network is not None:
            job = {'Label': 'com.lemonade.wifi-test',
                   'ProgramArguments': ['/redwood/bin/lemonade-net-test'], 'RunAtLoad': True,
                   'StandardOutPath': '/dev/console', 'StandardErrorPath': '/dev/console'}
            additions = {'fw/lemonade-network': (0o100600, 0, 0, network),
                         'bin/lemonade-net-test': (0o100755, 0, 0, probe.read_bytes()),
                         'LaunchDaemons/com.lemonade.wifi-test.plist': (0o100644, 0, 0, plistlib.dumps(job))}
            additions.update(network_services())
            for relative, (mode, uid, gid, data) in additions.items():
                target = tree / relative
                target.write_bytes(data)
                target.chmod(stat.S_IMODE(mode))
                metadata.append(f'{mode:o}\t{uid}\t{gid}\t{relative}\n')
            metadata_path.write_text(''.join(metadata))
        candidate = work / 'wifi.apfs'
        pack(candidate)
        after = contents(candidate)
        allowed = dict(before)
        allowed.setdefault('fw', (0o40755, 0, 0, b''))
        for name in names:
            allowed['fw/' + name] = (0o100644, 0, 0, (firmware_dir / name).read_bytes())
        allowed.update(additions)
        if after != allowed:
            raise SystemExit('Firmware overlay changes files outside the selected verified firmware')
        if digest(original) != original_hash:
            raise SystemExit('Normal root image changed during packaging')
        shutil.copy2(candidate, output / 'root.apfs')
    report = {'device': 'lemonade', 'scope': 'private RAM firmware test only',
              'base_root_sha256': original_hash, 'sha256': digest(output / 'root.apfs'),
              'bytes': (output / 'root.apfs').stat().st_size,
              'firmware': verified,
              'network_private': network is not None,
              'acceptance_program_sha256': digest(probe) if network is not None else None,
              'network_services': ['configd', 'mDNSResponder', 'mDNSResponderHelper'] if network is not None else [],
              'network_reconnect_test': network is not None,
              'normal_root_unchanged': True, 'other_file_contents_and_modes_verified': True,
              'deployed': False}
    (output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
