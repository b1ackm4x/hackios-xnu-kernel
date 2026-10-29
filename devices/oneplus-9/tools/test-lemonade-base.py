#!/usr/bin/env python3
"""Runtime packaging boundaries and the actual Wi-Fi integration entry point."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('base', ROOT / 'devices/oneplus-9/tools/lemonade-build-base.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


class BasePolicy(unittest.TestCase):
    def test_release_checksums_exclude_mutable_launch_state(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            manifest=self.package(folder)
            state=folder/'android-bluetooth-state.json'
            state.write_text('{"pending_restore":true}')
            base.checksums(folder)
            checksum=(folder/'SHA256SUMS').read_text()
            self.assertNotIn(state.name,checksum)
            self.assertEqual(len(checksum.splitlines()),len(manifest['files'])+1)
            state.write_text('{"pending_restore":false}')
            base.checksums(folder)
            self.assertEqual((folder/'SHA256SUMS').read_text(),checksum)
            manifest['files']['../outside']={}
            (folder/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):base.checksums(folder)

    def bootstrap_image(self, folder, login_mode=0o104755, duplicate=False):
        entries = {'': (0o40755, 0, 0, b''),
                   'private/preboot/jb-rw': (0o40755, 0, 0, b''),
                   'jb/prep.sh': (0o100755, 0, 0, b'#!/var/jb/bin/sh\n'),
                   'jb/usr/bin/login': (login_mode, 0, 0, b'login'),
                   'jb/Applications/Sileo.app/giveMeRoot': (0o104755, 0, 0, b'helper'),
                   'jb/Applications/Sileo.app/Info.plist': (0o100644, 0, 0,
                       plistlib.dumps({'CFBundleIdentifier': 'org.coolstar.SileoStore',
                                       'CFBundleShortVersionString': '2.5.1'}))}
        # Include parent directories so the real APFS round-trip compares all entries.
        for name in list(entries):
            for parent in Path(name).parents:
                if str(parent) != '.':entries.setdefault(str(parent), (0o40755, 0, 0, b''))
        if duplicate:entries['Applications'] = entries['Applications/Sileo.app'] = (0o40755, 0, 0, b'')
        image = folder / 'system.apfs'
        base.pack_tree(entries, image)
        return image

    def test_real_bootstrap_image_adds_only_registration_service(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs, receipt = base.jailbreak_services(self.bootstrap_image(Path(directory)))
            self.assertEqual(receipt['version'], '2.5.1')
            self.assertTrue(receipt['automatic_registration'])
            self.assertFalse(receipt['persistent_installs'])
            job = plistlib.loads(next(iter(jobs.values()))[3])
            self.assertEqual(job['ProgramArguments'], ['/var/jb/bin/sh', '/var/jb/prep.sh'])
            self.assertTrue(job['RunAtLoad'])
            self.assertTrue(job['AbandonProcessGroup'])
            self.assertNotIn('KeepAlive', job)
            tree = self.tree();tree.update(jobs);base.validate_tree(tree)

    def test_bootstrap_rejects_lost_privilege_and_duplicate_app(self):
        for mode, duplicate in ((0o100755, False), (0o104755, True)):
            with self.subTest(mode=mode, duplicate=duplicate), tempfile.TemporaryDirectory() as directory:
                image = self.bootstrap_image(Path(directory), mode, duplicate)
                with self.assertRaises(ValueError):base.jailbreak_services(image)

    def test_battery_acceptance_requires_distinct_valid_samples_and_service(self):
        validator=base.module('validate_base_battery','lemonade-validate-base.py')
        header='LemonadeBattery: external gauge power source registered, 100%\n'
        row='lemonade-battery: sample={} SOC=100% V=8700 mV I=-15 mA T=3500/100C valid=1ff failures=0 external=1 status=e0 charge_state=4\n'
        samples=''.join(row.format(i) for i in (1,3,6))
        native_row=('lemonade-battery-test: n={0} kernel_soc=100 stale=0 valid=1ff updates={0} '
                    'registry_soc=100 max=100 telemetry=1 iops_result=0 iops_soc=100 sources=1 '
                    'source_capacity=100 source_max=100 source_present=1 charging=0 full=1 external=1 '
                    'charge_state=4 registry_charging=0 registry_full=1 iops_external=1\n')
        native=''.join(native_row.format(i) for i in (7,8,9))
        self.assertTrue(validator.battery_evidence(samples+native)['passed'])
        self.assertTrue(validator.battery_evidence(header+samples+native)['passed'])
        for text in (header+samples, samples+native_row.format(7)*10,
                     samples+native.replace('registry_soc=100','registry_soc=99'),
                     samples+native.replace('iops_soc=100','iops_soc=99'),
                     samples+native.replace('stale=0','stale=1'),
                     samples+native.replace('source_present=1','source_present=0'),
                     samples+native.replace('iops_external=1','iops_external=0'),
                     samples+native.replace('registry_charging=0','registry_charging=1'),
                     samples+native.replace('telemetry=1','telemetry=0'),
                     native+row.format(1)*10, native+samples.replace('valid=1ff','valid=7f'),
                     native+samples.replace('failures=0','failures=1'),
                     native+samples.replace('charge_state=4','charge_state=0'),
                     native+samples+'lemonade-battery: telemetry stale; withdrawing capacity'):
            with self.subTest(text=text):self.assertFalse(validator.battery_evidence(text)['passed'])

    def test_validator_waits_for_loader_reenumeration(self):
        validator=base.module('validate_base','lemonade-validate-base.py')
        with patch.object(validator.launch,'fastboot_target',side_effect=[None,'target','target']), \
             patch.object(validator.launch,'product',side_effect=['lahaina','xnuloader']), \
             patch.object(validator.time,'sleep'):
            self.assertEqual(validator.wait_for_loader({}), 'target')
        with patch.object(validator.launch,'fastboot_target',return_value=None), \
             patch.object(validator.time,'monotonic',side_effect=[0,0,3]), \
             patch.object(validator.time,'sleep'):
            with self.assertRaises(SystemExit):validator.wait_for_loader({},seconds=2)

    def test_actual_arguments_allow_only_valid_loader_battery_handoff(self):
        validator=base.module('validate_runtime_arguments','lemonade-validate-base.py')
        actual=['debug=0x8','rd=md0','lemonade_battery_pa=0x00000000a025d000']+base.launch.BASE_ARGUMENTS+['epoch=1792976727']
        self.assertTrue(validator.runtime_arguments_match(actual,base.launch.BASE_ARGUMENTS))
        for pointer in ('0x0000000000000000','0x00000000a025d001','0x0000000400000000','bad'):
            changed=list(actual);changed[2]='lemonade_battery_pa='+pointer
            self.assertFalse(validator.runtime_arguments_match(changed,base.launch.BASE_ARGUMENTS))
        for changed in (actual[:2]+actual[3:],actual+[actual[2]],actual+['loader_return_after=90'],
                        actual+['ufs_write=1'],actual+['epoch=123'],actual[:-1]):
            self.assertFalse(validator.runtime_arguments_match(changed,base.launch.BASE_ARGUMENTS))

    def package(self, folder):
        files={}
        for name in ('boot.img','kernel','root.apfs','system.apfs','launch.py','mkpayload.py'):
            path=folder/name;path.write_bytes(name.encode())
            files[name]={'bytes':path.stat().st_size,'sha256':base.digest(path)}
        manifest={'profile':'lemonade-base-v1','boot_arguments':base.launch.BASE_ARGUMENTS,
                  'battery_telemetry_supported':base.launch.BATTERY_TELEMETRY,
                  'hardware_serial_sha256':'a'*64,'default_display_hz':120,
                  'refresh_panel':{'profile':'amb655x-60-120-v1','hardware_serial_sha256':'a'*64,
                                   'selected_panel':'qcom,mdss_dsi_samsung_amb655x_dsc_cmd:'},
                  'bluetooth':{'native_h4_contract':'H4:POSIX:com.apple.uart.bluetooth,115200', 'board_bootstrap':True, 'board_compat':True},
                  'bluetooth_discovery_verified':True,'bluetooth_coexistence_verified':True,
                  'bluetooth_kernel_sha256':files['kernel']['sha256'],'bluetooth_root_sha256':files['root.apfs']['sha256'],
                  'files':files,'dma_guard_verified':True,'dma_guard_kernel_sha256':files['kernel']['sha256'],
                  'integration_verified':True,'integration_display_hz':120,'integration_kernel_sha256':files['kernel']['sha256'],
                  'integration_root_sha256':files['root.apfs']['sha256']}
        (folder/'manifest.json').write_text(json.dumps(manifest))
        return manifest

    def test_launcher_rejects_tampering_and_wrong_guard_before_device_access(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            with patch.object(base.launch,'command',side_effect=AssertionError('Must not contact a device')):
                self.package(folder);base.launch.verify(folder)
                (folder/'kernel').write_bytes(b'other!')
                with self.assertRaises(SystemExit):base.launch.verify(folder)
                manifest=self.package(folder);manifest['dma_guard_kernel_sha256']='0'*64
                (folder/'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaises(SystemExit):base.launch.verify(folder)
                manifest=self.package(folder);manifest['integration_verified']=False
                (folder/'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaises(SystemExit):base.launch.verify(folder)
                manifest=self.package(folder);manifest['integration_root_sha256']='0'*64
                (folder/'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaises(SystemExit):base.launch.verify(folder)
                manifest=self.package(folder);manifest['boot_arguments']=['ufs_write=1']
                (folder/'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaises(SystemExit):base.launch.verify(folder)

    def test_launcher_exact_usb_identity(self):
        target='test-only-device';manifest={'hardware_serial_sha256':hashlib.sha256(target.encode()).hexdigest()}
        for location in ('usb:2-1','2-1.1'):
            with patch.object(base.launch,'command',return_value='List of devices attached\n'+target+' device '+location+' model:LE2110\n'):
                self.assertEqual(base.launch.adb_target(manifest),target)
        for row in (target+' device product:lemonade', target+' unauthorized usb:2-1', 'another-phone device usb:2-1'):
            with patch.object(base.launch,'command',return_value='List of devices attached\n'+row+'\n'):
                self.assertIsNone(base.launch.adb_target(manifest))
        for row in ('another-phone fastboot',target+' other-mode',target):
            with patch.object(base.launch,'command',return_value=row+'\n'):
                self.assertIsNone(base.launch.fastboot_target(manifest))
        with patch.object(base.launch,'command',return_value=target+' fastboot\n'):
            self.assertEqual(base.launch.fastboot_target(manifest),target)

    def test_120hz_requires_matching_panel_and_combined_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            for key,value in [('default_display_hz',60),('integration_display_hz',60),('refresh_panel',{}),
                              ('hardware_serial_sha256','b'*64)]:
                manifest=self.package(folder);manifest[key]=value
                (folder/'manifest.json').write_text(json.dumps(manifest))
                with self.subTest(key=key),self.assertRaises(SystemExit):base.launch.verify(folder)

    def test_battery_protocol_must_match_before_any_device_command(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            for protocol in (None,'bq28z610-uefi-cache-v1'):
                manifest=self.package(folder);manifest['battery_telemetry_supported']=protocol
                (folder/'manifest.json').write_text(json.dumps(manifest))
                with patch.object(base.launch,'command',side_effect=AssertionError('Must not contact a device')):
                    with self.assertRaisesRegex(SystemExit,'电池与充电状态协议'):base.launch.verify(folder)

    def tree(self):
        tree = {p: (0o100755, 0, 0, b'program') for p in ('bin/metal-enable', 'sbin/init', 'bin/lemonade-bt-bootstrap')}
        tree['etc/rc'] = (0o100644, 0, 0, b'/redwood/bin/metal-enable\nexec /sbin/launchd.ios\n')
        for n in ('amss.bin', 'm3.bin', 'board.bin', 'regdb.bin'):
            tree['fw/'+n] = (0o100644, 0, 0, b'firmware')
        for n in ('wifid', 'configd', 'mDNSResponder', 'chronod', 'geod', 'navd', 'sleepd'):
            tree['LaunchDaemons/com.apple.'+n+'.plist'] = (0o100644, 0, 0,
                plistlib.dumps({'Label':'com.apple.'+n, 'Program':'/usr/sbin/'+n}))
        for n in ('hpbtfw10.tlv', 'hpnv10.bin', 'bt-address'):
            tree['fw/'+n] = (0o100600, 0, 0, b'123456')
        tree['etc/inject.conf'] = (0o100644, 0, 0, b'/redwood/lib/liblemonade-bt-compat.dylib bluetoothd\n')
        for n in ('bluetoothd', 'bluetoothuserd', 'nanoregistryd', 'nanoregistrylaunchd'):
            tree['LaunchDaemons/com.apple.'+n+'.plist'] = (0o100600, 0, 0,
                plistlib.dumps({'Label':'com.apple.'+n, 'Program':'/usr/sbin/'+n}))
        tree['LaunchDaemons/org.lemonade.bluetooth-bootstrap.plist'] = (0o100600, 0, 0,
            plistlib.dumps({'Label':'org.lemonade.bluetooth-bootstrap',
                'ProgramArguments':['/redwood/bin/lemonade-bt-bootstrap'],
                'MachServices':{'com.apple.BlueTool':True}}))
        return tree

    def test_clean_runtime(self):
        base.validate_tree(self.tree())

    def test_apps_require_complete_offline_runtime_and_no_foreground_job(self):
        tree=self.tree()
        tree['bin/lemonade-apps']=(0o100755,0,0,b'program')
        tree['lib/apps.dylib']=(0o100755,0,0,b'library')
        for action,user in (('bootstrap','root'),('install','mobile')):
            label='org.lemonade.apps-'+action
            job={'Label':label,'UserName':user,'ProgramArguments':['/redwood/bin/lemonade-apps','--'+action],
                 'RunAtLoad':True,'ProcessType':'Interactive','EnableTransactions':True}
            tree['LaunchDaemons/'+label+'.plist']=(0o100644,0,0,plistlib.dumps(job))
        mode,uid,gid,data=tree['etc/inject.conf']
        tree['etc/inject.conf']=(mode,uid,gid,data+b'/redwood/lib/apps.dylib TrollApps Delta\n')
        base.validate_tree(tree)
        for name in ('bin/lemonade-apps','lib/apps.dylib','LaunchDaemons/org.lemonade.apps-install.plist'):
            broken=dict(tree);del broken[name]
            with self.subTest(name=name),self.assertRaises(ValueError):base.validate_tree(broken)
        broken=dict(tree);name='LaunchDaemons/org.lemonade.apps-install.plist'
        mode,uid,gid,data=broken[name];job=plistlib.loads(data);job['ProgramArguments'][-1]='--open'
        broken[name]=(mode,uid,gid,plistlib.dumps(job))
        with self.assertRaises(ValueError):base.validate_tree(broken)
        broken=dict(tree);mode,uid,gid,data=broken[name];job=plistlib.loads(data)
        job['EnableTransactions']=False
        broken[name]=(mode,uid,gid,plistlib.dumps(job))
        with self.assertRaises(ValueError):base.validate_tree(broken)

    def test_offline_apps_evidence_requires_both_registration_and_scratch_release(self):
        validator=base.module('apps_acceptance','lemonade-validate-base.py')
        applications={'system':{'applications':[{'identifier':'example.app','version':'1'}]}}
        lines=['apps-restore: stores_registered=1 offline=1',
               'apps-restore: app=example.app helper=0 registered=1',
               'apps-restore: complete=1 foreground_unchanged=1','apps-restore: scratch_unmounted=1']
        self.assertTrue(validator.applications_evidence('\n'.join(lines),applications)['passed'])
        for i in range(len(lines)):
            with self.subTest(missing=i):
                self.assertFalse(validator.applications_evidence('\n'.join(lines[:i]+lines[i+1:]),applications)['passed'])
        self.assertFalse(validator.applications_evidence('\n'.join(lines).replace('helper=0','helper=1'),applications)['passed'])

    def test_app_acceptance_is_bound_to_all_runtime_images(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);manifest=self.package(folder)
            manifest['applications']={'verified':True,**{name+'_sha256':manifest['files'][file]['sha256']
                for name,file in [('root','root.apfs'),('system','system.apfs'),('kernel','kernel')]}}
            (folder/'manifest.json').write_text(json.dumps(manifest));base.launch.verify(folder)
            for key in ('root_sha256','system_sha256','kernel_sha256','verified'):
                changed=copy.deepcopy(manifest);changed['applications'][key]=False if key=='verified' else '0'*64
                (folder/'manifest.json').write_text(json.dumps(changed))
                with self.subTest(key=key),self.assertRaises(SystemExit):base.launch.verify(folder)

    def test_injection_config_reserves_both_sysctl_terminators(self):
        prefix = b'/redwood/lib/example.dylib '
        allowed = prefix + b'A' * (510 - len(prefix))
        base.validate_injection_config(allowed + b' \n')
        for rejected in (allowed + b'A', allowed + b'\n/redwood/lib/extra.dylib Delta',
                         b'/redwood/lib/a.dylib App\0Hidden', b' ' * 4096):
            with self.subTest(length=len(rejected)), self.assertRaises(ValueError):
                base.validate_injection_config(rejected)

    def test_pack_rejects_injection_overflow_before_building_an_image(self):
        tree = self.tree()
        tree['etc/inject.conf'] = (0o100644, 0, 0, b'x' * 515 + b'\n')
        with tempfile.TemporaryDirectory() as directory, patch.object(base.subprocess, 'run') as run:
            destination = Path(directory) / 'root.apfs'
            with self.assertRaisesRegex(ValueError, 'kernel limit'):
                base.pack_tree(tree, destination)
            run.assert_not_called()
            self.assertFalse(destination.exists())

    def test_keyboard_requires_complete_service_and_exact_job(self):
        tree = self.tree()
        tree['bin/lemonade-bt-hid'] = (0o100755, 0, 0, b'helper')
        tree['lib/liblemonade-bt-hid.dylib'] = (0o100755, 0, 0, b'adapter')
        job = {'Label':'org.lemonade.bluetooth-hid', 'UserName':'root',
               'ProgramArguments':['/redwood/bin/lemonade-bt-hid'],
               'RunAtLoad':True, 'KeepAlive':True, 'ThrottleInterval':10}
        path = 'LaunchDaemons/org.lemonade.bluetooth-hid.plist'
        tree[path] = (0o100644, 0, 0, plistlib.dumps(job))
        mode, uid, gid, conf = tree['etc/inject.conf']
        tree['etc/inject.conf'] = (mode, uid, gid, conf+b'/redwood/lib/liblemonade-bt-hid.dylib bluetoothd\n')
        base.validate_tree(tree)
        for missing in ('bin/lemonade-bt-hid', 'lib/liblemonade-bt-hid.dylib', path):
            changed = dict(tree);del changed[missing]
            with self.subTest(missing=missing), self.assertRaises(ValueError):base.validate_tree(changed)
        for key, value in (('UserName','mobile'),('ProgramArguments',['/usr/bin/other']),
                           ('MachServices',{'com.apple.fake':True}),('KeepAlive',False)):
            changed = dict(tree);bad = dict(job);bad[key] = value
            changed[path] = (0o100644, 0, 0, plistlib.dumps(bad))
            with self.subTest(key=key), self.assertRaises(ValueError):base.validate_tree(changed)

    def test_keyboard_activation_is_bound_to_package(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory);manifest = self.package(folder)
            manifest['boot_arguments'] = base.launch.BASE_ARGUMENTS+['lemonade_bt_hid=1']
            (folder/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(SystemExit):base.launch.verify(folder)
            manifest['bluetooth']['board_hid'] = {'profile':'lemonade-bluetooth-keyboard-v1'}
            (folder/'manifest.json').write_text(json.dumps(manifest));base.launch.verify(folder)
            manifest['boot_arguments'] = base.launch.BASE_ARGUMENTS
            (folder/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(SystemExit):base.launch.verify(folder)

    def test_workloads_helpers_and_credentials_rejected(self):
        for p in ('bin/gl_test', 'bin/rwtouch', 'bin/lemonade-bt-client', 'lib/liblemonade-bt-trace.dylib', 'etc/rc.jobs', 'share/pattern.bin', 'fw/lemonade-network', 'share/test.metallib'):
            with self.subTest(path=p), self.assertRaises(ValueError):
                tree=self.tree();tree[p]=(0o100644,0,0,b'test');base.validate_tree(tree)

    def test_demo_and_tracing_rejected(self):
        for job in ({'Label':'com.redwood.demo'}, {'Label':'x','EnvironmentVariables':{'REDWOOD_METAL_TRACE':'1'}},
                    {'Label':'x','ProgramArguments':['/redwood/sbin/init','/redwood/etc/anything']}):
            with self.subTest(job=job), self.assertRaises(ValueError):
                tree=self.tree();tree['LaunchDaemons/test.plist']=(0o100644,0,0,plistlib.dumps(job));base.validate_tree(tree)

    def test_missing_runtime_service_rejected(self):
        tree=self.tree();del tree['LaunchDaemons/com.apple.wifid.plist']
        with self.assertRaises(ValueError):base.validate_tree(tree)

    def test_default_is_unbounded_ram_runtime(self):
        args=base.launch.BASE_ARGUMENTS
        for word in ('lemonade_runtime=1','lemonade_wifi=24','gpu=1','touchscreen=1','fb_fences=1',
                     'lemonade_display_hz=120','lemonade_panel=655','lemonade_battery=1','bootcpu=7',
                     'ufs_write=0','var_store=0','lemonade_bt=6'):
            self.assertIn(word,args)
        self.assertFalse(any(w.startswith(('loader_return_after=','touch_lat=','cpuload=','rec_','adreno_watch=')) for w in args))
        self.assertEqual(len(args),len({w.split('=')[0] for w in args}))

    def test_bluetooth_acceptance_and_binary_binding_required(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for key, value in (('bluetooth', {}), ('bluetooth_discovery_verified', False),
                               ('bluetooth_coexistence_verified', False), ('bluetooth_kernel_sha256', '0'*64),
                               ('bluetooth_root_sha256', '0'*64)):
                manifest = self.package(folder); manifest[key] = value
                (folder/'manifest.json').write_text(json.dumps(manifest))
                with self.subTest(key=key), self.assertRaises(SystemExit):base.launch.verify(folder)

    def test_android_bluetooth_restores_original_across_repeated_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory); serial='test-device'
            manifest={'hardware_serial_sha256':hashlib.sha256(serial.encode()).hexdigest()}
            setting=['1']; calls=[]
            def command(argv, **kwargs):
                text=argv[-1];calls.append(text)
                if 'settings get' in text:return setting[0]
                if 'bluetooth_manager disable' in text:setting[0]='0'
                if 'bluetooth_manager enable' in text:setting[0]='1'
                return ''
            with patch.object(base.launch, 'command', side_effect=command):
                base.launch.android_bluetooth(manifest,serial,folder=folder)
                self.assertEqual(setting[0],'0')
                base.launch.android_bluetooth(manifest,serial,folder=folder)
                base.launch.android_bluetooth(manifest,serial,restore=True,folder=folder)
                self.assertEqual(setting[0],'1')
                self.assertFalse(json.loads((folder/'android-bluetooth-state.json').read_text())['pending'])
                with self.assertRaises(SystemExit):base.launch.android_bluetooth(manifest,'wrong',folder=folder)
                self.assertEqual(len(calls),10)

    def test_restart_requires_android_roundtrip_before_any_new_payload(self):
        for result, expected_stops in (('result: running',0),('result: returned 123 0',1)):
            calls=[]
            def fb(serial,*args,**kwargs):
                calls.append(args)
                if args==('getvar','result'):return result
                self.fail('No payload or reboot may be sent before the Android roundtrip')
            with patch.object(base.launch,'fastboot_target',return_value='fixture'), \
                 patch.object(base.launch,'product',return_value='xnuloader'), \
                 patch.object(base.launch,'fb',side_effect=fb), \
                 patch.object(base.launch,'adb_target',return_value=None), \
                 patch.object(base.launch,'ensure_adb_server'), \
                 patch.object(base.launch,'stop') as stop:
                with self.assertRaises(SystemExit):base.launch.start({})
                self.assertEqual(stop.call_count,expected_stops)
                if expected_stops:stop.assert_called_once_with({},android=True)
                self.assertEqual(calls,[('getvar','result')])

    def test_failed_candidate_allows_recovery_but_rejects_tampered_files(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);manifest=self.package(folder)
            manifest['integration_verified']=False
            manifest['bluetooth_discovery_verified']=False
            (folder/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(SystemExit):base.launch.verify(folder)
            self.assertEqual(base.launch.verify(folder,require_acceptance=False),manifest)
            (folder/'kernel').write_bytes(b'changed')
            with self.assertRaises(SystemExit):base.launch.verify(folder,require_acceptance=False)

    def test_adb_start_preserves_dedicated_server_port(self):
        with patch.dict(os.environ,{'ADB_SERVER_SOCKET':'tcp:127.0.0.1:5041'}), \
             patch.object(base.launch,'command') as command:
            base.launch.ensure_adb_server()
            command.assert_called_once_with(['adb','-P','5041','start-server'],timeout=20)

    def test_bluetooth_ui_requires_real_list_notification_and_transport(self):
        validator=base.module('bluetooth_ui_acceptance','lemonade-validate-base-bluetooth.py')
        ready=('bt-lemonade: native H4 transport online; real controller firmware verified\n'
               'bt-lemonade: UART stopped; root clock restored=1\n'
               'bt-lemonade: clock probe journal restored=1\n'
               'bt-lemonade: native transport stopped TX=85 RX=140 clean=1\n'
               'bt-ui: controller depth=3 class=BTSDevicesController loaded=1\n'
               'bt-ui: main queue tick=1 state=0 scenes=1\n'
               'bt-ui: table section=1 rows=1\n'
               'wifi-lemonade: stage 24 passed;\n'
               'wifi-lemonade: station offered to iOS; runtime kept until normal stop\n')
        ready+=''.join(f'bt-client: state tick={i} powered=1 available=1 discovery_reports=1\n' for i in range(14))
        self.assertTrue(validator.discovery_evidence(ready)['passed'])
        ble=ready.replace('discovery_reports=1','discovery_reports=0')
        fixture='bt-ui: controlled_ble_fixture_visible=1\n'
        self.assertTrue(validator.discovery_evidence(ble+fixture)['passed'])
        for text in (ble+'bt-ui: controlled_ble_fixture_visible=0\n',
                     ble.replace('rows=1','rows=0')+fixture,
                     ble.replace('BTSDevicesController','OtherController')+fixture,
                     ble.replace('state=0','state=2')+fixture,
                     ble.replace('powered=1','powered=0')+fixture):
            with self.subTest(text=text):self.assertFalse(validator.discovery_evidence(text)['passed'])
        for before, after in (('rows=1','rows=0'),('discovery_reports=1','discovery_reports=0'),
                              ('powered=1','powered=0'),('clean=1','clean=0'),
                              ('BTSDevicesController','OtherController'),('state=0','state=2'),
                              ('root clock restored=1','root clock restored=0')):
            with self.subTest(before=before):self.assertFalse(validator.discovery_evidence(ready.replace(before,after))['passed'])
        self.assertFalse(validator.discovery_evidence(ready+'com.apple.bluetoothd exited status=1')['passed'])

    def test_bluetooth_overlay_does_not_duplicate_runtime_injection(self):
        bt=base.module('bt_runtime_injection','lemonade-build-bt-firmware.py')
        entries={'etc/inject.conf':(0o100644,0,0,b'original\n')}
        line=b'/redwood/lib/liblemonade-bt-compat.dylib bluetoothd'
        bt.add_injection(entries,line);once=dict(entries);bt.add_injection(entries,line)
        self.assertEqual(entries,once)
        self.assertEqual(entries['etc/inject.conf'][:3],(0o100644,0,0))
        self.assertTrue(entries['etc/inject.conf'][3].startswith(b'original\n'))

    def test_actual_wifi_entry_keeps_probes_isolated(self):
        source=(ROOT/'xnu/files/bsd/dev/lemonade_wifi.c').read_text()
        entry=source[source.index('void lemonade_wifi_init(void) {'):]
        stubs=r'''
#include <assert.h>
#include <stdbool.h>
#include <string.h>
static unsigned mode, guard, runtime, gpu, touch, cpu, powers, stops, wifi_mode;
static bool platform=true;
static bool PE_parse_boot_argn(const char *n, void *p, unsigned size) {
 unsigned value=!strcmp(n,"lemonade_wifi")?mode:!strcmp(n,"lemonade_dma_verified")?guard:
 !strcmp(n,"lemonade_runtime")?runtime:!strcmp(n,"gpu")?gpu:!strcmp(n,"touchscreen")?touch:cpu;
 *(unsigned*)p=value;return true;
}
static bool platform_is_lemonade(void){return platform;}
static void redwood_tl(const char *f,...){}
static bool inspect(void){return true;}
static bool power_root(unsigned m){powers++;return true;}
static void lemonade_wifi_stop(void){stops++;}
'''
        cases=r'''
int main(void){
 mode=24;guard=runtime=gpu=touch=cpu=1;lemonade_wifi_init();assert(powers==1 && stops==0);
 powers=0;guard=0;lemonade_wifi_init();assert(!powers);
 guard=1;runtime=0;lemonade_wifi_init();assert(!powers);
 runtime=2;lemonade_wifi_init();assert(!powers);
 runtime=1;for(mode=2;mode<24;mode++){lemonade_wifi_init();assert(!powers);}
 mode=24;platform=false;lemonade_wifi_init();assert(!powers);
 platform=true;gpu=touch=cpu=runtime=0;lemonade_wifi_init();assert(powers==1);
 powers=0;mode=25;lemonade_wifi_init();assert(!powers);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'test.c').write_text(stubs+entry+cases)
            subprocess.run([shutil.which('clang') or 'cc','-fsanitize=address,undefined','-o',str(p/'test'),str(p/'test.c')],check=True)
            subprocess.run([str(p/'test')],check=True)


if __name__ == '__main__':
    unittest.main()
