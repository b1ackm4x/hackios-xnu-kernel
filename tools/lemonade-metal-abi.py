#!/usr/bin/env python3
"""Compare Clang's actual ARM64 record layouts across the duplicated Metal/AIR ABI.

Input is produced by lemonade-build-metal.sh. This checks layout only, not GPU behavior.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re


def records(path):
    result = {}
    for block in path.read_text().split('*** Dumping AST Record Layout'):
        match = re.search(r'^\s*0 \| struct (rwa_\w+)\s*$', block, re.M)
        if match:
            result[match[1]] = '\n'.join(line.strip() for line in block.strip().splitlines())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    left = args.directory / 'frontend-layouts.txt'
    right = args.directory / 'compiler-layouts.txt'
    frontend, compiler = records(left), records(right)
    required = {'rwa_compute_info', 'rwa_texture_shape', 'rwa_bindings', 'rwa_draw',
                'rwa_render_shape', 'rwa_render_info', 'rwa_argbuf_layout'}
    compared = sorted(frontend.keys() & compiler.keys())
    missing = sorted(frontend.keys() - compiler.keys() | required - set(compared))
    mismatches = sorted(n for n in compared if frontend[n] != compiler[n])
    passed = not missing and not mismatches
    report = dict(result='pass' if passed else 'fail', target='arm64-apple-ios16.5',
                  compared=compared, missing=missing, mismatches=mismatches,
                  inputs={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (left, right)},
                  device_tested=False)
    (args.directory / 'abi-layout-audit.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'{"PASS" if passed else "FAIL"}: {len(compared)} shared AIR records; '
          f'missing={missing}, mismatches={mismatches}')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
