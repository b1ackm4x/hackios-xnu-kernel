#!/usr/bin/env python3
"""Package this phone's verified private Bluetooth inputs into a new RAM image."""
import argparse
import importlib.util
import json
import os
import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bt_firmware_base', ROOT / 'tools/lemonade-build-base.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
digest = builder.digest


def add_injection(entries, line):
    mode, uid, gid, conf = entries['etc/inject.conf']
    if line not in conf.splitlines():
        entries['etc/inject.conf'] = (mode, uid, gid, conf.rstrip(b'\n') + b'\n' + line + b'\n')


def runtime_entries(before, system, identity, args):
    """Build verified Bluetooth entries; diagnostic options are explicit."""
    stock = ROOT / 'local/lemonade/bluetooth-20261026/stock-firmware'
    record = json.loads((stock / 'manifest.json').read_text())
    kernel = json.loads((ROOT / 'out/lemonade-kernel.manifest.json').read_text())
    if not record['device_serial_sha256'] == identity:
        raise ValueError('Firmware and base are bound to different phones')
    if kernel.get('sha256') != digest(ROOT / 'out/lemonade-kernel') or 'firmware' not in kernel.get('bluetooth_modes_supported', []):
        raise ValueError('Current kernel has no verified firmware mode build')
    for relative, expected in json.loads((ROOT / 'out/lemonade-build-inputs.json').read_text()).items():
        if digest(ROOT / 'xnu/files' / relative) != expected:
            raise ValueError('Kernel source changed after build: ' + relative)
    actual = record['actual_controller']
    if (actual['product'], actual['rom'], actual['soc']) != (0x13, 0x100, 0x400c0110):
        raise ValueError('Unsupported controller identity')
    entries = dict(before)
    inputs = {}
    for name, item in record['files'].items():
        if '/' in name or not item.get('device_hash_verified') or digest(stock / name) != item['sha256']:
            raise ValueError('Invalid stock firmware input')
        if name.endswith('.ver'):
            continue
        data = (stock / name).read_bytes()
        if name == 'hpbtfw10.tlv':
            if (len(data) < 28 or data[0] != 1 or int.from_bytes(data[1:4], 'little') != len(data) - 4 or
                int.from_bytes(data[16:18], 'little') != actual['product'] or
                int.from_bytes(data[18:20], 'little') != actual['rom']):
                raise ValueError('Patch does not match the physical controller')
        entries['fw/' + name] = (0o100600, 0, 0, data)
        inputs[name] = item
    address = stock / 'controller-address.bin'
    if address.stat().st_size != 6 or digest(address) != record['address_sha256']:
        raise ValueError('Private controller address changed')
    entries['fw/bt-address'] = (0o100600, 0, 0, address.read_bytes())
    native_changes = set()
    native_services = {}
    trace_record = None
    bootstrap_record = None
    if args.board_bootstrap:
        bootstrap = ROOT / 'out/lemonade-bt-bootstrap'
        bootstrap_record = json.loads((bootstrap / 'build.json').read_text())
        if (bootstrap_record.get('profile') != 'lemonade-native-bluetooth-bootstrap-v1' or
                not bootstrap_record.get('requires_kernel_verified_firmware') or
                digest(bootstrap / 'lemonade-bt-bootstrap') != bootstrap_record['sha256']):
            raise ValueError('Board bootstrap binary is not verified')
        for name, value in bootstrap_record['sources'].items():
            if digest(ROOT / name) != value:
                raise ValueError('Board bootstrap source changed after build')
        entries['bin/lemonade-bt-bootstrap'] = (0o100755, 0, 0, (bootstrap / 'lemonade-bt-bootstrap').read_bytes())
        native_changes.add('bin/lemonade-bt-bootstrap')
    if args.native_trace:
        trace = ROOT / 'out/lemonade-bt-trace'
        receipt = json.loads((trace / 'build.json').read_text())
        trace_record = receipt
        if not receipt.get('observation_only') or digest(trace / 'liblemonade-bt-trace.dylib') != receipt['sha256']:
            raise ValueError('Unverified trace library')
        for name, value in receipt['sources'].items():
            if digest(ROOT / name) != value:
                raise ValueError('Trace sources changed')
        entries['lib/liblemonade-bt-trace.dylib'] = (0o100755, 0, 0, (trace / 'liblemonade-bt-trace.dylib').read_bytes())
        add_injection(entries, b'/redwood/lib/liblemonade-bt-trace.dylib bluetoothd BlueTool')
        native_changes.update({'lib/liblemonade-bt-trace.dylib', 'etc/inject.conf'})
    if args.native:
        container = builder.base.apfs.Container(str(system))
        try:
            volume = builder.base.apfs.pick_volume(container, None)
            # Native BluetoothSettings synchronously asks NanoRegistry for its
            # paired-watch list, even on a fresh image with no paired watch.
            # Keep the original service contract so Settings can complete that
            # request; do not synthesize an empty registry or replace the UI.
            for label in ('com.apple.bluetoothd', 'com.apple.BlueTool', 'com.apple.bluetoothuserd',
                          'com.apple.nanoregistryd', 'com.apple.nanoregistrylaunchd'):
                plist = ROOT / 'ios165/rootfs/System/Library/LaunchDaemons' / (label + '.plist')
                job = plistlib.loads(plist.read_bytes())
                program = job.get('Program') or job['ProgramArguments'][0]
                stock_program = ROOT / 'ios165/rootfs' / program.lstrip('/')
                found = volume.lookup(program)
                if job['Label'] != label or not found or volume.inode(found[0]).contents() != stock_program.read_bytes():
                    raise ValueError('Native Bluetooth program changed')
                job['RunAtLoad'] = True; job['ThrottleInterval'] = 10
                job['StandardOutPath'] = job['StandardErrorPath'] = '/dev/console'
                if label == 'com.apple.bluetoothd':
                    environment = dict(job.get('EnvironmentVariables', {}))
                    environment.update({'BT_HCI_TRANSPORT': 'H4:POSIX:com.apple.uart.bluetooth,115200',
                                        'BT_BAUDRATE': '115200'})
                    job['EnvironmentVariables'] = environment
                if label == 'com.apple.BlueTool' and args.board_bootstrap:
                    # This is our board daemon, not an Apple platform binary.
                    # Preserve only the native client's Mach service contract;
                    # never give an ad-hoc executable an Apple launchd identity.
                    job = {'Label': 'org.lemonade.bluetooth-bootstrap', 'UserName': 'mobile',
                           'ProgramArguments': ['/redwood/bin/lemonade-bt-bootstrap'],
                           'MachServices': {'com.apple.BlueTool': True}, 'RunAtLoad': True,
                           'ThrottleInterval': 10, 'StandardOutPath': '/dev/console',
                           'StandardErrorPath': '/dev/console'}
                entry = 'LaunchDaemons/' + job['Label'] + '.plist'
                entries[entry] = (0o100600, 0, 0, plistlib.dumps(job)); native_changes.add(entry)
                native_services[label] = {'program_sha256': digest(stock_program), 'stock_job_sha256': digest(plist)}
        finally:
            container.close()
    client_record = None
    compat_record = None
    ui_record = None
    if args.ui_trace:
        ui_dir = ROOT / 'out/lemonade-bt-ui-trace'
        ui_record = json.loads((ui_dir / 'build.json').read_text())
        if (ui_record.get('profile') != 'lemonade-native-bluetooth-ui-trace-v1' or
                not ui_record.get('observation_only') or not ui_record.get('bounded') or
                digest(ui_dir / 'liblemonade-bt-ui-trace.dylib') != ui_record['sha256']):
            raise ValueError('Unverified UI observation library')
        for name, value in ui_record['sources'].items():
            if digest(ROOT / name) != value:
                raise ValueError('UI observation source changed after build')
        entries['lib/liblemonade-bt-ui-trace.dylib'] = (0o100755, 0, 0, (ui_dir / 'liblemonade-bt-ui-trace.dylib').read_bytes())
        add_injection(entries, b'/redwood/lib/liblemonade-bt-ui-trace.dylib Preferences')
        native_changes.update({'lib/liblemonade-bt-ui-trace.dylib', 'etc/inject.conf'})
    if args.board_compat:
        compat = ROOT / 'out/lemonade-bt-compat'
        compat_record = json.loads((compat / 'build.json').read_text())
        if (compat_record.get('profile') != 'lemonade-native-bluetooth-compat-v1' or
                not compat_record.get('preserves_real_hci_responses') or
                digest(compat / 'liblemonade-bt-compat.dylib') != compat_record['sha256'] or
                native_services['com.apple.bluetoothd']['program_sha256'] != compat_record['native_sha256']):
            raise ValueError('Native compatibility input mismatch')
        for name, value in compat_record['sources'].items():
            if digest(ROOT / name) != value:
                raise ValueError('Native compatibility source changed after build')
        entries['lib/liblemonade-bt-compat.dylib'] = (0o100755, 0, 0, (compat / 'liblemonade-bt-compat.dylib').read_bytes())
        add_injection(entries, b'/redwood/lib/liblemonade-bt-compat.dylib bluetoothd')
        native_changes.update({'lib/liblemonade-bt-compat.dylib', 'etc/inject.conf'})
    if args.native_client or args.settings_launch:
        client_dir = ROOT / 'out/lemonade-bt-client'
        client_record = json.loads((client_dir / 'build.json').read_text())
        if (client_record.get('profile') != 'lemonade-native-bluetooth-client-v1' or
                not client_record.get('bounded') or
                digest(client_dir / 'lemonade-bt-client') != client_record['sha256']):
            raise ValueError('Native Bluetooth test client is not verified')
        for name, value in client_record['sources'].items():
            if digest(ROOT / name) != value:
                raise ValueError('Native client source changed after build')
        entries['bin/lemonade-bt-client'] = (0o100755, 0, 0, (client_dir / 'lemonade-bt-client').read_bytes())
        job = {'Label': 'org.lemonade.bluetooth-client', 'UserName': 'mobile',
               'ProgramArguments': ['/redwood/bin/lemonade-bt-client'], 'RunAtLoad': True,
               'StandardOutPath': '/dev/console', 'StandardErrorPath': '/dev/console'}
        if args.settings_launch:
            job['ProgramArguments'].append('--settings-only')
        entries['LaunchDaemons/org.lemonade.bluetooth-client.plist'] = (0o100600, 0, 0, plistlib.dumps(job))
        native_changes.update({'bin/lemonade-bt-client', 'LaunchDaemons/org.lemonade.bluetooth-client.plist'})
    changed = sorted(k for k in entries if entries[k] != before.get(k))
    if set(changed) - {'fw/bt-address', *('fw/' + name for name in inputs), *native_changes}:
        raise ValueError('Unexpected image modification')
    image = {'profile': 'lemonade-bt-firmware-v1', 'hardware_verified': False,
             'bluetooth_usable': False, 'device_serial_sha256': record['device_serial_sha256'],
             'kernel_sha256': kernel['sha256'], 'system_sha256': digest(system),
             'firmware_inputs': inputs, 'private_address': True,
             'all_entries_verified': True, 'actual_controller': actual,
             'native_client': client_record,
             'client_mode': 'settings-only' if args.settings_launch else 'acceptance' if args.native_client else None,
             'native_ui_trace': ui_record,
             'board_bootstrap': bootstrap_record,
             'board_compat': compat_record,
             'native_trace': trace_record,
             'native_services': native_services, 'native_h4_contract': 'H4:POSIX:com.apple.uart.bluetooth,115200' if args.native else None}
    return entries, image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base', type=Path, default=ROOT / 'out/releases/lemonade-2026.10.26-base.2')
    parser.add_argument('--native', action='store_true', help='include original native services')
    parser.add_argument('--native-trace', action='store_true', help='include the development-only native HCI observer')
    parser.add_argument('--native-client', action='store_true', help='include a bounded native BluetoothManager test client')
    parser.add_argument('--settings-launch', action='store_true', help='include a one-shot Settings launcher without Bluetooth requests')
    parser.add_argument('--board-bootstrap', action='store_true', help='use the real Qualcomm kernel firmware readiness service')
    parser.add_argument('--board-compat', action='store_true', help='gate optional native initialization by real controller capabilities')
    parser.add_argument('--ui-trace', action='store_true', help='include a bounded, observation-only native Settings probe')
    args = parser.parse_args()
    if args.native_client and not args.native:
        parser.error('--native-client requires --native')
    if (args.native_trace or args.settings_launch) and not args.native:
        parser.error('--native-trace and --settings-launch require --native')
    if args.settings_launch and args.native_client:
        parser.error('--settings-launch and --native-client are separate acceptance modes')
    if args.ui_trace and not args.native_client:
        parser.error('--ui-trace requires --native-client')
    if args.board_bootstrap and not args.native:
        parser.error('--board-bootstrap requires --native')
    if args.board_compat and not (args.native and args.board_bootstrap):
        parser.error('--board-compat requires --native and --board-bootstrap')
    os.umask(0o077)
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / 'out'):
        parser.error('--output must be new and inside out/')
    source = args.base.resolve()
    baseline = json.loads((ROOT / 'local/lemonade/baseline.json').read_text())
    base = json.loads((source / 'manifest.json').read_text())
    if baseline['hardware_serial_sha256'] != base['hardware_serial_sha256']:
        raise ValueError('Base is bound to another phone')
    for filename in ('kernel', 'root.apfs', 'system.apfs'):
        if digest(source / filename) != base['files'][filename]['sha256']:
            raise ValueError('Source base changed: ' + filename)
    entries, image = runtime_entries(builder.base.contents(source / 'root.apfs'),
        source / 'system.apfs', baseline['hardware_serial_sha256'], args)
    output.mkdir(parents=True)
    builder.pack_tree(entries, output / 'root.apfs')
    image.update(root_sha256=digest(output / 'root.apfs'), bytes=(output / 'root.apfs').stat().st_size,
                 base_root_sha256=base['files']['root.apfs']['sha256'])
    (output / 'manifest.json').write_text(json.dumps(image, indent=2) + '\n')
    print('Private Bluetooth RAM image built and completely read back:', output.relative_to(ROOT))


if __name__ == '__main__':
    main()
