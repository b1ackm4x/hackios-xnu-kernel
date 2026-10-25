#!/usr/bin/env python3
"""Reject unexpected or mismatched files before creating a public CI package."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ci_package', ROOT / 'tools/ci/package-lemonade.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackagingTests(unittest.TestCase):
    def test_public_allowlist_has_no_private_or_image_paths(self):
        for name in package.FILES:
            self.assertTrue(name.startswith(('out/lemonade-', 'userland/obj/')))
            self.assertFalse(name.endswith(('.apfs', '.img', '.dtb', '.metallib', '.log')))
            self.assertFalse(any(part in ('local', 'fw', 'var', 'ios165') for part in Path(name).parts))
        self.assertEqual(len(package.FILES), len(set(package.FILES)))

    def test_refuse_missing_and_symlink_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                package.checked_file(root, 'missing')
            (root / 'real').write_bytes(b'public')
            (root / 'alias').symlink_to('real')
            with self.assertRaises(ValueError):
                package.checked_file(root, 'alias')

    def test_refuse_path_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'root'
            root.mkdir()
            (root.parent / 'private').write_bytes(b'fixture')
            with self.assertRaises(ValueError):
                package.checked_file(root, '../private')

    def test_receipt_detects_source_and_binary_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / 'out'
            out.mkdir()
            source = root / 'source.c'
            binary = out / 'driver'
            source.write_bytes(b'source')
            binary.write_bytes(b'binary')
            (out / 'build.json').write_text(json.dumps({
                'sources': {'source.c': package.digest(source)},
                'artifacts': {'driver': {'sha256': package.digest(binary)}}}))
            package.check_receipt(root, 'out')
            source.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Source'):
                package.check_receipt(root, 'out')
            source.write_bytes(b'source')
            binary.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Binary'):
                package.check_receipt(root, 'out')

    def test_receipt_cannot_escape_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / 'out'
            out.mkdir()
            private = root / 'private'
            private.write_bytes(b'fixture')
            (out / 'build.json').write_text(json.dumps({
                'sources': {}, 'artifacts': {'../private': {'sha256': package.digest(private)}}}))
            with self.assertRaises(ValueError):
                package.check_receipt(root, 'out')

    def test_split_archive_roundtrip(self):
        import tarfile
        import io
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            writer = package.SplitWriter(root, 'runtime.tar.gz', limit=37)
            data = bytes(range(256)) * 3
            with tarfile.open(fileobj=writer, mode='w|gz') as archive:
                info = tarfile.TarInfo('runtime/kernel')
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            writer.close()
            self.assertGreater(len(writer.paths), 1)
            self.assertTrue(all(p.stat().st_size <= 37 for p in writer.paths))
            joined = b''.join(p.read_bytes() for p in writer.paths)
            with tarfile.open(fileobj=io.BytesIO(joined), mode='r:gz') as archive:
                self.assertEqual(archive.extractfile('runtime/kernel').read(), data)

    def test_launcher_rejects_ambiguous_usb(self):
        self.assertEqual(package.launch.unique_target(['one']), 'one')
        for devices, requested in (([], None), (['one', 'two'], 'one'), (['one'], 'two')):
            with self.assertRaises(SystemExit):
                package.launch.unique_target(devices, requested)

    def test_launcher_ignores_wireless_adb_and_unauthorized_targets(self):
        data = 'List of devices attached\nusb123 device usb:1-1 product:lemonade\n192.0.2.1:5555 device product:lemonade\nother unauthorized usb:1-2\n'
        self.assertEqual(package.launch.adb_devices(data), ['usb123'])

    def test_unaccepted_ci_build_requires_explicit_experimental_opt_in(self):
        from unittest.mock import patch
        with patch.object(package.launch, 'command', side_effect=AssertionError('Must not touch USB')):
            with self.assertRaisesRegex(SystemExit, 'no device acceptance'):
                package.launch.start({}, None, False)


if __name__ == '__main__':
    unittest.main()
