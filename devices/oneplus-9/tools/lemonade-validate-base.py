#!/usr/bin/env python3
"""Validate a base with a disposable diagnostic overlay, never shipped in it.

Requires the exact phone's Device Hub lease. Private network input stays in a
temporary directory and every device log is redacted before retention.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT/'devices/oneplus-9/tools'/file)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


build = load('base_build', 'lemonade-build-base.py')
network = load('network_private', 'lemonade-network.py')
wifi = load('wifi_evidence', 'lemonade-check-wifi.py')
direct = load('direct_evidence', 'lemonade-check-direct.py')
launch = build.launch


def run(args, output=None):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    if output:
        output.write_text(result.stdout+result.stderr)
    if result.returncode:
        raise SystemExit('Validation step failed: '+Path(args[1]).name+'; see its private result file')
    return result.stdout


def wait_for_loader(manifest, seconds=60):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            serial=launch.fastboot_target(manifest)
            if serial and launch.product(serial)=='xnuloader':
                return serial
        except (subprocess.TimeoutExpired, SystemExit):
            pass
        time.sleep(1)
    raise SystemExit('Exact RAM loader did not enumerate after boot; no System payload sent')


def battery_evidence(text):
    rows = set(re.findall(r'lemonade-battery: sample=(\d+) SOC=\d+% V=\d+ mV I=-?\d+ mA '
        r'T=-?\d+/100C valid=([0-9a-f]+) failures=(\d+) external=(-?\d+) '
        r'status=[0-9a-f]+ charge_state=(\d+)', text))
    samples = {int(row[0]) for row in rows}
    native = []
    for line in set(re.findall(r'^lemonade-battery-test: (.*)$', text, re.M)):
        values = dict(re.findall(r'(\w+)=(-?[0-9a-f]+)', line))
        try:
            native.append({key: int(value, 16 if key in ('valid', 'gauge_status') else 10)
                           for key, value in values.items()})
        except ValueError:
            native.append({})
    def native_valid(row):
        try:
            return (0 <= row['kernel_soc'] <= 100 and
                    row['kernel_soc'] == row['registry_soc'] == row['iops_soc'] == row['source_capacity'] and
                    row['max'] == row['source_max'] == 100 and row['telemetry'] == 1 and
                    row['stale'] == row['iops_result'] == 0 and row['valid'] & 0x18b == 0x18b and
                    row['sources'] >= 1 and row['source_present'] == 1 and row['updates'] > 0 and
                    row['external'] in (0, 1) and row['external'] == row['iops_external'] and
                    row['charge_state'] in (1, 2, 3, 4) and
                    row['registry_charging'] == row['charging'] == int(row['charge_state'] == 1) and
                    row['registry_full'] == int(row['charge_state'] == 4) and
                    row['full'] == int(row['registry_full'] or row['kernel_soc'] == 100))
        except KeyError:
            return False
    native_updates = {row.get('updates') for row in native if native_valid(row)}
    # IOLog can be absent from a captured boot ring. Require direct read-only
    # evidence from IORegistry and powerd/IOPS instead of that optional message.
    registered = len(native_updates) >= 3 and all(native_valid(row) for row in native)
    # Require real SOC, voltage, temperature, presence and charge status; logs
    # repeated in multiple ring snapshots do not count as new measurements.
    valid = all(int(flags, 16) & 0x18b == 0x18b and int(errors) == 0 and
                int(external) in (0, 1) and int(state) in (1, 2, 3, 4)
                for _, flags, errors, external, state in rows)
    stale = 'lemonade-battery: telemetry stale' in text
    return {'passed': len(samples) >= 3 and registered and valid and not stale,
            'distinct_logged_samples': len(samples), 'power_source_registered': registered,
            'distinct_native_updates': len(native_updates), 'native_samples': len(native),
            'native_interfaces_consistent': registered,
            'stale_observed': stale}


def runtime_arguments_match(args, expected):
    """Match the launcher options plus the loader's bounded battery handoff."""
    epoch = [arg for arg in args if arg.startswith('epoch=')]
    cache = [arg for arg in args if arg.startswith('lemonade_battery_pa=')]
    if len(epoch) != 1 or not re.fullmatch(r'epoch=\d+', epoch[0]):
        return False
    if 'lemonade_battery=1' in expected:
        if len(cache) != 1 or not re.fullmatch(r'lemonade_battery_pa=0x[0-9a-f]{16}', cache[0]):
            return False
        physical = int(cache[0].split('=')[1], 16)
        # Same RAM bounds and 4 KiB alignment as lemonade_battery_address_valid.
        if not 0x80000000 <= physical < 0x400000000 or physical & 0xfff:
            return False
    elif cache:
        return False
    normalized = [arg for arg in args if arg not in epoch + cache]
    return normalized == ['debug=0x8', 'rd=md0'] + expected


def bluetooth_evidence(text):
    states = set(re.findall(r'bt-client: state tick=(\d+) powered=(\d+) available=(\d+) discovery_reports=(\d+)', text))
    stopped = re.findall(r'bt-lemonade: native transport stopped TX=(\d+) RX=(\d+) clean=(\d+)', text)
    checks = {'native_powered_available': len({r[0] for r in states}) >= 10 and
              all(r[1:3] == ('1', '1') for r in states),
              'controller_firmware_ready': 'native H4 transport online; real controller firmware verified' in text,
              'uart_and_clocks_restored': 'bt-lemonade: UART stopped; root clock restored=1' in text and
                  'bt-lemonade: clock probe journal restored=1' in text,
              'no_worker_failure': not re.search(r'bt-lemonade:.*(?:failed|timed out|framing_error=[1-9])', text),
              'transport_stopped_cleanly': bool(stopped) and all(r[2] == '1' and int(r[0]) > 0 and int(r[1]) > 0 for r in stopped)}
    return {'passed': all(checks.values()), 'checks': checks, 'samples': len(states)}


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',default=launch.BASE_VERSION)
    options=parser.parse_args()
    if not re.fullmatch(r'[0-9][0-9A-Za-z.-]{0,55}',options.version):parser.error('Invalid release version')
    folder=ROOT/'out/releases'/('lemonade-'+options.version)
    evidence=ROOT/'local/lemonade'/('release-'+options.version)
    evidence.mkdir(exist_ok=True)
    manifest=json.loads((folder/'manifest.json').read_text())
    if build.digest(folder/'kernel')!=build.digest(ROOT/'out/lemonade-kernel'):
        raise SystemExit('Candidate and current diagnostic kernel differ')
    runtime=build.base.contents(folder/'root.apfs');build.validate_tree(runtime)
    net_info=json.loads((ROOT/'out/lemonade-net-test.manifest.json').read_text())
    if (net_info['sha256']!=build.digest(ROOT/'out/lemonade-net-test') or
        net_info['source_sha256']!=build.digest(ROOT/'devices/oneplus-9/tools/lemonade-net-test.m')):
        raise SystemExit('Network validator differs from its build')
    battery_info=json.loads((ROOT/'out/lemonade-battery-test.manifest.json').read_text())
    if (battery_info['sha256']!=build.digest(ROOT/'out/lemonade-battery-test') or
        battery_info['source_sha256']!=build.digest(ROOT/'devices/oneplus-9/tools/lemonade-battery-test.c')):
        raise SystemExit('Battery validator differs from its build')
    try:
        private=network.read_current(ROOT)
    except Exception:
        raise SystemExit('Could not privately obtain this phone\'s connected network; response omitted') from None
    with tempfile.TemporaryDirectory(prefix='base-validation-',dir=evidence) as temporary:
        work=Path(temporary);tree=dict(runtime)
        tree['fw/lemonade-network']=(0o100600,0,0,private)
        tree['bin/lemonade-net-test']=(0o100755,0,0,(ROOT/'out/lemonade-net-test').read_bytes())
        netjob={'Label':'com.lemonade.base-validation','ProgramArguments':['/redwood/bin/lemonade-net-test'],
                'RunAtLoad':True,'StandardOutPath':'/dev/console','StandardErrorPath':'/dev/console'}
        tree['LaunchDaemons/com.lemonade.base-validation.plist']=(0o100644,0,0,plistlib.dumps(netjob))
        tree['bin/lemonade-battery-test']=(0o100755,0,0,(ROOT/'out/lemonade-battery-test').read_bytes())
        tree['etc/base-battery-validation.jobs']=(0o100644,0,0,
            b'/redwood/bin/rwtouch wait 30\n/redwood/bin/lemonade-battery-test 10\n')
        batteryjob={'Label':'com.lemonade.base-battery-validation','RunAtLoad':True,
            'ProgramArguments':['/redwood/sbin/init','/redwood/etc/base-battery-validation.jobs'],
            'StandardOutPath':'/dev/console','StandardErrorPath':'/dev/console'}
        tree['LaunchDaemons/com.lemonade.base-battery-validation.plist']=(0o100644,0,0,plistlib.dumps(batteryjob))
        client=ROOT/'out/lemonade-bt-client'
        receipt=json.loads((client/'build.json').read_text())
        if build.digest(client/'lemonade-bt-client')!=receipt['sha256']:
            raise SystemExit('Bluetooth validator differs from its build')
        for name, expected in receipt['sources'].items():
            if build.digest(ROOT/name)!=expected:raise SystemExit('Bluetooth validator source changed')
        tree['bin/lemonade-bt-client']=(0o100755,0,0,(client/'lemonade-bt-client').read_bytes())
        btjob={'Label':'org.lemonade.bluetooth-client','UserName':'mobile','RunAtLoad':True,
               'ProgramArguments':['/redwood/bin/lemonade-bt-client'],
               'StandardOutPath':'/dev/console','StandardErrorPath':'/dev/console'}
        tree['LaunchDaemons/org.lemonade.bluetooth-client.plist']=(0o100600,0,0,plistlib.dumps(btjob))
        metal=ROOT/'out/lemonade-metal'
        for name in ('lemonade_metal_features','lemonade_metal_mps','lemonade_metal_ca','metal_drv_test'):
            tree['bin/'+name]=(0o100755,0,0,(metal/name).read_bytes())
        tree['share/lemonade_test.metallib']=(0o100644,0,0,(metal/'lemonade_test.metallib').read_bytes())
        normal=build.base.contents(ROOT/'userland/root.apfs')
        tree['bin/rwtouch']=normal['bin/rwtouch']
        jobs=(ROOT/'devices/oneplus-9/lemonade-metal-system.rc.jobs').read_bytes().replace(b'/redwood/bin/metal_props show\n',b'')
        tree['etc/base-validation.jobs']=(0o100644,0,0,jobs)
        job={'Label':'com.lemonade.base-graphics-validation','RunAtLoad':True,'ProcessType':'Interactive',
             'ProgramArguments':['/redwood/sbin/init','/redwood/etc/base-validation.jobs'],
             'StandardOutPath':'/dev/console','StandardErrorPath':'/dev/console'}
        tree['LaunchDaemons/com.lemonade.base-graphics-validation.plist']=(0o100644,0,0,plistlib.dumps(job))
        image=work/'root.apfs';build.pack_tree(tree,image)
        changed=sorted(p for p,v in tree.items() if runtime.get(p)!=v)
        print('Disposable validation overlay prepared; release image unchanged.',flush=True)
        android=launch.adb_target(manifest)
        if not android:raise SystemExit('Exact Android USB target is unavailable')
        launch.android_bluetooth(manifest,android,folder=folder)
        run(['python3','devices/oneplus-9/tools/lemonade-usb.py','boot'],evidence/'loader-boot.log')
        print('Boot image accepted; waiting for the RAM loader USB interface.',flush=True)
        wait_for_loader(manifest)
        print('RAM loader identity verified; uploading System.',flush=True)
        run(['python3','devices/oneplus-9/tools/lemonade-usb.py','ramdisk'],evidence/'system-upload.log')
        before=set((ROOT/'local/lemonade').glob('*-run-cp0.manifest.json'))
        run(['python3','devices/oneplus-9/tools/lemonade-usb.py','run','--kernel','built','--boot-cpu','7','--cpus','1',
             '--userland','--wifi','dma-fault','--seconds','25'],evidence/'guard-run.log')
        after=set((ROOT/'local/lemonade').glob('*-run-cp0.manifest.json'))-before
        if len(after)!=1:raise SystemExit('Ambiguous guard record')
        guard_log=next(iter(after)).with_suffix('').with_suffix('.log')
        run(['python3','devices/oneplus-9/tools/lemonade-check-wifi.py',str(guard_log),'--record-guard'],evidence/'guard-evidence.json')
        guard=json.loads((ROOT/'local/lemonade/wifi/dma-guard-verified.json').read_text())
        if guard['kernel_sha256']!=manifest['files']['kernel']['sha256']:raise SystemExit('Guard kernel mismatch')
        # Reload host metadata: verification can take minutes, during which a
        # host-only launcher/documentation correction must not be overwritten.
        current=json.loads((folder/'manifest.json').read_text())
        for name in ('kernel','root.apfs','system.apfs','boot.img'):
            if current['files'][name]!=manifest['files'][name] or build.digest(folder/name)!=current['files'][name]['sha256']:
                raise SystemExit('Runtime package changed during device validation')
        manifest=current
        manifest.update(dma_guard_verified=True,dma_guard_kernel_sha256=guard['kernel_sha256'])
        (folder/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');build.checksums(folder)
        print('Exact-kernel DMA guard passed; starting combined GPU/display/touch/Wi-Fi validation.',flush=True)
        serial=launch.loader(manifest)
        seconds=180
        args=launch.BASE_ARGUMENTS+[f'loader_return_after={seconds}',f'epoch={int(time.time())}',
            'touch_lat=2','cpuload=500',f'rec_secs={seconds}','rec_mb=64','rec_ring=1','rec_step=3']
        payload=work/'boot.bin'
        run(['python3','xnuloader/mkpayload.py','-o',str(payload),'kernel='+str(folder/'kernel'),
             'ramdisk='+str(image),'--cmdline',' '.join(args)])
        record={'device':'lemonade','kernel_sha256':manifest['files']['kernel']['sha256'],
                'payload_sha256':build.digest(payload),'cmdline':' '.join(args),'seconds':seconds,
                'wifi':'station','network_test':True,'dma_guard':guard,
                'wifi_firmware':{'firmware':manifest['wifi_firmware'],'network_private':True,'network_reconnect_test':True},
                'display':'direct','display_fences':True,'gpu_enabled':True,'touch':'on','performance_metrics':True,
                'display_return_drain':manifest.get('display_return_drain',False),
                'gpu_return_call_drain':manifest.get('gpu_return_call_drain',False),
                'display_hz_requested':120,
                'base_root_sha256':manifest['files']['root.apfs']['sha256'],'validation_root_sha256':build.digest(image),
                'validation_added_paths':changed,'live_logs':[],'network_identity_redacted':True,
                'battery_validator':battery_info,'bluetooth_validator':receipt}
        launch.fb(serial,'stage',payload,timeout=60)
        launch.fb(serial,'oem','run','0')
        start=time.monotonic()
        while time.monotonic()-start<seconds+8:
            # Native service logging can fill the 1 MiB ring between slow
            # samples. Capture frequently to retain retirement evidence.
            time.sleep(0.25)
            path=evidence/(str(time.time_ns())+'-combined-live.log')
            try:
                launch.fb(serial,'oem','log',timeout=10)
                launch.fb(serial,'get_staged',path,timeout=20)
            finally:
                if path.exists():path.write_bytes(network.redact(path.read_bytes(),private))
            record['live_logs'].append({'path':str(path.relative_to(ROOT)),'sha256':build.digest(path)})
        result=launch.fb(serial,'getvar','result')
        launch.fb(serial,'oem','log')
        path=evidence/(str(int(time.time()))+'-combined.log')
        try:launch.fb(serial,'get_staged',path,timeout=30)
        finally:
            if path.exists():path.write_bytes(network.redact(path.read_bytes(),private))
        record.update(result=result,log_sha256=build.digest(path))
        path.with_suffix('.manifest.json').write_text(json.dumps(record,indent=2)+'\n')
        live=[(ROOT/i['path']).read_bytes() for i in record['live_logs']]
        d=direct.check(record,path.read_bytes(),live)
        (evidence/'combined-direct-evidence.json').write_text(json.dumps(d,indent=2)+'\n')
        joined=b'\n'.join([*live,path.read_bytes()])
        aggregate=dict(record,log_sha256=hashlib.sha256(joined).hexdigest())
        w=wifi.check(aggregate,joined)
        (evidence/'combined-wifi-evidence.json').write_text(json.dumps(w,indent=2)+'\n')
        text=joined.decode(errors='replace')
        metal_checks={name: f'lemonade-metal: {name} summary checks={count} failed=0' in text
                      for name,count in (('feature',103),('MPS',2),('CA',4))}
        battery=battery_evidence(text)
        bluetooth=bluetooth_evidence(text)
        acceptance={'passed':d['passed'] and w['passed'] and all(metal_checks.values()) and battery['passed'] and bluetooth['passed'] and
                    'init: runtime setup complete; failures=0' in text and '=== 36 checks' not in text,
                    'kernel_sha256':record['kernel_sha256'],'base_root_sha256':record['base_root_sha256'],
                    'metal_checks':metal_checks,'direct_passed':d['passed'],'wifi_passed':w['passed'], 'battery':battery,
                    'bluetooth':bluetooth,
                    'manifest_path':str(path.with_suffix('.manifest.json').relative_to(ROOT)),
                    'manifest_sha256':build.digest(path.with_suffix('.manifest.json'))}
        (evidence/'combined-acceptance.json').write_text(json.dumps(acceptance,indent=2)+'\n')
        print(json.dumps({'log':str(path),'direct_passed':d['passed'],'direct_frames':d['completed_direct_frames'],
                          'wifi_passed':w['passed'],'networking_verified':w['networking_verified'],
                          'battery_passed':battery['passed'],
                          'bluetooth_passed':bluetooth['passed'],
                          'runtime_setup_complete':'init: runtime setup complete; failures=0' in text,
                          'runtime_boot_self_tests_absent':'=== 36 checks' not in text},indent=2),flush=True)
        if not acceptance['passed']:
            raise SystemExit('Combined validation did not pass; inspect the evidence before accepting the base')
        current=json.loads((folder/'manifest.json').read_text())
        for name in ('kernel','root.apfs','system.apfs','boot.img'):
            if current['files'][name]!=manifest['files'][name] or build.digest(folder/name)!=current['files'][name]['sha256']:
                raise SystemExit('Runtime package changed before recording acceptance')
        current.update(integration_verified=True,integration_display_hz=120,integration_kernel_sha256=record['kernel_sha256'],
                       integration_root_sha256=record['base_root_sha256'],
                       bluetooth_coexistence_verified=True,bluetooth_kernel_sha256=record['kernel_sha256'],
                       bluetooth_root_sha256=record['base_root_sha256'],
                       integration_evidence_sha256=build.digest(evidence/'combined-acceptance.json'))
        (folder/'manifest.json').write_text(json.dumps(current,indent=2)+'\n');build.checksums(folder)
    print('Disposable overlay and network input removed; release package contains neither.',flush=True)


if __name__=='__main__':
    main()
