#!/usr/bin/env python3
"""Read the bootstrap tar on stdin and emit APFS overrides for its set-ID files.

The extracted prefix is the sole argument. Host copies (including macOS shared
folders) can silently clear set-ID bits; derive them from the archive instead.
Both System and Boot images store the prefix under jb/. No host chmod is needed.
"""
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile


def entries(stream, prefix):
    modes = {}
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            if not member.isfile() or not member.mode & 0o6000:
                continue
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or path.parts[:2] != ('var', 'jb')
                    or any(c in member.name for c in '\t\r\n')):
                raise ValueError('set-ID archive member is outside var/jb')
            relative = Path(*path.parts[2:])
            target = prefix / relative
            if target.is_symlink():
                raise ValueError(f'set-ID file became a symlink: {relative}')
            if target.is_file():
                modes['jb/' + relative.as_posix()] = stat.S_IFREG | member.mode
    helper = prefix / 'Applications/Sileo.app/giveMeRoot'
    if not helper.is_file() or helper.is_symlink():
        raise ValueError('Sileo root helper is missing or is a symlink')
    modes['jb/Applications/Sileo.app/giveMeRoot'] = stat.S_IFREG | 0o4755
    if modes.get('jb/usr/bin/login') != stat.S_IFREG | 0o4755:
        raise ValueError('bootstrap login must have mode 4755')
    return modes


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    for path, mode in sorted(entries(sys.stdin.buffer, Path(sys.argv[1])).items()):
        print(f'{mode:o}\t0\t0\t{path}')
