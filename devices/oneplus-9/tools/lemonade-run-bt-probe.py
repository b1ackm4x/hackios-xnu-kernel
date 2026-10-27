#!/usr/bin/env python3
"""Bounded native Bluetooth interface observation, using a verified base kernel.

Requires the exact OnePlus 9 lease. Starts in Android and returns to Android;
never flashes a partition. An unavailable USB channel requires human recovery.
This gathers missing interface evidence; it does not certify working Bluetooth.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load(path):
    spec = importlib.util.spec_from_file_location('bt_probe_base_launch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(probe):
    probe = probe.resolve()
    if not probe.is_relative_to(ROOT / 'out'):
        raise ValueError('Probe must be inside out/')
    record = json.loads((probe / 'manifest.json').read_text())
    if record.get('profile') != 'lemonade-bluetooth-interface-probe-v1':
        raise ValueError('Wrong probe profile')
    base = (ROOT / record['base']).resolve()
    if not base.is_relative_to(ROOT / 'out/releases'):
        raise ValueError('Base must be inside out/releases/')
    if digest(base / 'manifest.json') != record['base_manifest_sha256']:
        raise ValueError('Base manifest changed')
    launch = load(base / 'launch.py')
    manifest = launch.verify(base)
    for probe_key, filename in (('kernel_sha256', 'kernel'), ('system_sha256', 'system.apfs'),
                                ('base_root_sha256', 'root.apfs')):
        if record[probe_key] != manifest['files'][filename]['sha256']:
            raise ValueError('Probe does not match its verified base')
    if (digest(probe / 'root.apfs') != record['root_sha256'] or
            (probe / 'root.apfs').stat().st_size != record['bytes']):
        raise ValueError('Probe image changed')
    if (not record.get('normal_base_unchanged') or not record.get('all_other_entries_verified') or
            not record.get('trace_receipt', {}).get('observation_only')):
        raise ValueError('Probe does not have an observation-only build receipt')
    baseline = json.loads((ROOT / 'local/lemonade/baseline.json').read_text())
    if baseline['hardware_serial_sha256'] != manifest['hardware_serial_sha256']:
        raise ValueError('Base is bound to a different phone')
    return base, launch, manifest, record


def sanitize(raw):
    raw = re.sub(rb'(?i)(?<![0-9a-f])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f])',
                 b'[radio address omitted]', raw)
    return b'\n'.join(b'[identity or credential detail omitted]' if re.search(
        rb'(?i)\b(?:ssid|bssid|passphrase|password|link.key|pairing.key)\b', line)
        else line for line in raw.splitlines()) + b'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='new private evidence directory')
    parser.add_argument('--seconds', type=int, default=120)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if not 60 <= args.seconds <= 180:
        parser.error('--seconds must be 60..180')
    base, launch, manifest, record = verify(args.probe)
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'local/lemonade') or output.exists():
        parser.error('--output must be a new directory inside local/lemonade/')
    if args.verify_only:
        print('Probe, shipped base, exact target binding and checksums verified; no device access.')
        return
    os.umask(0o077)
    output.mkdir(parents=True)
    state = {'profile': record['profile'], 'hardware_verified': False,
             'bluetooth_usable': False, 'probe_root_sha256': record['root_sha256'],
             'kernel_sha256': record['kernel_sha256'], 'duration_requested': args.seconds,
             'phase': 'Android baseline', 'android_restored': False}
    def evidence():
        (output / 'run.json').write_text(json.dumps(state, indent=2) + '\n')
    def android_check():
        result = subprocess.run(['python3', str(ROOT / 'devices/oneplus-9/tools/lemonade-verify-android.py'), '--timeout', '30'],
            capture_output=True, text=True, timeout=570)
        if result.returncode:
            raise RuntimeError('Exact Android baseline readback did not pass')
        (output / ('android-' + ('after' if state['phase'] == 'Android restore' else 'before') + '.log')).write_text(result.stdout)
    def collect(serial, name):
        path = output / name
        try:
            launch.fb(serial, 'oem', 'log', timeout=10)
            launch.fb(serial, 'get_staged', path, timeout=30)
        finally:
            if path.exists():
                path.write_bytes(sanitize(path.read_bytes()))
    try:
        android = launch.adb_target(manifest)
        if not android:
            raise RuntimeError('Exact phone must be in Android with USB debugging before this probe')
        android_check()
        state['phase'] = 'RAM loader'; evidence()
        launch.command(['adb', '-s', android, 'reboot', 'bootloader'])
        serial = launch.wait_for(lambda: launch.fastboot_target(manifest), 60)
        if not serial or launch.product(serial) not in ('lemonade', 'OnePlus9', 'LE2110', 'lahaina'):
            raise RuntimeError('Exact Android bootloader did not become ready')
        launch.fb(serial, 'boot', base / 'boot.img', timeout=60)
        loader_identity = 'lemonade-' + manifest['hardware_serial_sha256'][:16]
        serial = launch.wait_for(lambda: (found if (found := launch.fastboot_target(manifest)) == loader_identity else None), 40)
        if not serial or launch.product(serial) != 'xnuloader':
            raise RuntimeError('Exact RAM loader did not become ready')
        if 'running' in launch.fb(serial, 'getvar', 'result'):
            raise RuntimeError('RAM loader is already running a session')
        state['phase'] = 'RAM upload'; evidence()
        print('Uploading the verified System and independent Bluetooth observation image.', flush=True)
        launch.fb(serial, 'oem', 'ramdisk')
        launch.fb(serial, 'stage', base / 'system.apfs', timeout=300)
        payload = output / 'boot.bin'
        arguments = manifest['boot_arguments'] + ['loader_return_after=' + str(args.seconds),
                                                   'epoch=' + str(int(time.time()))]
        launch.command(['python3', str(base / 'mkpayload.py'), '-o', str(payload),
            'kernel=' + str(base / 'kernel'), 'ramdisk=' + str(args.probe.resolve() / 'root.apfs'),
            '--cmdline', ' '.join(arguments)], timeout=60)
        if payload.stat().st_size > 0x03f00000:
            raise RuntimeError('Probe exceeds the loader payload limit')
        state['payload_sha256'] = digest(payload); state['boot_arguments'] = arguments
        launch.fb(serial, 'stage', payload, timeout=60)
        launch.fb(serial, 'oem', 'run', '0')
        state['phase'] = 'observation'; evidence()
        start = time.monotonic()
        for index in range(args.seconds // 15 + 1):
            time.sleep(max(0, start + index * 15 - time.monotonic()))
            result = launch.fb(serial, 'getvar', 'result', timeout=10)
            collect(serial, 'native-' + str(index) + '.log')
            print('Bluetooth interface observation: ' + str(index * 15) + ' seconds.', flush=True)
            if 'returned' in result:
                break
        launch.stop(manifest)
        collect(serial, 'native-final.log')
        state['result'] = launch.fb(serial, 'getvar', 'result')
        if not re.search(r'result: returned \d+ 0(?:\s|$)', state['result']):
            raise RuntimeError('Probe did not return cleanly')
        state['phase'] = 'Android restore'; evidence()
        launch.stop(manifest, android=True)
        android_check(); state['android_restored'] = True
        trace = b'\n'.join(p.read_bytes() for p in output.glob('native-*.log'))
        lines = list(dict.fromkeys(line for line in trace.splitlines() if b'bt-trace:' in line))
        (output / 'interface-only.log').write_bytes(b'\n'.join(lines) + b'\n')
        state['trace_line_count'] = len(lines)
        state['probe_loaded'] = b'probe loaded; observation only' in trace
        if not state['probe_loaded']:
            state['phase'] = 'probe injection unconfirmed'
            raise RuntimeError('Probe injection was not observed; Android baseline restored')
        state['phase'] = 'observation complete'
        print('Observation complete; Android baseline restored. Bluetooth usability is still unverified.')
    finally:
        evidence()


if __name__ == '__main__':
    try:
        main()
    except (subprocess.SubprocessError, OSError, ValueError, RuntimeError) as error:
        # Subprocess exceptions include private target IDs; never print argv.
        raise SystemExit('Bluetooth observation stopped (' + type(error).__name__ +
                         '). Inspect the private phase record; no recovery success is implied.')
