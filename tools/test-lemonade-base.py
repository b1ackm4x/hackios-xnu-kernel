#!/usr/bin/env python3
"""Runtime packaging boundaries and the actual Wi-Fi integration entry point."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('base', ROOT / 'tools/lemonade-build-base.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


class BasePolicy(unittest.TestCase):
    def package(self, folder):
        files={}
        for name in ('boot.img','kernel','root.apfs','system.apfs','launch.py','mkpayload.py'):
            path=folder/name;path.write_bytes(name.encode())
            files[name]={'bytes':path.stat().st_size,'sha256':base.digest(path)}
        manifest={'profile':'lemonade-base-v1','boot_arguments':base.launch.BASE_ARGUMENTS,
                  'files':files,'dma_guard_verified':True,'dma_guard_kernel_sha256':files['kernel']['sha256']}
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

    def tree(self):
        tree = {p: (0o100755, 0, 0, b'program') for p in ('bin/metal-enable', 'sbin/init')}
        tree['etc/rc'] = (0o100644, 0, 0, b'/redwood/bin/metal-enable\nexec /sbin/launchd.ios\n')
        for n in ('amss.bin', 'm3.bin', 'board.bin', 'regdb.bin'):
            tree['fw/'+n] = (0o100644, 0, 0, b'firmware')
        for n in ('wifid', 'configd', 'mDNSResponder', 'chronod'):
            tree['LaunchDaemons/com.apple.'+n+'.plist'] = (0o100644, 0, 0,
                plistlib.dumps({'Label':'com.apple.'+n, 'Program':'/usr/sbin/'+n}))
        return tree

    def test_clean_runtime(self):
        base.validate_tree(self.tree())

    def test_workloads_helpers_and_credentials_rejected(self):
        for p in ('bin/gl_test', 'bin/rwtouch', 'etc/rc.jobs', 'share/pattern.bin', 'fw/lemonade-network', 'share/test.metallib'):
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
        for word in ('lemonade_runtime=1','lemonade_wifi=24','gpu=1','touchscreen=1','fb_fences=1','ufs_write=0','var_store=0'):
            self.assertIn(word,args)
        self.assertFalse(any(w.startswith(('loader_return_after=','touch_lat=','cpuload=','rec_','adreno_watch=')) for w in args))
        self.assertEqual(len(args),len({w.split('=')[0] for w in args}))

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
