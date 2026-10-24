#!/usr/bin/env python3
"""Check held-IOSurface DPU transfers, producer release and reversible return.

Each live log must match the final run's recorded hash. These checks establish
command completion and source ownership, not optical panel capture or 120 Hz.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fences', Path(__file__).with_name('lemonade-check-fences.py'))
fences = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fences)
NUMBER = r'(0x[0-9a-f]+|\d+)'


def check(record, raw, live=()):
    expected_live = record.get('live_logs', [])
    live_integrity = len(live) == len(expected_live) and all(
        hashlib.sha256(b).hexdigest() == item.get('sha256') for item, b in zip(expected_live, live))
    joined = b'\n'.join([*live, raw])
    text = joined.decode(errors='replace')
    final_text = raw.decode(errors='replace')
    # Repeated snapshots contain the same events. Dedupe them, preserving the
    # individual swap IDs; never substitute a requested count for hardware work.
    events = list(dict.fromkeys(line[line.index('lemonade-display: '):].strip()
                               for line in text.splitlines() if 'lemonade-display: ' in line))
    log = '\n'.join(events)
    final_mark = final_text.rfind('lemonade-display: direct final ')
    tail = final_text[final_mark:] if final_mark >= 0 else ''
    summary = re.findall(r'direct final completed=(\d+) fallbacks=(\d+) failed=(\d+) mapped_pages=(\d+)', log)
    samples = re.findall(r'direct swap=(\d+) surface=(\d+) count=(\d+) pitch=(\d+) pages=(\d+) frame_delta=(\d+) done='+NUMBER+
                         r' intf='+NUMBER+r' unmapped=(\d+) us=(\d+)', log)
    completed = int(summary[0][0]) if len(summary) == 1 else 0
    irq = re.findall(r'final irq_count=(\d+) unexpected='+NUMBER+r' presents=(\d+) late=(\d+) dropped=(\d+)', log)
    cleanup = re.search(r'mapping cleanup stopping=1 mapped=1 flipping=1 source='+NUMBER+'/'+NUMBER+
                        r' fsr='+NUMBER+r' global='+NUMBER+r' underrun=(\d+)', tail)
    args = record.get('cmdline', '').split()
    actual_cmdlines = list(dict.fromkeys(re.findall(r'^cmdline: (.*)$', text, re.M)))
    actual_args = actual_cmdlines[0].split() if len(actual_cmdlines) == 1 else []
    order = fences.check(record, joined)
    shown_ids = {int(v) for v in re.findall(r'tl: v (\d+) ready ', text)}
    retired_ids = {int(v) for v in re.findall(r'tl: r (\d+) at ', text)}
    total_completed = completed + int(irq[0][2]) if len(irq) == 1 else -1
    complete_trace = len(retired_ids) == total_completed or (len(retired_ids) + 1 == total_completed and
        bool(shown_ids) and shown_ids - retired_ids == {max(shown_ids)})
    fallbacks = re.findall(r'direct fallback count=(\d+) restored=(\d+)', log)
    geometry = re.findall(r'scaler readback pipe=(\d+) size='+NUMBER+r' xy='+NUMBER+r' pitch='+NUMBER+r' op='+NUMBER+
                          r' step='+NUMBER+'/'+NUMBER+r' preload='+NUMBER+r' phase='+NUMBER, log)
    # The shared fence parser checks q -> displayed -> retirement -> wait.
    # Its copy-presenter identity and display counters are replaced below by
    # explicit direct-mode hardware checks. Integrity is checked per input log.
    checks = {k: v for k, v in order['checks'].items() if k not in (
        'synchronized_copy_presenter', 'display_clean', 'log_integrity', 'sustained_frame_evidence')}
    checks.update({
        'all_logs_integrity': hashlib.sha256(raw).hexdigest() == record.get('log_sha256') and live_integrity,
        'direct_mode_requested_and_booted': record.get('display') == 'direct' and
            [a for a in args if a.startswith('lemonade_display=')] == ['lemonade_display=10'] and
            [a for a in actual_args if a.startswith('lemonade_display=')] == ['lemonade_display=10'],
        'sustained_retired_sources': order['retired'] >= 100 and order['queued'] >= order['retired'],
        'complete_retirement_trace': complete_trace,
        'sustained_completed_commands': len(summary) == 1 and completed >= 100 and summary[0][2] == '0',
        'sampled_commands_completed_and_unmapped': len(samples) >= 8 and
            len({m[2] for m in samples}) == len(samples) and max(int(m[2]) for m in samples) > completed - 120 and
            all(int(m[1]) > 0 and 0 < int(m[2]) <= completed and int(m[3]) in (4544, 4608) and
                int(m[4]) == (2703 if int(m[3]) == 4544 else 2741) and int(m[5]) == 1 and
                int(m[6], 0) == 0x100 and int(m[8]) == 1 for m in samples),
        'changing_interface_output': len({m[7] for m in samples}) >= 3,
        'no_direct_failure': not re.search(r'direct failed|manual transfer failed|frame count failed|mapping refused|GPU completion missing|completion unproven', text),
        'fallbacks_restored': len(summary) == 1 and all(v == '1' for _, v in fallbacks) and
            sorted(int(n) for n, _ in fallbacks) == list(range(1, int(summary[0][1]) + 1)),
        'actual_scaler_geometry': len(geometry) >= 2 and {int(m[0]) for m in geometry} == {0, 1} and all(
            tuple(int(v, 0) for v in m[1:3]) == (0x9840233, int(m[0]) * 562) and
            int(m[3], 0) in (4544, 4608) and tuple(int(v, 0) for v in m[4:8]) == (0x20034401, 2184533, 2128609, 0x3050305) and
            int(m[8], 0) == (43690 if m[0] == '0' else 1092086) for m in geometry),
        'irq_and_copy_fallback_clean': len(irq) == 1 and int(irq[0][0]) >= completed and
            int(irq[0][1], 0) == 0 and irq[0][3:] == ('0', '0'),
        'inflight_return_clean': bool(cleanup and tuple(int(v, 0) for v in cleanup.groups()[:2]) == (0x10000000, 0x10000000) and
            not (int(cleanup[3], 0) & 0xc00001fe) and int(cleanup[4], 0) == 0 and cleanup[5] == '0'),
        'geometry_restored_before_gop': 'scaler restored registers=1 count=50 before_gop=1' in tail and
            tail.find('scaler restored ') < tail.find('source restore pixels=1'),
        'gop_output_restored_before_unmap': 'source restore pixels=1 address=1/1 ' in tail and
            tail.find('source restore pixels=1') < tail.find('mapping restore '),
        'context_and_tlb_restored': bool(re.search(r'mapping restore registers=1 tlb=1 fsr=(?:0x400|0) global=0(?:\s|$)', tail)),
        'automatic_transfer_restored': 'manual restored sync=1 auto=1' in tail,
        'measurement_controls_restored': 'MISR control restored=1/1 status=0/0 ' in tail and 'interface MISR restored=1 status=0 ' in tail,
        'return_access_gate_closed': 'quiesced; resumed probe access disabled' in tail,
    })
    return {'passed': all(checks.values()), 'checks': checks, 'completed_direct_frames': completed,
            'cpu_fallback_frames': int(irq[0][2]) if len(irq) == 1 else None,
            'fallback_transitions': int(summary[0][1]) if len(summary) == 1 else None,
            'pages_held_at_return': int(summary[0][3]) if len(summary) == 1 else None,
            'sampled_interface_signatures': len({m[7] for m in samples}),
            'retired_sources': order['retired'], 'queued_sources': order['queued'],
            'busy_results': order['busy_results'], 'successful_waits_after_release': order['successes_after_observed_release'],
            'ordering_errors': order['ordering_errors'], 'kernel_sha256': record.get('kernel_sha256'),
            'log_sha256': record.get('log_sha256'), 'live_logs_verified': live_integrity}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    record = json.loads(args.log.with_suffix('.manifest.json').read_text())
    paths = [(ROOT / item['path']).resolve() for item in record.get('live_logs', [])]
    if any(not p.is_relative_to(ROOT) for p in paths):
        raise SystemExit('Live evidence must remain inside this checkout')
    result = check(record, args.log.read_bytes(), [p.read_bytes() for p in paths])
    encoded = json.dumps(result, indent=2) + '\n'
    if args.output: args.output.write_text(encoded)
    print(encoded, end='')
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
