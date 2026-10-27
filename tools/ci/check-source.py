#!/usr/bin/env python3
"""Fail early if a clone is incomplete or would include private build inputs."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]

def main():
    tracked = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')) - {''}
    required = ('Makefile', 'userland/build.sh', 'userland/src/init.c',
                'userland/src/redwoodmetal.m', 'userland/tests/test_jb_setid_manifest.py',
                'sysimage/fetch.sh', 'devices/oneplus-9/device.conf',
                'devices/oneplus-9/tools/lemonade-build-kernel.sh',
                'devices/oneplus-9/uefi/BASE', 'devices/poco-x5-pro/uefi/BASE',
                'devices/poco-x5-pro/prebuilt/device-tree.dtb', 'licenses/userland-MIT.txt')
    for name in required:
        if name not in tracked or not (ROOT / name).is_file():
            raise SystemExit('Incomplete monorepo source: ' + name)
    obsolete = ('ios165/', 'uefi/', 'prebuilt/uefi/', 'common/devices/', 'tools/lemonade-', 'tools/test-lemonade-')
    private = ('local/', 'out/', 'logs/', 'fw-', 'sysimage/rootfs/', 'userland/root/', 'userland/obj/')
    for name in sorted(tracked):
        if name.startswith(obsolete + private) or (name.endswith(('.apfs', '.ipsw', '.dmg', '.pem', '.key')) and name != 'userland/rc.jobs.apfs'):
            raise SystemExit('Unexpected tracked path: ' + name)
    if (ROOT / '.userland.git').exists():
        raise SystemExit('A legacy second repository is still active; preserve it outside the source tree')
    for name in ('local/probe', 'out/probe', 'sysimage/rootfs/probe', 'sysimage/test.ipsw',
                 'sysimage/test.dmg', 'userland/root/probe', 'userland/obj/probe',
                 'fw-wifi/probe', 'xnuloader/identity.h', 'devices/oneplus-9/prebuilt/boot.img'):
        if subprocess.run(['git', 'check-ignore', '-q', name], cwd=ROOT).returncode:
            raise SystemExit('Private/generated path is not ignored: ' + name)
    print('Single-repository source, device layout and private-file exclusions verified.')

if __name__ == '__main__':
    main()
