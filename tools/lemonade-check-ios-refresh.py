#!/usr/bin/env python3
"""Check UIKit clocks against changing, completed DPU frames from one RAM boot.

Reads only the snapshots bound to the run receipt. Physical panel scanout is
measured electrically (TE/DPU); this is not a high-speed optical capture.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def check(record, final, snapshots):
    integrity = hashlib.sha256(final).hexdigest() == record.get('log_sha256')
    integrity &= len(snapshots) == len(record.get('live_logs', []))
    integrity &= all(hashlib.sha256(raw).hexdigest() == entry.get('sha256')
                     for raw, entry in zip(snapshots, record.get('live_logs', [])))
    text = b'\n'.join([*snapshots, final]).decode(errors='replace')
    # IOLog and printf may both carry a driver event; rolling snapshots overlap.
    lines = list(dict.fromkeys(re.sub(r'^.*?(?=(?:lemonade-display:|refresh-app:|refresh-launch:|redwood:))', '', line)
                              for line in text.splitlines()
                              if any(tag in line for tag in ('lemonade-display:', 'refresh-app:', 'refresh-launch:', 'redwood:'))))
    log = '\n'.join(lines)
    def records(pattern, casts=None):
        values = re.findall(pattern, log)
        return [tuple((casts[i] if casts else int)(v) for i, v in enumerate(row)) for row in values]
    measured = records(r'refresh measured valid=(\d+) edges=(\d+) elapsed_us=(\d+) millihz=(\d+)')
    starts = [int(v) for v in re.findall(r'refresh-app: phase=displaylink at_us=(\d+)', log)]
    transitions = [int(v) for v in re.findall(r'refresh-app: phase=coreanimation at_us=(\d+)', log)]
    callbacks = records(r'refresh-app: phase=(\d+) at_us=(\d+) frames=(\d+) samples=(\d+) fps=([\d.]+) '
                        r'min_us=(\d+) max_us=(\d+) late=(\d+) duration_us=(\d+) target_us=(\d+) max_fps=(\d+)',
                        [int, int, int, int, float, int, int, int, int, int, int])
    physical = records(r'direct-window frames=(\d+) changed=(\d+) elapsed_us=(\d+) millifps=(\d+) '
                       r'min_us=(\d+) max_us=(\d+) active_hz=(\d+) at_us=(\d+)')
    final_counts = records(r'direct final completed=(\d+) fallbacks=(\d+) failed=(\d+) mapped_pages=(\d+)')
    irqs = records(r'final irq_count=(\d+) unexpected=(0x[0-9a-f]+|\d+) presents=(\d+) late=(\d+) dropped=(\d+)',
                   [int, lambda v: int(v, 0), int, int, int])
    # A shorter catch-up interval at launch is harmless; reject slow callbacks,
    # and separately demand real changing output paced by hardware completion.
    stable_callbacks = [r for r in callbacks if r[3] >= 590 and 118 <= r[4] <= 122 and
                        0 < r[5] <= r[6] <= 12500 and r[7] == 0 and
                        8300 <= r[8] <= 8400 and 8300 <= r[9] <= 8400 and r[10] == 120]
    phases = {'displaylink': [], 'coreanimation': []}
    if len(starts) == len(transitions) == 1 and callbacks:
        bounds = ((starts[0] + 2_000_000, transitions[0]),
                  (transitions[0] + 1_000_000, max(r[1] for r in callbacks)))
        for name, (begin, end) in zip(phases, bounds):
            phases[name] = [r for r in physical if r[7] - r[2] >= begin and r[7] <= end]
    def changing(windows):
        return len(windows) >= 2 and all(r[0] >= 590 and r[1] >= r[0]-2 and
            4_900_000 <= r[2] <= 5_100_000 and 118000 <= r[3] <= 122000 and
            abs(r[0]*1e9/r[2]-r[3]) < 2 and 6000 <= r[4] <= r[5] <= 20000 and r[6] == 120
            for r in windows)
    checks = {
        'same_run_log_integrity': bool(integrity),
        'kernel_identified': bool(re.fullmatch(r'[0-9a-f]{64}', record.get('kernel_sha256', ''))),
        'direct_desktop_with_fences': record.get('device') == 'lemonade' and record.get('display') == 'direct' and
            record.get('display_hz_requested') == 120 and record.get('display_fences') is True and
            record.get('metal_system') == 'desktop',
        'returned_without_panic': bool(re.search(r'result: returned \d+ 0(?:\s|$)', record.get('result', ''))) and
            not re.search(r'panic\(cpu|Debugger called: <panic>', text, re.I),
        'electrical_te_120hz': len(measured) == 1 and measured[0][0] == 1 and measured[0][1] >= 64 and
            118000 <= measured[0][3] <= 122000 and abs(measured[0][1]*1e9/measured[0][2]-measured[0][3]) < 5,
        'guarded_system_mode_parser': 'single-mode timing parser enabled for measured 120 Hz (protect=0)' in log,
        'system_clock_and_current_mode_120': 'CADisplay refreshRate=0.008333333' in log and
            'CADisplay hardwareRefreshRate=0.008333333' in log and 'CADisplay currentMode rate=120.000000000' in log,
        'real_uikit_application_opened': 'refresh-launch: workspace=1 registered=1 opened=1' in log and
            'refresh-app: start max_fps=120 preferred=120/120/120' in log,
        'displaylink_callbacks_120fps': sum(r[0] == 0 for r in stable_callbacks) >= 2,
        'coreanimation_phase_callbacks_120fps': sum(r[0] == 1 for r in stable_callbacks) >= 3,
        'displaylink_drives_changing_120fps': changing(phases['displaylink']),
        'coreanimation_drives_changing_120fps': changing(phases['coreanimation']),
        'all_transfers_complete_without_fallback': len(final_counts) == 1 and final_counts[0][0] >= 5000 and
            final_counts[0][1:] == (0, 0, 0),
        'display_interrupts_clean': len(irqs) == 1 and irqs[0][0] >= 5000 and irqs[0][1:] == (0, 0, 0, 0),
        'panel_60hz_restored': 'refresh restored hz=60 commands=1 registers=1' in log,
        'original_source_and_context_restored': 'source restore pixels=1 address=1/1 ' in log and
            bool(re.search(r'mapping restore registers=1 tlb=1 fsr=(?:0x400|0) global=0(?:\s|$)', log)),
        'interrupt_state_restored': 'irq restored tear_enable=0 gic_enable=0 route=0' in log,
        'return_gate_closed': 'quiesced; resumed probe access disabled' in log,
    }
    return {'passed': all(checks.values()), 'checks': checks,
            'te_hz': measured[0][3]/1000 if len(measured) == 1 else None,
            'completed_frames': final_counts[0][0] if len(final_counts) == 1 else None,
            'callback_windows': [{'phase': r[0], 'fps': r[4], 'samples': r[3], 'max_interval_us': r[6], 'late': r[7]}
                                 for r in callbacks],
            'physical_windows': {name: [{'frames': r[0], 'changed': r[1], 'fps': r[3]/1000,
                                        'max_interval_us': r[5]} for r in windows] for name, windows in phases.items()},
            'scope': 'Real UIKit application and Core Animation; DPU completion/MISR and electrical TE; no optical capture'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    record = json.loads(args.log.with_suffix('.manifest.json').read_text())
    snapshots = []
    for entry in record.get('live_logs', []):
        path = (ROOT / entry['path']).resolve()
        if not path.is_relative_to((ROOT / 'local/lemonade').resolve()):
            raise SystemExit('Snapshot outside the private device evidence directory')
        snapshots.append(path.read_bytes())
    result = check(record, args.log.read_bytes(), snapshots)
    rendered = json.dumps(result, indent=2) + '\n'
    if args.output: args.output.write_text(rendered)
    print(rendered, end='')
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
