#!/usr/bin/env python3
"""Reject unexpected or mismatched files before creating a public CI package."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('ci_package', ROOT / 'tools/ci/package-lemonade.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackagingTests(unittest.TestCase):
    def test_public_allowlist_has_no_private_or_image_paths(self):
        for name in package.FILES:
            self.assertTrue(name.startswith(('out/lemonade-', 'userland/obj/')))
            self.assertFalse(name.endswith(('.apfs', '.img', '.dtb', '.metallib', '.log')))
            self.assertFalse(any(part in ('local', 'fw', 'var', 'sysimage') for part in Path(name).parts))
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

    def test_release_requires_every_uploaded_asset_and_matching_server_digest(self):
        import copy
        import hashlib
        publisher = package.module('ci_publish_test', 'tools/ci/publish-release.py')
        checksum = 'a' * 64 + '  components.tar.gz\n'
        release = {'assets': [
            {'name': 'components.tar.gz', 'state': 'uploaded', 'size': 10, 'digest': 'sha256:' + 'a' * 64},
            {'name': 'SHA256SUMS', 'state': 'uploaded', 'size': len(checksum),
             'digest': 'sha256:' + hashlib.sha256(checksum.encode()).hexdigest()},
        ]}
        publisher.verify_remote(release, checksum)
        bad = copy.deepcopy(release)
        bad['assets'].pop()
        with self.assertRaises(SystemExit):
            publisher.verify_remote(bad, checksum)
        for key, value in (('state', 'new'), ('digest', 'sha256:' + 'b' * 64), ('size', 0)):
            bad = copy.deepcopy(release)
            bad['assets'][0][key] = value
            with self.assertRaises(SystemExit):
                publisher.verify_remote(bad, checksum)

    def test_release_lookup_finds_drafts_without_a_published_tag(self):
        publisher = package.module('ci_publish_draft_test', 'tools/ci/publish-release.py')
        first = [{'id': i, 'tag_name': 'older-' + str(i)} for i in range(100)]
        draft = {'id': 1234, 'tag_name': 'ci-target', 'draft': True}
        with patch.object(publisher, 'gh', side_effect=[json.dumps(first), json.dumps([draft])]) as api:
            self.assertEqual(publisher.find_release('owner/repo', 'ci-target'), draft)
            self.assertEqual(api.call_args_list[1].args,
                             ('api', 'repos/owner/repo/releases?per_page=100&page=2'))
        with patch.object(publisher, 'gh', return_value='[]'):
            self.assertIsNone(publisher.find_release('owner/repo', 'missing'))

    def test_publish_uses_creation_response_when_release_list_is_stale(self):
        import contextlib
        import hashlib
        import io
        import os
        from urllib.parse import parse_qs, urlparse
        publisher = package.module('ci_publish_flow_test', 'tools/ci/publish-release.py')
        old_cwd = Path.cwd()
        uploaded = []
        calls = []
        endpoint = 'repos/owner/repo/releases/77'
        def fake_gh(*args, **kwargs):
            calls.append(args)
            if args == ('api', 'repos/owner/repo/releases?per_page=100&page=1'):
                return '[]'  # Remains stale throughout this transaction.
            if args[:4] == ('api', '--method', 'POST', 'repos/owner/repo/releases'):
                self.assertTrue(json.loads(kwargs['input'])['draft'])
                return json.dumps({'id': 77, 'draft': True, 'assets': []})
            if args[:3] == ('api', '--method', 'POST') and args[3].startswith('https://uploads.github.com/'):
                self.assertIn('/releases/77/assets?', args[3])
                name = parse_qs(urlparse(args[3]).query)['name'][0]
                data = Path(args[-1]).read_bytes()
                uploaded.append({'name': name, 'size': len(data), 'state': 'uploaded',
                                 'digest': 'sha256:' + hashlib.sha256(data).hexdigest()})
                return json.dumps(uploaded[-1])
            if args == ('api', endpoint):
                return json.dumps({'assets': uploaded, 'draft': True})
            if args == ('api', '--method', 'PATCH', endpoint, '-F', 'draft=false'):
                return json.dumps({'assets': uploaded, 'draft': False,
                                   'html_url': 'https://github.com/owner/repo/releases/tag/ci-test'})
            self.fail('Unexpected release operation: ' + repr(args))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                out = root / 'out/ci-release'
                out.mkdir(parents=True)
                (out / 'components.tar.gz').write_bytes(b'compiled fixture')
                checksum = hashlib.sha256(b'compiled fixture').hexdigest() + '  components.tar.gz\n'
                (out / 'SHA256SUMS').write_text(checksum)
                with patch.object(publisher, 'ROOT', root), patch.object(publisher, 'gh', side_effect=fake_gh), \
                     patch.object(publisher.subprocess, 'run'), patch.dict(os.environ, {
                         'COMMIT': 'a' * 40, 'GH_REPO': 'owner/repo', 'RUN_URL': 'https://github.com/owner/repo/actions/runs/1'}), \
                     contextlib.redirect_stdout(io.StringIO()):
                    publisher.main()
                self.assertEqual(len(uploaded), 2)
                self.assertEqual(sum('per_page' in call[-1] for call in calls), 1)
        finally:
            os.chdir(old_cwd)


if __name__ == '__main__':
    unittest.main()
