#!/usr/bin/env python3
"""Verify the original Bluetooth Settings list with the combined base enabled.

Requires the exact device lease, current-kernel DMA and combined acceptance,
and the matching phone booted in Android. Diagnostics
are added only to a temporary image; no test client enters the release package.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('base_validation', ROOT/'tools/lemonade-validate-base.py')
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)
build, launch = validation.build, validation.launch


def discovery_evidence(text):
    bluetooth = validation.bluetooth_evidence(text)
    checks = dict(bluetooth['checks'])
    checks.update({
        'original_settings_controller': 'class=BTSDevicesController loaded=1' in text,
        'settings_foreground': bool(re.search(r'bt-ui: main queue tick=\d+ state=0 scenes=1', text)),
        'visible_device_row': bool(re.search(r'bt-ui: table section=1 rows=[1-9]\d*', text)),
        'native_discovery_notification': bool(re.search(r'bt-client: state tick=\d+ powered=1 available=1 discovery_reports=[1-9]\d*', text)),
        'wifi_station_enabled': 'wifi-lemonade: stage 24 passed;' in text and
            'wifi-lemonade: station offered to iOS; runtime kept until normal stop' in text,
        'native_service_stable': not re.search(r'com.apple.bluetoothd.*exited', text),
        'no_kernel_panic': not re.search(r'panic\(cpu|Debugger called: <panic>|Debugger message:\s*panic', text),
    })
    return {'passed': all(checks.values()), 'checks': checks}


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default=launch.BASE_VERSION)
    options=parser.parse_args()
    if not re.fullmatch(r'[0-9][0-9A-Za-z.-]{0,55}', options.version):parser.error('Invalid release version')
    folder=ROOT/'out/releases'/('lemonade-'+options.version)
    evidence=ROOT/'local/lemonade'/('release-'+options.version)
    manifest=json.loads((folder/'manifest.json').read_text())
    for name in ('kernel','root.apfs','system.apfs','boot.img'):
        if build.digest(folder/name)!=manifest['files'][name]['sha256']:
            raise SystemExit('Runtime package changed: '+name)
    if (not manifest.get('integration_verified') or not manifest.get('bluetooth_coexistence_verified') or
            manifest.get('integration_kernel_sha256')!=manifest['files']['kernel']['sha256'] or
            manifest.get('integration_root_sha256')!=manifest['files']['root.apfs']['sha256']):
        raise SystemExit('Complete the matching combined validation first')
    build.usb.verified_firmware_overlay(manifest['files']['kernel']['sha256'])
    runtime=build.base.contents(folder/'root.apfs')
    build.validate_tree(runtime)
    bluetooth=build.module('bt_validation_image','lemonade-build-bt-firmware.py')
    options=argparse.Namespace(native=True,board_bootstrap=True,board_compat=True,
        native_trace=False,native_client=True,settings_launch=False,ui_trace=True)
    tree, receipt=bluetooth.runtime_entries(runtime,folder/'system.apfs',manifest['hardware_serial_sha256'],options)
    changed=sorted(p for p,v in tree.items() if v!=runtime.get(p))
    allowed={'bin/lemonade-bt-client','LaunchDaemons/org.lemonade.bluetooth-client.plist',
             'lib/liblemonade-bt-ui-trace.dylib','etc/inject.conf'}
    if set(changed)!=allowed or set(runtime)-set(tree):
        raise SystemExit('Unexpected difference from the accepted runtime')
    run_id=str(int(time.time()))
    with tempfile.TemporaryDirectory(prefix='bluetooth-validation-',dir=evidence) as temporary:
        work=Path(temporary)
        image=work/'root.apfs';build.pack_tree(tree,image)
        seconds=180
        args=launch.BASE_ARGUMENTS+['lemonade_bt_ui=1',f'loader_return_after={seconds}',
            f'epoch={int(time.time())}',f'rec_secs={seconds}','rec_mb=64','rec_ring=1','rec_step=3']
        payload=work/'boot.bin'
        validation.run(['python3','xnuloader/mkpayload.py','-o',str(payload),
            'kernel='+str(folder/'kernel'),'ramdisk='+str(image),'--cmdline',' '.join(args)])
        if payload.stat().st_size>0x03f00000:raise SystemExit('Payload exceeds RAM loader limit')
        android=launch.adb_target(manifest)
        if not android:raise SystemExit('Return the exact phone to Android before Bluetooth acceptance')
        launch.android_bluetooth(manifest,android,folder=folder)
        validation.run(['python3','tools/lemonade-usb.py','boot'],evidence/(run_id+'-bt-ui-boot.log'))
        print('Waiting for the cold RAM loader and System upload.',flush=True)
        validation.wait_for_loader(manifest)
        validation.run(['python3','tools/lemonade-usb.py','ramdisk'],evidence/(run_id+'-bt-ui-system.log'))
        serial=launch.loader(manifest)
        launch.fb(serial,'stage',payload,timeout=60)
        launch.fb(serial,'oem','run','0')
        print('Original Bluetooth Settings acceptance started with the complete base.',flush=True)
        started=time.monotonic();live=[]
        while time.monotonic()-started<seconds+8:
            time.sleep(0.5)
            path=evidence/(run_id+'-bt-ui-'+str(len(live))+'.log')
            launch.fb(serial,'oem','log',timeout=10)
            try:
                launch.fb(serial,'get_staged',path,timeout=20)
            finally:
                if path.exists():
                    raw=re.sub(rb'(?i)(?<![0-9a-f])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f])',
                        b'[radio address omitted]',path.read_bytes())
                    raw=b'\n'.join(b'[network identity detail omitted]' if
                        re.search(rb'(?i)\b(?:ssid|bssid|passphrase|password)\b',line) else line for line in raw.splitlines())
                    path.write_bytes(raw)
            live.append(path)
        result=launch.fb(serial,'getvar','result')
        text='\n'.join(p.read_text(errors='replace') for p in live)
        proof=discovery_evidence(text)
        proof['checks']['returned_cleanly']=bool(re.search(r'result: returned \d+ 0(?:\s|$)',result))
        proof['passed']=all(proof['checks'].values())
        proof.update(kernel_sha256=manifest['files']['kernel']['sha256'],
            root_sha256=manifest['files']['root.apfs']['sha256'],validation_root_sha256=build.digest(image),
            cmdline=' '.join(args),added_paths=changed,diagnostic_receipt=receipt,
            live_logs=[{'path':str(p.relative_to(ROOT)),'sha256':build.digest(p)} for p in live])
        record=evidence/(run_id+'-bluetooth-discovery.json')
        record.write_text(json.dumps(proof,indent=2)+'\n')
        print(json.dumps({'passed':proof['passed'],'checks':proof['checks'],'evidence':str(record)},indent=2),flush=True)
        if not proof['passed']:raise SystemExit('Original Settings discovery acceptance did not pass')
        current=json.loads((folder/'manifest.json').read_text())
        for name in ('kernel','root.apfs','system.apfs','boot.img'):
            if current['files'][name]!=manifest['files'][name] or build.digest(folder/name)!=current['files'][name]['sha256']:
                raise SystemExit('Runtime changed during discovery acceptance')
        current.update(bluetooth_discovery_verified=True,
            bluetooth_discovery_evidence_sha256=build.digest(record),
            bluetooth_kernel_sha256=proof['kernel_sha256'],bluetooth_root_sha256=proof['root_sha256'])
        (folder/'manifest.json').write_text(json.dumps(current,indent=2)+'\n')
        build.checksums(folder)


if __name__=='__main__':main()
