#!/usr/bin/env python3
"""Verify real TE cadence, changing DPU output and restoration for the 120-Hz probe.

This is an XNU display-backend animation, not a UIKit or optical panel benchmark.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

NUM = r'(0x[0-9a-f]+|\d+)'

def check(record, raw, interrupted=False):
    text = raw.decode(errors='replace')
    events = list(dict.fromkeys(line[line.index('lemonade-display: '):].strip()
                               for line in text.splitlines() if 'lemonade-display: ' in line))
    log = '\n'.join(events)
    def one(pattern):
        matches = re.findall(pattern, log)
        return tuple(int(v, 0) for v in matches[0]) if len(matches) == 1 else None
    measured = one(r'refresh measured valid=(\d+) edges=(\d+) elapsed_us=(\d+) millihz=(\d+)')
    animated = one(r'animation summary completed=(\d+) changed=(\d+) intervals=(\d+) elapsed_us=(\d+) millifps=(\d+) min_us=(\d+) max_us=(\d+)')
    flip = one(r'flip completed=(\d+)/(\d+) frames=(\d+) MISR='+NUM+'/'+NUM+r' fsr='+NUM+r' global='+NUM+r' underrun=(\d+)')
    irq = one(r'final irq_count=(\d+) unexpected='+NUM+r' presents=(\d+) late=(\d+) dropped=(\d+)')
    bootargs = list(dict.fromkeys(re.findall(r'^cmdline: (.*)$', text, re.M)))
    args = bootargs[0].split() if len(bootargs) == 1 else []
    declared = record.get('cmdline', '').split()
    required = ['lemonade_display=8', 'lemonade_display_hz=120', 'lemonade_panel=655', 'ufs_write=0']
    switches = re.findall(r'refresh switch requested=120 commands=(\d+) sync='+NUM+r' height=(\d+) external_te=(\d+)', log)
    samples = re.findall(r'animation n=(\d+) us=(\d+) frame=(\d+) MISR='+NUM+'/'+NUM, log)
    checks = {
        'log_integrity': hashlib.sha256(raw).hexdigest() == record.get('log_sha256'),
        'kernel_identified': bool(re.fullmatch(r'[0-9a-f]{64}', record.get('kernel_sha256', ''))),
        'exact_experimental_scope': record.get('device') == 'lemonade' and record.get('display') == 'manual' and
            record.get('display_hz_requested') == 120 and all(x in args and x in declared for x in required),
        'returned_without_panic': bool(re.search(r'result: returned \d+ 0(?:\s|$)', record.get('result', ''))) and
            not re.search(r'panic\(cpu|Debugger called: <panic>', text, re.I),
        'no_driver_error': not re.search(r'refused|failed|mismatch|context restore offset=', log),
        'external_te_retained': len(switches) == 1 and switches[0][0] == '1' and switches[0][3] == '1' and
            int(switches[0][1], 0) == 0x180043 and int(switches[0][2]) > 20000,
        'measured_te_120hz': bool(measured and measured[0] == 1 and measured[1] >= 64 and 118000 <= measured[3] <= 122000 and
            abs(measured[1] * 1e9 / measured[2] - measured[3]) < 5),
        'sustained_changing_120fps': bool(animated and animated[0] == 1000 and animated[1:3] == (999, 999) and
            118000 <= animated[4] <= 122000 and 6000 <= animated[5] <= animated[6] <= 20000 and
            abs(animated[2] * 1e9 / animated[3] - animated[4]) < 5),
        'one_completed_transfer_per_frame': bool(flip and flip[:3] == (1000, 1000, 1000) and
            not (flip[5] & 0xc00001fe) and flip[6:] == (0, 0)),
        'hardware_signatures_observed': len(samples) >= 12 and len({s[0] for s in samples}) == len(samples) and
            len({s[3:] for s in samples}) >= 12,
        'interrupts_clean': bool(irq and irq[0] >= 1000 and irq[1:] == (0, 0, 0, 0)),
        'panel_60hz_restored': 'refresh restored hz=60 commands=1 registers=1' in log,
        'original_pixels_before_unmap': 'source restore pixels=1 address=1/1 ' in log and
            log.find('refresh restored hz=60') < log.find('source restore pixels=1') < log.find('mapping restore '),
        'context_and_tlb_restored': bool(re.search(r'mapping restore registers=1 tlb=1 fsr=(?:0x400|0) global=0(?:\s|$)', log)),
        'automatic_transfer_restored': 'manual restored sync=1 auto=1' in log,
        'measurement_controls_restored': 'MISR control restored=1/1 status=0/0 ' in log,
        'interrupt_state_restored': 'irq restored tear_enable=0 gic_enable=0 route=0' in log,
        'return_gate_closed': 'quiesced; resumed probe access disabled' in log,
    }
    votes = re.findall(r'bus vote (\d+) restored enable=(\d+) expected=(\d+)', log)
    checks['bus_clock_votes_restored'] = {v[0] for v in votes} == {'0', '1'} and all(v[1] == v[2] for v in votes)
    if interrupted:
        cleanup = re.search(r'mapping cleanup stopping=1 mapped=1 flipping=1 source='+NUM+'/'+NUM+
                            r' fsr='+NUM+r' global='+NUM+r' underrun=(\d+)', log)
        checks.pop('sustained_changing_120fps')
        checks.pop('one_completed_transfer_per_frame')
        checks.update({
            'explicit_bounded_interruption': record.get('display_return_test') is True and record.get('seconds') == 6 and
                'loader_return_after=6' in args and 'lemonade_display_flips=1000' in args,
            'interrupted_while_owned_source_active': bool(cleanup and
                int(cleanup[1], 0) in (0x10000000, 0x12000000) and cleanup[1] == cleanup[2] and
                not (int(cleanup[3], 0) & 0xc00001fe) and int(cleanup[4], 0) == 0 and cleanup[5] == '0'),
            'interrupted_before_completion': animated is None and flip is None and '--- loader_return_after expired ---' in text,
            'hardware_signatures_observed': len(samples) >= 8 and len({s[3:] for s in samples}) >= 8,
            'interrupts_clean': bool(irq and irq[0] >= 200 and irq[1:] == (0, 0, 0, 0)),
        })
    return {'passed': all(checks.values()), 'checks': checks,
            'te_hz': measured[3]/1000 if measured else None, 'animation_fps': animated[4]/1000 if animated else None,
            'completed_frames': animated[0] if animated else 0,
            'changed_transitions': animated[1] if animated else 0,
            'max_frame_interval_us': animated[6] if animated else None,
            'scope': 'XNU owned-buffer moving-pattern DPU output; not UIKit/Metal desktop or optical capture'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--interrupted', action='store_true')
    args = parser.parse_args()
    report = check(json.loads(args.log.with_suffix('.manifest.json').read_text()), args.log.read_bytes(), args.interrupted)
    result = json.dumps(report, indent=2) + '\n'
    if args.output: args.output.write_text(result)
    print(result, end='')
    raise SystemExit(0 if report['passed'] else 1)

if __name__ == '__main__': main()
