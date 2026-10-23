#!/usr/bin/env python3
"""Validate recorded physical S6SY792 input; chip initialization alone does not pass."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from frames import lines_of

ROOT = Path(__file__).resolve().parents[1]


def inspect(log):
    manifest = json.loads(log.with_suffix('.manifest.json').read_text())
    paths = [log]
    valid_logs = hashlib.sha256(log.read_bytes()).hexdigest() == manifest.get('log_sha256')
    for item in manifest.get('live_logs', []):
        path = ROOT / item['path']
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            valid_logs = False
        else:
            paths.append(path)
    text = '\n'.join(p.read_text(errors='replace') for p in paths)
    final = re.findall(r's6sy792: stopped; pin configuration restored; reports (\d+) down (\d+) up (\d+) '
                       r'active (0x[0-9a-f]+|0) I2C errors (\d+) bad (\d+)', log.read_text(errors='replace'))
    counts = {}
    if final:
        values = [int(x, 16 if i == 3 else 10) for i, x in enumerate(final[-1])]
        counts = dict(zip(('reports', 'down', 'up', 'active_mask', 'i2c_errors', 'bad_packets'), values))
    samples = []
    for line in lines_of(paths):
        match = re.fullmatch(r'tl: p ([123]) (\d+) (\d+) (\d+) at (\d+)', line)
        if match:
            samples.append(tuple(map(int, match.groups())))
    posted = {name: sum(v[0] == kind for v in samples) for kind, name in ((1, 'down'), (2, 'move'), (3, 'up'))}
    checks = {
        'exact_adapted_touch_run': manifest.get('device') == 'lemonade' and
            manifest.get('kernel_source') == 'built' and manifest.get('touch') == 'on',
        'evidence_hashes': valid_logs,
        'returned_to_loader': 'result: returned ' in manifest.get('result', ''),
        'original_chip_and_firmware': 's6sy792: ID 53 45 37 boot 20 integrity 80 firmware 28240121' in text,
        'scan_enabled': 's6sy792: physical touch ready, 1080x2400, 10 contacts' in text,
        'physical_down_and_up': counts.get('down', 0) > 0 and counts.get('up', 0) > 0,
        'contacts_released': bool(counts) and counts['down'] == counts['up'] and counts['active_mask'] == 0,
        'no_i2c_or_packet_error': bool(counts) and counts['i2c_errors'] == 0 and counts['bad_packets'] == 0,
        'recorded_click_and_motion': posted['down'] > 0 and posted['move'] > 0 and posted['up'] > 0,
        'physical_counts_match_trace': bool(counts) and posted['down'] == counts['down'] and posted['up'] == counts['up'],
        'no_input_failure': not any(s in text for s in ('s6sy792: initialization failed',
            's6sy792: repeated I2C failure', 'panic(cpu')),
    }
    return {'passed': all(checks.values()), 'checks': checks, 'driver_counts': counts,
            'posted_samples': posted, 'kernel_sha256': manifest.get('kernel_sha256'),
            'log': str(log), 'log_sha256': hashlib.sha256(log.read_bytes()).hexdigest(),
            'checked_log_files': len(paths), 'manual_ui_acceptance': 'requires user observation'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = inspect(args.log)
    result = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.write_text(result)
    print(result, end='')
    raise SystemExit(0 if report['passed'] else 1)
