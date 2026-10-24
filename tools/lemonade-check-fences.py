#!/usr/bin/env python3
"""Validate real producer-release ordering from a bounded copy-presenter run.

Queue/retirement/result timestamps are captured under the same kernel lock.
This proves CPU source lifetime, not panel scanout, optical quality or all Metal.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re


def check(record, raw):
    text = raw.decode(errors='replace')
    traces = list(dict.fromkeys(line[line.index('tl: '):].strip()
                               for line in text.splitlines() if 'tl: ' in line))
    queued, retired, shown, waits = {}, {}, {}, []
    errors = []
    for line in traces:
        if m := re.fullmatch(r'tl: ([qr]) (\d+) at (\d+)', line):
            group = queued if m[1] == 'q' else retired
            sid, when = int(m[2]), int(m[3])
            if sid in group:
                errors.append('Conflicting ' + m[1] + ' timestamps for ' + str(sid))
            group[sid] = when
        elif m := re.fullmatch(r'tl: v (\d+) ready (\d+) shown (\d+) done (\d+)', line):
            sid = int(m[1])
            if sid in shown:
                errors.append('Conflicting display timestamps for ' + str(sid))
            shown[sid] = tuple(int(v) for v in m.groups()[1:])
        elif m := re.fullmatch(r'tl: wr (\d+) (0x[\da-f]+|0) (0x[\da-f]+|0) at (\d+)', line):
            waits.append(tuple(int(v, 0) if v.startswith('0x') else int(v) for v in m.groups()))
        elif re.match(r'tl: (q|r|v|wr) ', line):
            errors.append('Malformed fence/display trace')
    for sid, when in retired.items():
        if sid not in queued or sid not in shown or not queued[sid] <= shown[sid][0] <= shown[sid][1] <= shown[sid][2] <= when:
            errors.append('Retired before all source reads completed: ' + str(sid))
    busy, success, checked_success, blocking = 0, 0, 0, 0
    for sid, flags, result, when in waits:
        mode, poll = flags & 0x7fffffff, bool(flags & 0x80000000)
        if mode > 4:
            errors.append('Unexpected wait mode')
            continue
        if mode in (3, 4):
            if result != (0 if mode == 3 else 0xe00002c2):
                errors.append('Unexpected active-state check result')
            continue
        # Strict inequalities avoid pretending microsecond timestamp ties have
        # a known order. A completed timestamp is required for each shown frame.
        pending = [n for n, q in queued.items() if (mode == 2 or n == sid) and
                   q < when and (n not in retired or when < retired[n])]
        if result == 0:
            success += 1
            blocking += not poll
            if pending:
                errors.append('Successful wait before source release: ' + str(sid))
            if mode != 2 and sid in retired and retired[sid] <= when:
                checked_success += 1
        elif result == 0xe00002d5:
            busy += 1
            if not poll:
                errors.append('Blocking wait returned Busy')
            if not any((mode == 2 or n == sid) and q <= when and
                       (n not in retired or when <= retired[n]) for n, q in queued.items()):
                errors.append('Busy result without an outstanding source: ' + str(sid))
        else:
            errors.append('Wait failed: ' + hex(result))
    args = record.get('cmdline', '').split()
    actual_cmdlines = list(dict.fromkeys(re.findall(r'^cmdline: (.*)$', text, re.M)))
    actual_args = actual_cmdlines[0].split() if len(actual_cmdlines) == 1 else []
    finals = list(dict.fromkeys(re.findall(
        r'lemonade-display: final irq_count=(\d+) unexpected=(0x[\da-f]+|\d+) presents=(\d+) late=(\d+) dropped=(\d+)', text)))
    votes = set(re.findall(r'bus vote (\d+) restored enable=(\d+) expected=(\d+)', text))
    checks = {
        'exact_device': record.get('device') == 'lemonade',
        'log_integrity': hashlib.sha256(raw).hexdigest() == record.get('log_sha256'),
        'kernel_identified': bool(re.fullmatch(r'[0-9a-f]{64}', record.get('kernel_sha256', ''))),
        # The small kernel printf ring may evict its startup banner. The
        # loader's cmdline and the per-frame q/r/wr events survive separately.
        'explicit_fences': record.get('display_fences') is True and
            [a for a in args if a.startswith('fb_fences=')] == ['fb_fences=1'] and
            [a for a in actual_args if a.startswith('fb_fences=')] == ['fb_fences=1'],
        'synchronized_copy_presenter': record.get('display') == 'sync' and 'lemonade_display=4' in args,
        'storage_writes_disabled': 'ufs_write=0' in args,
        'returned_to_loader': bool(re.search(r'result: returned \d+ 0(?:\s|$)', record.get('result', ''))),
        'no_kernel_panic': not re.search(r'panic\(cpu|Debugger called: <panic>|Debugger message:\s*panic|uncaught exception', text, re.I),
        'sustained_frame_evidence': len(retired) >= 100 and set(shown).issubset(retired) and len(queued) >= len(retired),
        'observed_busy_and_success': busy > 0 and checked_success >= 100 and blocking > 0,
        'release_and_wait_order': not errors,
        'display_clean': len(finals) == 1 and int(finals[0][0]) >= 100 and int(finals[0][1], 0) == 0 and
            int(finals[0][2]) >= 100 and finals[0][3:] == ('0', '0'),
        'interrupts_restored': 'irq restored tear_enable=0 gic_enable=0 route=0' in text,
        'bus_votes_restored': {v[0] for v in votes} == {'0', '1'} and all(v[1] == v[2] for v in votes),
    }
    return {'passed': all(checks.values()), 'checks': checks, 'queued': len(queued), 'retired': len(retired),
            'shown': len(shown), 'busy_results': busy, 'success_results': success,
            'successes_after_observed_release': checked_success, 'blocking_successes': blocking,
            'ordering_errors': errors[:20], 'kernel_sha256': record.get('kernel_sha256'),
            'log_sha256': record.get('log_sha256')}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = check(json.loads(args.log.with_suffix('.manifest.json').read_text()), args.log.read_bytes())
    encoded = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(encoded)
    print(encoded, end='')
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
