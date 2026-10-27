#!/usr/bin/env python3
"""Offline integration with synthetic credentials; no device commands execute.

Uses the locally verified firmware/root assets and the compiled acceptance
program, all read-only. Every output is removed with the temporary directory.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


builder = module('wifi_builder', ROOT / 'devices/oneplus-9/tools/lemonade-build-wifi-image.py')
usb = module('wifi_usb', ROOT / 'devices/oneplus-9/tools/lemonade-usb.py')


class PrivateImage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT / 'out/lemonade-net-test.manifest.json').exists():
            raise unittest.SkipTest('Build the local acceptance program first')
        cls.work = tempfile.TemporaryDirectory(prefix='lemonade-offline-')
        cls.base = Path(cls.work.name)
        cls.secret = b'LNW1\x09\x10syntheticexample-password'
        for directory in ('out', 'local/lemonade/wifi', 'userland'):
            (cls.base / directory).mkdir(parents=True, exist_ok=True)
        (cls.base / 'tools').symlink_to(ROOT / 'tools', target_is_directory=True)
        (cls.base / 'devices').symlink_to(ROOT / 'devices', target_is_directory=True)
        (cls.base / 'sysimage').symlink_to(ROOT / 'sysimage', target_is_directory=True)
        (cls.base / 'local/lemonade/wifi/firmware').symlink_to(ROOT / 'local/lemonade/wifi/firmware', target_is_directory=True)
        for file in ('userland/root.apfs', 'userland/system.apfs', 'local/lemonade/images.json',
                     'out/lemonade-net-test', 'out/lemonade-net-test.manifest.json',
                     'out/lemonade-kernel', 'out/lemonade-kernel.manifest.json'):
            (cls.base / file).symlink_to(ROOT / file)
        with patch.object(builder, 'ROOT', cls.base), patch.object(builder, 'current_network', return_value=cls.secret), \
                patch('sys.argv', ['builder', '--driver-files', '--connect-current']), \
                contextlib.redirect_stdout(io.StringIO()):
            builder.main()
        cls.built = cls.base / 'out/lemonade-wifi/root.apfs'
        cls.saved = cls.base / 'synthetic.apfs'
        shutil.copy2(cls.built, cls.saved)
        cls.manifest = json.loads(cls.built.with_name('manifest.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_image_changes_only_intended_files_and_permissions(self):
        before = builder.contents(self.base / 'userland/root.apfs')
        after = builder.contents(self.saved)
        changed = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
        self.assertEqual(changed, {'fw/amss.bin', 'fw/m3.bin', 'fw/board.bin', 'fw/regdb.bin',
            'fw/lemonade-network', 'bin/lemonade-net-test', 'LaunchDaemons/com.lemonade.wifi-test.plist',
            'LaunchDaemons/com.apple.configd.plist', 'LaunchDaemons/com.apple.mDNSResponder.plist',
            'LaunchDaemons/com.apple.mDNSResponderHelper.plist'} |
            ({'fw'} if 'fw' not in before else set()))
        self.assertEqual(after['fw/lemonade-network'], (0o100600, 0, 0, self.secret))
        self.assertTrue(self.manifest['network_private'])
        self.assertNotIn('example-password', json.dumps(self.manifest))

    def simulate(self, stage_fails=False):
        shutil.copy2(self.saved, self.built)
        serial = 'offline-test-device'
        identity = hashlib.sha256(serial.encode()).hexdigest()
        loader = 'lemonade-' + identity[:16]
        (self.base / 'local/lemonade/baseline.json').write_text(json.dumps({'hardware_serial_sha256': identity}))
        commands, cleanup = [], []
        def command(argv, timeout=15, check=True):
            commands.append(argv)
            if argv == ['adb', 'devices', '-l']:
                return subprocess.CompletedProcess(argv, 0, 'List of devices attached\n', '')
            if argv == ['fastboot', 'devices']:
                return subprocess.CompletedProcess(argv, 0, loader + '\tfastboot\n', '')
            if argv[0] == 'bash':
                return subprocess.CompletedProcess(argv, 0, 'wifi=0 ufs=0 ufs_write=0', '')
            if argv[0] == 'python3' and Path(argv[1]).name == 'mkpayload.py':
                Path(argv[argv.index('-o')+1]).write_bytes(self.secret)
                return subprocess.CompletedProcess(argv, 0, '', '')
            self.assertEqual(argv[:3], ['fastboot', '-s', loader])
            parts = argv[3:]
            if parts == ['getvar', 'product']:
                output = 'product: xnuloader\n'
            elif parts == ['getvar', 'result']:
                output = 'result: returned 1 0\n'
            elif parts[0] == 'stage':
                self.assertTrue(Path(parts[1]).is_file())
                if stage_fails:
                    raise subprocess.TimeoutExpired(argv, timeout)
                output = ''
            elif parts in (['oem', 'run', '0'], ['oem', 'log']):
                output = ''
            elif parts[0] == 'get_staged':
                Path(parts[1]).write_bytes(b'synthetic example-password 00:11:22:33:44:55')
                output = ''
            else:
                self.fail('Unexpected operation in offline device simulation')
            return subprocess.CompletedProcess(argv, 0, output, '')
        def register(fn, **kwargs):
            cleanup.append(lambda: fn(**kwargs))
        try:
            with patch.object(usb, 'ROOT', self.base), patch.object(usb, 'command', command), \
                    patch.object(usb, 'verified_firmware_overlay', return_value=(self.built, self.manifest, {})), \
                    patch.object(usb.time, 'sleep'), patch.object(usb.atexit, 'register', register), \
                    patch('sys.argv', ['usb', 'run', '--kernel', 'built', '--boot-cpu', '7', '--cpus', '1', '--wifi', 'station', '--userland', '--network-test', '--seconds', '1']), \
                    contextlib.redirect_stdout(io.StringIO()):
                if stage_fails:
                    with self.assertRaises(subprocess.TimeoutExpired):
                        usb.main()
                else:
                    usb.main()
        finally:
            for callback in cleanup:
                callback()
        self.assertFalse(self.built.exists())
        self.assertFalse((self.base / 'out/lemonade-xnu.bin').exists())
        self.assertNotIn('example-password', json.dumps(commands))
        return list((self.base / 'local/lemonade').glob('*-run-cp0.log'))

    def test_transfer_and_log_are_private_and_temporary(self):
        logs = self.simulate()
        self.assertTrue(logs)
        for log in logs:
            raw = log.read_bytes()
            self.assertNotIn(b'example-password', raw)
            self.assertNotIn(b'synthetic', raw)
            self.assertNotIn(b'00:11:22:33:44:55', raw)
            receipt = json.loads(log.with_suffix('.manifest.json').read_text())
            self.assertTrue(receipt['network_identity_redacted'])
            self.assertEqual(len(receipt['live_logs']), 1)
            self.assertEqual(receipt['log_sha256'], hashlib.sha256(raw).hexdigest())

    def test_failed_upload_cleans_both_secret_artifacts(self):
        self.simulate(stage_fails=True)


if __name__ == '__main__':
    unittest.main()
