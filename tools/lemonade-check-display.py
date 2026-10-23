#!/usr/bin/env python3
"""Check bounded DPU identity-mapping/dual-buffer evidence, including recovery.

Hardware mixer signatures demonstrate DPU output, not optical panel capture.
This does not certify IOSurface release fences, scaling, or a higher refresh mode.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re

NUMBER = r'(0x[0-9a-f]+|[0-9]+)'
FAULT_MASK = 0xc00001fe


def check(record, raw):
    text = raw.decode(errors='replace')
    # The bounded loader log also contains a copy of the kernel printf buffer.
    lines = list(dict.fromkeys(line[line.index('lemonade-display: '):]
                              for line in text.splitlines() if 'lemonade-display: ' in line))
    log = '\n'.join(lines)
    args = record.get('cmdline', '').split()
    mode = record.get('display')

    def numbers(pattern):
        match = re.search(pattern, log)
        return tuple(int(item, 0) for item in match.groups()) if match else None

    baseline = numbers(r'initial MISR=' + NUMBER + '/' + NUMBER)
    mapped = numbers(r'mapping samples=(\d+)/(\d+) frames=(\d+) MISR=' + NUMBER + '/' + NUMBER +
                     r' fsr=' + NUMBER + r' global=' + NUMBER + r' sctlr=' + NUMBER)
    restored = numbers(r'mapping restore registers=(\d+) tlb=(\d+) fsr=' + NUMBER + r' global=' + NUMBER)
    misr = numbers(r'MISR control restored=(\d+)/(\d+) status=' + NUMBER + '/' + NUMBER)
    final = numbers(r'final irq_count=(\d+) unexpected=' + NUMBER + r' presents=(\d+) late=(\d+) dropped=(\d+)')
    clocks = re.findall(r'bus vote (\d+) restored enable=(\d+) expected=(\d+)', log)
    summaries = ('mapping samples=', 'mapping restore ', 'MISR control restored=', 'final irq_count=')
    checks = {
        'lemonade_record': record.get('device') == 'lemonade',
        'known_probe': mode in ('map', 'flip'),
        'log_integrity': hashlib.sha256(raw).hexdigest() == record.get('log_sha256'),
        'kernel_identified': bool(re.fullmatch(r'[0-9a-f]{64}', record.get('kernel_sha256', ''))),
        'returned_to_loader': bool(re.search(r'result: returned \d+ 0(?:\s|$)', record.get('result', ''))),
        'no_kernel_panic': not re.search(r'panic\(cpu|Debugger called: <panic>|uncaught exception', text, re.I),
        'matching_mode': 'lemonade_display=' + str({'map': 6, 'flip': 7}.get(mode)) in args,
        'storage_writes_disabled': 'ufs_write=0' in args,
        'no_probe_failure': not re.search(r'refused|failed|context restore offset=', log),
        'unambiguous_results': all(sum(line.startswith('lemonade-display: ' + prefix) for line in lines) == 1
                                  for prefix in summaries + (('flip completed=', 'source restore ') if mode == 'flip' else ())),
        'identity_pages_and_guards': 'identity table verified pages=2532 ' in log and 'guards=unmapped' in log,
        'identity_output_unchanged': bool(mapped and baseline and mapped[:2] == (32, 32) and mapped[2] >= 32
                                        and mapped[3:5] == baseline and not (mapped[5] & FAULT_MASK)
                                        and mapped[6] == 0 and mapped[7] & 1),
        'context_restored': bool(restored and restored[:2] == (1, 1) and not (restored[2] & FAULT_MASK) and restored[3] == 0),
        'measurement_controls_restored': misr == (1, 1, 0, 0),
        'irq_clean': bool(final and final[0] >= 32 and final[1:] == (0, 0, 0, 0)),
        'irq_restored': 'irq restored tear_enable=0 gic_enable=0 route=0' in log,
        'bus_votes_restored': {int(c[0]) for c in clocks} == {0, 1} and all(c[1] == c[2] for c in clocks),
    }
    frames = 0
    completed = 0
    if mode == 'flip':
        expected = [int(a.split('=', 1)[1]) for a in args if re.fullmatch(r'lemonade_display_flips=\d+', a)]
        flipped = numbers(r'flip completed=(\d+)/(\d+) frames=(\d+) MISR=' + NUMBER + '/' + NUMBER +
                          r' fsr=' + NUMBER + r' global=' + NUMBER + r' underrun=(\d+)')
        signatures = [numbers(r'buffer ' + str(n) + r' MISR=' + NUMBER + '/' + NUMBER) for n in range(2)]
        buffers = [numbers(r'buffer ' + str(n) + r' pages=(\d+) above4G=(\d+) iova=' + NUMBER + r' guards=unmapped')
                   for n in range(2)]
        checks.update({
            'requested_count_completed': bool(flipped and len(expected) == 1 and expected[0] in (64, 1000)
                                             and flipped[0] == flipped[1] == expected[0] and flipped[2] >= expected[0]),
            'distinct_hardware_images': bool(baseline and all(signatures) and signatures[0] != signatures[1]
                                            and all(all(s[i] != baseline[i] for i in (0, 1)) for s in signatures)),
            'owned_high_memory_buffers': all(b and b[0] == 2532 and b[1] > 0 and b[2] == 0x10000000 + n * 0x2000000
                                            for n, b in enumerate(buffers)),
            'no_dma_fault_or_underrun': bool(flipped and not (flipped[5] & FAULT_MASK) and flipped[6:] == (0, 0)),
            'original_pixels_before_unmap': 'source restore pixels=1 address=1/1 ' in log and
                                           log.index('source restore pixels=1 address=1/1 ') < log.find('mapping restore '),
        })
        if flipped:
            completed, frames = flipped[0], flipped[2]
    return {'passed': all(checks.values()), 'mode': mode, 'checks': checks,
            'completed_flips': completed, 'hardware_frames_during_flips': frames,
            'kernel_sha256': record.get('kernel_sha256'), 'log_sha256': record.get('log_sha256')}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    record = json.loads(args.log.with_suffix('.manifest.json').read_text())
    result = check(record, args.log.read_bytes())
    encoded = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(encoded)
    print(encoded, end='')
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
