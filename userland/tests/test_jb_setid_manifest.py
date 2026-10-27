#!/usr/bin/env python3
"""Exercise archive-derived privileges, including an actual APFS round trip."""
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('jb_setid', ROOT / 'tools/jb-setid-manifest.py')
jb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jb)
sys.path.insert(0, str(ROOT / 'tools/apfs'))
import apfs


def archive(members):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as tar:
        for name, mode in members:
            member = tarfile.TarInfo(name)
            member.mode = mode
            member.size = 1
            tar.addfile(member, io.BytesIO(b'x'))
    stream.seek(0)
    return stream


class Permissions(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.root = Path(self.work.name)
        self.prefix = self.root / 'tree/jb'
        for name in ('usr/bin/login', 'usr/bin/su', 'usr/bin/ordinary', 'Applications/Sileo.app/giveMeRoot'):
            path = self.prefix / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'x')
            path.chmod(0o755)  # Reproduce a host copy that loses set-ID bits.
        self.members = [('./var/jb/usr/bin/login', 0o4755), ('var/jb/usr/bin/su', 0o4750),
                        ('var/jb/usr/bin/ordinary', 0o755), ('var/jb/usr/bin/removed', 0o4755)]

    def tearDown(self):
        self.work.cleanup()

    def test_apfs_roundtrip_restores_archive_modes_after_system_manifest(self):
        modes = jb.entries(archive(self.members), self.prefix)
        self.assertEqual(set(modes), {'jb/usr/bin/login', 'jb/usr/bin/su', 'jb/Applications/Sileo.app/giveMeRoot'})
        baseline = self.root / 'system.manifest'
        baseline.write_text('100755\t0\t0\tjb/usr/bin/login\n')
        overrides = self.root / 'setid.manifest'
        overrides.write_text(''.join(f'{m:o}\t0\t0\t{p}\n' for p, m in sorted(modes.items())))
        image = self.root / 'test.apfs'
        subprocess.run([sys.executable, str(ROOT / 'tools/apfs/mkapfs.py'), '-o', str(image),
                        f'System={self.root / "tree"},manifest={baseline},manifest={overrides}'],
                       check=True, stdout=subprocess.DEVNULL)
        container = apfs.Container(str(image))
        try:
            volume = apfs.pick_volume(container, None)
            actual = {path.lstrip('/'): volume.inode(fid) for path, _, fid, kind in apfs.walk(volume, apfs.ROOT_DIR_INO_NUM) if kind == 8}
            for path, mode in modes.items():
                self.assertEqual((actual[path].mode, actual[path].owner, actual[path].group), (mode, 0, 0))
                self.assertEqual(actual[path].contents(), b'x')
            self.assertEqual(actual['jb/usr/bin/ordinary'].mode, 0o100755)
        finally:
            container.close()

    def test_rejects_symlink_in_place_of_privileged_file(self):
        login = self.prefix / 'usr/bin/login'
        login.unlink()
        login.symlink_to('ordinary')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            jb.entries(archive(self.members), self.prefix)

    def test_rejects_missing_login_privilege(self):
        with self.assertRaisesRegex(ValueError, 'login'):
            jb.entries(archive([('var/jb/usr/bin/login', 0o755)]), self.prefix)

    def test_rejects_path_escape(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            jb.entries(archive([('var/jb/../../escape', 0o4755)]), self.prefix)


if __name__ == '__main__':
    unittest.main()
