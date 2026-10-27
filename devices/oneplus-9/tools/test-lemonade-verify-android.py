#!/usr/bin/env python3
"""Exercise missing/corrupt checksum evidence without contacting a device."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('verify_android', Path(__file__).with_name('lemonade-verify-android.py'))
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class ChecksumEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.private = self.root / 'local/lemonade'
        self.private.mkdir(parents=True)
        self.digest = 'a' * 64
        self.serial = 'fixture-usb-device'
        (self.private / 'baseline.json').write_text(json.dumps({
            'hardware_serial_sha256': hashlib.sha256(self.serial.encode()).hexdigest(),
            'ro.boot.slot_suffix': '_b', 'ro.boot.flash.locked': '0'}))
        (self.private / 'sha256.json').write_text(json.dumps({
            name + '_b.img': {'sha256': self.digest} for name in ('boot', 'vendor_boot', 'dtbo', 'vbmeta', 'xbl')}))
        self.responses = []
        self.root_calls = []

    def command(self, args, timeout=15):
        if args == ['adb', 'devices', '-l']:
            return 'List of devices attached\n' + self.serial + ' device usb:1\n'
        self.assertEqual(args[:3], ['adb', '-s', self.serial])
        if args[3:5] == ['shell', 'getprop']:
            return {'sys.boot_completed': '1', 'ro.product.model': 'LE2110', 'ro.soc.model': 'SM8350',
                    'ro.boot.slot_suffix': '_b', 'ro.boot.flash.locked': '0'}[args[5]]
        self.assertEqual(args[3], 'exec-out')
        self.assertEqual(len(args), 5)
        self.assertTrue(args[4].startswith("su -c 'sha256sum /dev/block/by-name/"))
        self.root_calls.append(args[4])
        return self.responses.pop(0) if self.responses else self.digest + '  partition'

    def run_verify(self):
        with patch.object(verify, 'ROOT', self.root), patch.object(verify, 'command', self.command), \
                patch.object(verify.time, 'sleep'), patch('sys.argv', ['verify-android']), \
                contextlib.redirect_stdout(io.StringIO()):
            verify.main()

    def test_empty_first_read_retries_and_checks_all_partitions(self):
        self.responses = ['']
        self.run_verify()
        self.assertEqual(len(self.root_calls), 6)
        report = json.loads(next(self.private.glob('android-roundtrip-*.json')).read_text())
        self.assertTrue(report['passed'])
        self.assertEqual(len(report['active_partition_hashes_match_backup']), 5)

    def test_invalid_output_never_creates_success_record(self):
        self.responses = ['', 'permission unavailable', 'short']
        with self.assertRaisesRegex(SystemExit, 'No valid root checksum'):
            self.run_verify()
        self.assertEqual(len(self.root_calls), 3)
        self.assertFalse(list(self.private.glob('android-roundtrip-*.json')))

    def test_valid_but_different_hash_fails(self):
        self.responses = ['b' * 64 + '  partition']
        with self.assertRaisesRegex(SystemExit, 'Partition hash mismatch'):
            self.run_verify()
        report = json.loads(next(self.private.glob('android-roundtrip-*.json')).read_text())
        self.assertFalse(report['passed'])


if __name__ == '__main__':
    unittest.main()
