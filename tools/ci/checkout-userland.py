#!/usr/bin/env python3
"""Check out the exact paired userland revision in a fresh, shared workspace."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def run(*args, **kwargs):
    return subprocess.check_output(args, cwd=ROOT, text=True, **kwargs).strip()


def main():
    paired = json.loads((ROOT / 'common/devices/lemonade.sources.json').read_text())['userland']
    revision = paired['commit']
    repository = paired['repository']
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise SystemExit('Userland must be pinned to a full commit SHA')
    if repository != 'https://github.com/b1ackm4x/hackios-xnu-userland':
        raise SystemExit('Unexpected paired repository')
    destination = ROOT / '.userland.git'
    if destination.exists():
        raise SystemExit('Use a fresh workspace; refusing to replace an existing userland checkout')
    with tempfile.TemporaryDirectory(prefix='lemonade-userland-') as temporary:
        checkout = Path(temporary) / 'source'
        run('git', 'init', '-q', str(checkout))
        run('git', '-C', str(checkout), 'remote', 'add', 'origin', repository + '.git')
        run('git', '-C', str(checkout), 'fetch', '--depth=1', 'origin', revision)
        run('git', '-C', str(checkout), 'checkout', '--detach', revision)
        actual = run('git', '-C', str(checkout), 'rev-parse', 'HEAD')
        if actual != revision:
            raise SystemExit('Paired userland revision mismatch')
        files = [p for p in run('git', '-C', str(checkout), 'ls-files', '-z').split('\0') if p]
        reserved = {'README.md', 'LICENSE', '.gitignore'}
        for relative in files:
            if relative in reserved:
                continue
            if (ROOT / relative).exists() or (ROOT / relative).is_symlink():
                raise SystemExit('Userland would overwrite a workspace file: ' + relative)
        shutil.move(str(checkout / '.git'), destination)
        ugit = ('git', '--git-dir=' + str(destination), '--work-tree=' + str(ROOT))
        run(*ugit, 'config', 'core.worktree', str(ROOT))
        run(*ugit, 'config', 'status.showUntrackedFiles', 'no')
        for relative in sorted(reserved.intersection(files)):
            run(*ugit, 'update-index', '--skip-worktree', relative)
        for relative in files:
            if relative in reserved:
                continue
            target = ROOT / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(checkout / relative), target)
        (destination / 'info/exclude').write_bytes((ROOT / 'common/userland.exclude').read_bytes())
    print('Paired userland:', actual)


if __name__ == '__main__':
    main()
