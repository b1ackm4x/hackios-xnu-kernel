#!/usr/bin/env python3
"""Read-only return-to-Android checks for the exact USB device in the baseline.

Keep the Device Hub lease until this finishes. Never overwrites the baseline.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def command(args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 300:
        parser.error('--timeout must be 1..300 seconds')
    os.umask(0o077)
    private = ROOT / 'local/lemonade'
    baseline = json.loads((private / 'baseline.json').read_text())
    backups = json.loads((private / 'sha256.json').read_text())
    deadline = time.monotonic() + args.timeout
    serial = None
    while time.monotonic() < deadline:
        for line in command(['adb', 'devices', '-l']).splitlines()[1:]:
            cells = line.split()
            if len(cells) < 3 or cells[1] != 'device':
                continue
            physical = cells[2].startswith('usb:') or re.fullmatch(r'\d+-\d+(?:\.\d+)*', cells[2])
            if physical and hashlib.sha256(cells[0].encode()).hexdigest() == baseline['hardware_serial_sha256']:
                serial = cells[0]
                break
        if serial:
            try:
                if command(['adb', '-s', serial, 'shell', 'getprop', 'sys.boot_completed']) == '1':
                    break
            except (subprocess.SubprocessError, OSError):
                pass
        time.sleep(2)
    else:
        raise SystemExit('Exact USB target has not completed Android boot; no success record saved')
    props = {key: command(['adb', '-s', serial, 'shell', 'getprop', key]) for key in (
        'ro.product.model', 'ro.soc.model', 'ro.boot.slot_suffix', 'ro.boot.flash.locked')}
    expected = {'ro.product.model': 'LE2110', 'ro.soc.model': 'SM8350',
                'ro.boot.slot_suffix': baseline['ro.boot.slot_suffix'],
                'ro.boot.flash.locked': baseline['ro.boot.flash.locked']}
    if props != expected:
        raise SystemExit('Android identity, slot or lock state differs from the baseline')
    slot = props['ro.boot.slot_suffix']
    if slot not in ('_a', '_b'):
        raise SystemExit('Unexpected slot; no partition checks attempted')
    checks = {}
    for name in ('boot', 'vendor_boot', 'dtbo', 'vbmeta', 'xbl'):
        partition = name + slot
        remote = 'su -c ' + shlex.quote('sha256sum /dev/block/by-name/' + partition)
        value = None
        for attempt in range(3):
            output = command(['adb', '-s', serial, 'exec-out', remote], timeout=90).split()
            if output and re.fullmatch(r'[0-9a-fA-F]{64}', output[0]):
                value = output[0].lower()
                break
            if attempt < 2:
                time.sleep(2)
        if value is None:
            raise SystemExit(f'No valid root checksum for {partition}; no success record saved')
        checks[partition] = value == backups[partition + '.img']['sha256']
    record = {'checked_at': datetime.now().astimezone().isoformat(), 'sys.boot_completed': '1',
              'slot': slot, 'flash_locked': props['ro.boot.flash.locked'], 'usb_identity_verified': True,
              'active_partition_hashes_match_backup': checks, 'passed': all(checks.values())}
    path = private / ('android-roundtrip-' + datetime.now().strftime('%Y%m%dT%H%M%S') + '.json')
    path.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))
    print(f'Saved: {path}')
    if not record['passed']:
        raise SystemExit('Partition hash mismatch; inspect the record before continuing')


if __name__ == '__main__':
    try:
        main()
    except (subprocess.SubprocessError, OSError) as exc:
        raise SystemExit(f'Android readback failed ({type(exc).__name__}); no successful roundtrip is confirmed')
