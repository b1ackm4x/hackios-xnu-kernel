#!/usr/bin/env python3
"""Read-only Wi-Fi hardware inventory of the baseline-matched OnePlus 9 USB target.

Acquire the Device Hub lease before running. No SSIDs, MAC addresses, network
credentials, calibration contents or complete Android property lists are read.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def run(argv, timeout=25):
    result = subprocess.run(argv, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Hardware query failed (exit {result.returncode})")
    return result.stdout


def target():
    expected = json.loads((ROOT / 'local/lemonade/baseline.json').read_text())['hardware_serial_sha256']
    for line in run(['adb', 'devices', '-l']).decode().splitlines()[1:]:
        parts = line.split()
        if len(parts) < 3 or parts[1] != 'device':
            continue
        usb = parts[2].startswith('usb:') or re.fullmatch(r'\d+-\d+(?:\.\d+)*', parts[2])
        if usb and hashlib.sha256(parts[0].encode()).hexdigest() == expected:
            return parts[0]
    raise RuntimeError('Baseline-matched OnePlus 9 is not available over USB')


def main():
    os.umask(0o077)
    serial = target()

    def shell(script, root=False):
        # adb joins shell argv with spaces. Quote the complete remote script
        # ourselves, so sh -c cannot accidentally execute a bare getprop.
        remote = ('su -c ' if root else 'sh -c ') + shlex.quote(script)
        return run(['adb', '-s', serial, 'exec-out', remote]).decode(errors='replace').strip()

    props = {key: shell('getprop ' + key) for key in (
        'ro.product.model', 'ro.soc.model', 'sys.boot_completed')}
    if props != {'ro.product.model': 'LE2110', 'ro.soc.model': 'SM8350', 'sys.boot_completed': '1'}:
        raise RuntimeError('Unexpected Android model, SoC or boot state')
    if shell('id -u', root=True) != '0':
        raise RuntimeError('Root is required for the read-only hardware inventory')
    record = {'collected_at': datetime.now().astimezone().isoformat(),
              'usb_identity_verified': True, 'android': props,
              'kernel': shell('uname -r'), 'pci': []}
    base = '/sys/bus/pci/devices/'
    nodes = shell('ls -1 ' + base, root=True).splitlines()
    fields = ('vendor', 'device', 'class', 'revision', 'subsystem_vendor',
              'subsystem_device', 'resource', 'current_link_speed', 'current_link_width')
    for node in nodes:
        if not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]', node):
            raise RuntimeError('Unexpected PCI node name')
        prefix = base + node
        entry = {'bdf': node}
        for field in fields:
            entry[field] = shell('cat ' + prefix + '/' + field + ' 2>/dev/null || true', root=True)
        entry['driver'] = shell('readlink ' + prefix + '/driver || true', root=True).split('/')[-1]
        entry['runtime_status'] = shell('cat ' + prefix + '/power/runtime_status 2>/dev/null || true', root=True)
        entry['iommu_group'] = shell('readlink ' + prefix + '/iommu_group || true', root=True).split('/')[-1]
        record['pci'].append(entry)
    endpoint = [p for p in record['pci'] if p['vendor'] == '0x17cb' and p['device'] == '0x1103']
    if len(endpoint) != 1:
        raise RuntimeError('Expected exactly one Qualcomm 17cb:1103 Wi-Fi endpoint')
    record['wlan_device'] = shell('readlink -f /sys/class/net/wlan0/device', root=True)
    record['wlan_operstate'] = shell('cat /sys/class/net/wlan0/operstate', root=True)
    record['mhi_nodes'] = shell('ls -1 /sys/bus/mhi/devices 2>/dev/null || true', root=True).splitlines()
    record['endpoint_files'] = shell('ls -1 ' + base + endpoint[0]['bdf'], root=True).splitlines()
    record['firmware_paths'] = shell(
        "find /vendor/firmware /vendor/firmware_mnt/image -maxdepth 4 -type f "
        "\\( -iname '*wlan*' -o -iname '*amss*' -o -iname '*m3*' -o -iname '*qca*' \\) "
        "2>/dev/null || true", root=True).splitlines()
    record['board_file_hash'] = shell('sha256sum /mnt/vendor/persist/bdwlan.elf 2>/dev/null || true', root=True).split(' ')[0]
    record['battery'] = {field: shell('cat /sys/class/power_supply/battery/' + field + ' 2>/dev/null || true', root=True)
                         for field in ('capacity', 'status', 'temp', 'voltage_now')}
    directory = ROOT / 'local/lemonade/wifi'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ('android-hardware-' + datetime.now().strftime('%Y%m%dT%H%M%S') + '.json')
    path.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'saved': str(path), 'usb_identity_verified': True,
                      'pci_endpoint': endpoint[0], 'mhi_nodes': record['mhi_nodes'],
                      'wlan_operstate': record['wlan_operstate'], 'battery': record['battery']}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f'Wi-Fi inventory failed: {type(exc).__name__}' +
                         (f': {exc}' if isinstance(exc, RuntimeError) else ''))
