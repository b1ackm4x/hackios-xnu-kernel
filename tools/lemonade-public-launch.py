#!/usr/bin/env python3
"""Launch an experimental OnePlus 9 CI runtime from RAM (never flash partitions)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

HERE = Path(__file__).resolve().parent
LOADER_SERIAL = 'lemonade-ci'
PANEL = 'qcom,mdss_dsi_samsung_amb655x_dsc_cmd:'
# Shared hardware profile, not a device identifier: /memory/reg in the clean DTB.
MEMORY_REG_SHA256 = '8f23ffc54df402a2da37b2ec86f62300b8cfefa6fd203451244d8ab1105b74a6'
BOOT_ARGUMENTS = ('sb_delay=8 bootcpu=7 cpus=4 cpufreq=1 ddr=0 gpu=1 gpu_log=0 adreno_speed=2 '
    'metal=1 metal_profile=1 camtl=1 lemonade_display=10 lemonade_display_hz=120 lemonade_panel=655 ca_vbl=0 fb_fences=1 '
    'touchscreen=1 buttons=0 wifi=0 lemonade_wifi=24 lemonade_runtime=1 lemonade_dma_verified=1 '
    'adsp=0 ufs=0 ufs_native=0 ufs_write=0 var_store=0').split()


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def command(argv, timeout=30):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise SystemExit('Command failed: ' + Path(argv[0]).name + '; check USB connection and device state.')
    return result.stdout + result.stderr


def verify(folder=HERE):
    manifest = json.loads((folder / 'manifest.json').read_text())
    if (manifest.get('profile') != 'lemonade-public-runtime-v1' or
            manifest.get('boot_arguments') != BOOT_ARGUMENTS or manifest.get('partition_writes') is not False):
        raise SystemExit('Unexpected runtime manifest or boot configuration')
    required = {'kernel', 'root.apfs', 'system.apfs', 'boot.img', 'launch.py', 'mkpayload.py'}
    if not required.issubset(manifest.get('files', {})):
        raise SystemExit('Incomplete runtime package')
    for name, expected in manifest['files'].items():
        path = folder / name
        if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(folder.resolve()) or
                path.stat().st_size != expected['bytes'] or digest(path) != expected['sha256']):
            raise SystemExit('Runtime verification failed: ' + name)
    return manifest


def adb_devices(text):
    result = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if (len(parts) >= 3 and parts[1] == 'device' and
                (parts[2].startswith('usb:') or re.fullmatch(r'\d+-\d+(?:\.\d+)*', parts[2]))):
            result.append(parts[0])
    return result


def fastboot_devices():
    return [p[0] for line in command(['fastboot', 'devices']).splitlines()
            if len(p := line.split()) >= 2 and p[1] == 'fastboot']


def unique_target(devices, requested=None):
    if len(devices) != 1 or (requested and devices[0] != requested):
        raise SystemExit('Connect exactly one target phone by USB; --serial must match it.')
    return devices[0]


def fb(serial, *args, timeout=30):
    return command(['fastboot', '-s', serial, *map(str, args)], timeout)


def wait_for(function, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if value := function():
            return value
        time.sleep(1)
    raise SystemExit('Timed out waiting for the target USB interface; no further commands sent.')


def loader():
    target = unique_target(fastboot_devices(), LOADER_SERIAL)
    if not re.search(r'product:\s*xnuloader(?:\s|$)', fb(target, 'getvar', 'product')):
        raise SystemExit('The connected device is not this runtime loader')
    return target


def start(manifest, requested, experimental):
    if not experimental:
        raise SystemExit('CI builds have no device acceptance. Read README.md, then opt in with: python3 launch.py start --experimental')
    target = unique_target(adb_devices(command(['adb', 'devices', '-l'])), requested)
    if fastboot_devices():
        raise SystemExit('Disconnect other fastboot devices before starting')
    for name, expected in (('ro.product.model', 'LE2110'), ('ro.soc.model', 'SM8350'), ('ro.boot.flash.locked', '0')):
        if command(['adb', '-s', target, 'shell', 'getprop', name]).strip() != expected:
            raise SystemExit('Requires an unlocked OnePlus 9 LE2110 / SM8350')
    # Keep the full boot command line private. Only compare the selected panel.
    cmdline = command(['adb', '-s', target, 'shell', "su -c 'cat /proc/cmdline'"])
    if re.findall(r'(?:^|\s)msm_drm\.dsi_display0=([^\s]+)', cmdline) != [PANEL]:
        raise SystemExit('Requires the AMB655X panel and root access to verify its identity')
    memory = command(['adb', '-s', target, 'shell', "su -c 'sha256sum /proc/device-tree/memory/reg'"])
    if memory.split()[:1] != [MEMORY_REG_SHA256]:
        raise SystemExit('Requires the matching 8 GiB memory layout; no reboot or boot image was sent.')
    print('Experimental build: GPU/Wi-Fi integration has not been accepted on this binary. RAM-only boot.', flush=True)
    command(['adb', '-s', target, 'reboot', 'bootloader'])
    wait_for(lambda: fastboot_devices() == [target], 60)
    if not re.search(r'product:\s*(?:lemonade|OnePlus9|LE2110|lahaina)(?:\s|$)', fb(target, 'getvar', 'product')):
        raise SystemExit('Unexpected bootloader product')
    fb(target, 'boot', HERE / 'boot.img', timeout=60)
    wait_for(lambda: fastboot_devices() == [LOADER_SERIAL], 40)
    target = loader()
    print('Uploading the system into RAM…', flush=True)
    fb(target, 'oem', 'ramdisk')
    fb(target, 'stage', HERE / 'system.apfs', timeout=300)
    with tempfile.TemporaryDirectory(prefix='lemonade-ci-') as temporary:
        payload = Path(temporary) / 'boot.bin'
        arguments = BOOT_ARGUMENTS + ['epoch=' + str(int(time.time()))]
        command(['python3', str(HERE / 'mkpayload.py'), '-o', str(payload),
                 'kernel=' + str(HERE / 'kernel'), 'ramdisk=' + str(HERE / 'root.apfs'),
                 '--cmdline', ' '.join(arguments)], timeout=60)
        if payload.stat().st_size > 0x03f00000:
            raise SystemExit('Boot payload exceeds loader limit')
        fb(target, 'stage', payload, timeout=60)
    fb(target, 'oem', 'run', '0')
    print('Start requested. Wait for SpringBoard. No saved Wi-Fi, automated gestures, or timed exit.')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('verify', 'start', 'stop', 'android', 'status'))
    parser.add_argument('--serial', help='Optional Android USB serial; never saved into the package')
    parser.add_argument('--experimental', action='store_true', help='Accept running a CI binary without per-binary hardware acceptance')
    args = parser.parse_args()
    manifest = verify()
    if args.action == 'verify':
        print('All runtime files verified; this is not a hardware acceptance result.')
    elif args.action == 'start':
        start(manifest, args.serial, args.experimental)
    else:
        target = loader()
        result = fb(target, 'getvar', 'result')
        if args.action == 'status':
            print(result)
            return
        if 'running' in result:
            fb(target, 'oem', 'stop')
            wait_for(lambda: 'returned' in fb(target, 'getvar', 'result'), 45)
        if args.action == 'android':
            fb(target, 'reboot')
            print('Return to Android requested. Wait for the Android lock screen.')
        else:
            print('Runtime stopped; loader remains in RAM.')


if __name__ == '__main__':
    main()
