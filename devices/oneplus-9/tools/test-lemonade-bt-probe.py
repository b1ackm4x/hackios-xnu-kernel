#!/usr/bin/env python3
"""Reject changed/misbound Bluetooth observation inputs without using a device."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('bt_probe', Path(__file__).with_name('lemonade-run-bt-probe.py'))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeInputs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.base = self.root / 'out/releases/fixture'; self.base.mkdir(parents=True)
        self.image = self.root / 'out/probe'; self.image.mkdir()
        self.private = self.root / 'local/lemonade'; self.private.mkdir(parents=True)
        self.identity = hashlib.sha256(b'fixture phone').hexdigest()
        self.manifest = {'hardware_serial_sha256': self.identity, 'files': {}}
        for name in ('kernel', 'system.apfs', 'root.apfs'):
            data = name.encode(); (self.base / name).write_bytes(data)
            self.manifest['files'][name] = {'sha256': hashlib.sha256(data).hexdigest()}
        (self.base / 'manifest.json').write_text(json.dumps(self.manifest))
        (self.private / 'baseline.json').write_text(json.dumps({'hardware_serial_sha256': self.identity}))
        (self.image / 'root.apfs').write_bytes(b'diagnostic root')
        self.record = {'profile': 'lemonade-bluetooth-interface-probe-v1', 'base': 'out/releases/fixture',
            'base_manifest_sha256': probe.digest(self.base / 'manifest.json'),
            'root_sha256': probe.digest(self.image / 'root.apfs'), 'bytes': len(b'diagnostic root'),
            'normal_base_unchanged': True, 'all_other_entries_verified': True,
            'trace_receipt': {'observation_only': True}}
        for key, name in (('kernel_sha256', 'kernel'), ('system_sha256', 'system.apfs'), ('base_root_sha256', 'root.apfs')):
            self.record[key] = self.manifest['files'][name]['sha256']
        self.write()
        self.addCleanup(patch.stopall)
        patch.object(probe, 'ROOT', self.root).start()
        patch.object(probe, 'load', return_value=SimpleNamespace(verify=lambda _: self.manifest)).start()
        patch.object(probe.subprocess, 'run', side_effect=AssertionError('No device command allowed')).start()

    def write(self):
        (self.image / 'manifest.json').write_text(json.dumps(self.record))

    def test_valid_observation_inputs(self):
        self.assertEqual(probe.verify(self.image)[0], self.base)

    def test_modified_root_rejected(self):
        (self.image / 'root.apfs').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'image changed'): probe.verify(self.image)

    def test_other_kernel_rejected_even_with_a_valid_base(self):
        self.record['kernel_sha256'] = 'different'; self.write()
        with self.assertRaisesRegex(ValueError, 'verified base'): probe.verify(self.image)

    def test_other_phone_rejected(self):
        (self.private / 'baseline.json').write_text(json.dumps({'hardware_serial_sha256': 'other phone'}))
        with self.assertRaisesRegex(ValueError, 'different phone'): probe.verify(self.image)

    def test_base_directory_escape_rejected(self):
        self.record['base'] = '../outside'; self.write()
        with self.assertRaisesRegex(ValueError, 'inside out/releases'): probe.verify(self.image)

    def test_non_observation_receipt_rejected(self):
        self.record['trace_receipt']['observation_only'] = False; self.write()
        with self.assertRaisesRegex(ValueError, 'observation-only'): probe.verify(self.image)

    def test_verify_only_does_not_create_output_or_touch_device(self):
        output = self.private / 'new-evidence'
        with patch('sys.argv', ['probe', '--probe', str(self.image), '--output', str(output), '--verify-only']), contextlib.redirect_stdout(io.StringIO()):
            probe.main()
        self.assertFalse(output.exists())

    def test_private_radio_and_network_details_are_removed(self):
        raw = b'peer 01:23:45:67:89:ab\nSSID personal\nlink_key private\nbt-trace: result=0\n'
        clean = probe.sanitize(raw)
        self.assertNotIn(b'01:23', clean); self.assertNotIn(b'personal', clean); self.assertNotIn(b'private', clean)
        self.assertIn(b'bt-trace: result=0', clean)


if __name__ == '__main__': unittest.main()
