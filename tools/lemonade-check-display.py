#!/usr/bin/env python3
"""Check bounded DPU identity-mapping/dual-buffer evidence, including recovery.

Hardware mixer signatures demonstrate DPU output, not optical panel capture.
Scale mode compares final interface signatures with native uniform/grid references.
Mixer signatures include alpha that is absent from the encoded panel output.
This does not certify arbitrary filtering, IOSurface scanout, or a higher refresh mode.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re

NUMBER = r'(0x[0-9a-f]+|[0-9]+)'
FAULT_MASK = 0xc00001fe


def check(record, raw, interrupted=False):
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
    cleanup = numbers(r'mapping cleanup stopping=(\d+) mapped=(\d+) flipping=(\d+) source=' + NUMBER + '/' + NUMBER +
                      r' fsr=' + NUMBER + r' global=' + NUMBER + r' underrun=(\d+)')
    clocks = re.findall(r'bus vote (\d+) restored enable=(\d+) expected=(\d+)', log)
    summaries = ('mapping samples=', 'mapping restore ', 'MISR control restored=', 'final irq_count=')
    if mode in ('flip', 'manual', 'scale'):
        summaries += ('source restore ', 'mapping cleanup ') if interrupted else ('flip completed=', 'source restore ')
    if mode in ('manual', 'scale'):
        summaries += ('manual configured=', 'manual idle ', 'manual restored ')
        if not interrupted:
            summaries += ('mapping cleanup ',)
    if mode == 'scale':
        summaries += ('scaler configured=', 'scaler restored ', 'scaler native buffer=0 ', 'scaler native buffer=1 ',
                      'scaler compare buffer=0 ', 'scaler compare buffer=1 ', 'scaler interface buffer=0 ', 'scaler interface buffer=1 ', 'interface MISR restored=', 'scaler readback pipe=0 ', 'scaler readback pipe=1 ')
    checks = {
        'lemonade_record': record.get('device') == 'lemonade',
        'known_probe': mode in ('flip', 'manual', 'scale') if interrupted else mode in ('map', 'flip', 'manual', 'scale'),
        'log_integrity': hashlib.sha256(raw).hexdigest() == record.get('log_sha256'),
        'kernel_identified': bool(re.fullmatch(r'[0-9a-f]{64}', record.get('kernel_sha256', ''))),
        'returned_to_loader': bool(re.search(r'result: returned \d+ 0(?:\s|$)', record.get('result', ''))),
        'no_kernel_panic': not re.search(r'panic\(cpu|Debugger called: <panic>|uncaught exception', text, re.I),
        'matching_mode': 'lemonade_display=' + str({'map': 6, 'flip': 7, 'manual': 8, 'scale': 9}.get(mode)) in args,
        'storage_writes_disabled': 'ufs_write=0' in args,
        'no_probe_failure': not re.search(r'refused|failed|mismatch|context restore offset=', log),
        'unambiguous_results': all(sum(line.startswith('lemonade-display: ' + prefix) for line in lines) == 1
                                  for prefix in summaries),
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
    if cleanup is not None:
        checks['pre_cleanup_state_clean'] = not (cleanup[5] & FAULT_MASK) and cleanup[6:] == (0, 0)
    frames = None if interrupted else 0
    completed = None if interrupted else 0
    if mode in ('flip', 'manual', 'scale'):
        expected = [int(a.split('=', 1)[1]) for a in args if re.fullmatch(r'lemonade_display_flips=\d+', a)]
        flipped = numbers(r'flip completed=(\d+)/(\d+) frames=(\d+) MISR=' + NUMBER + '/' + NUMBER +
                          r' fsr=' + NUMBER + r' global=' + NUMBER + r' underrun=(\d+)')
        signatures = [numbers(r'buffer ' + str(n) + r' MISR=' + NUMBER + '/' + NUMBER) for n in range(2)]
        buffers = [numbers(r'buffer ' + str(n) + r' pages=(\d+) above4G=(\d+) iova=' + NUMBER + r' guards=unmapped')
                   for n in range(2)]
        checks.update({
            'distinct_hardware_images': bool(baseline and all(signatures) and signatures[0] != signatures[1]
                                            and all(all(s[i] != baseline[i] for i in (0, 1)) for s in signatures)),
            'owned_high_memory_buffers': all(b and b[0] == (2703 if mode == 'scale' else 2532) and b[1] > 0 and b[2] == 0x10000000 + n * 0x2000000
                                            for n, b in enumerate(buffers)),
            'original_pixels_before_unmap': 'source restore pixels=1 address=1/1 ' in log and
                                           log.index('source restore pixels=1 address=1/1 ') < log.find('mapping restore '),
        })
        if interrupted:
            expired = text.find('--- loader_return_after expired ---')
            observed = text.find('lemonade-display: mapping cleanup ')
            restored_source = text.find('lemonade-display: source restore ')
            checks.update({
                'bounded_interruption_requested': record.get('seconds') in (2, 3) and
                    'loader_return_after=' + str(record.get('seconds')) in args and expected == [64],
                'interrupted_before_completion': flipped is None and not any('flip completed=' in line for line in lines),
                'active_owned_scanout_at_return': bool(cleanup and cleanup[:3] == (1, 1, 1) and
                    cleanup[3] == cleanup[4] and cleanup[3] in (0x10000000, 0x12000000)),
                'no_dma_fault_or_underrun_before_cleanup': bool(cleanup and not (cleanup[5] & FAULT_MASK) and
                    cleanup[6:] == (0, 0)),
                'timer_precedes_cleanup_and_restore': 0 <= expired < observed < restored_source,
                'resumed_access_disabled': 'quiesced; resumed probe access disabled' in log,
            })
        else:
            checks.update({
                'requested_count_completed': bool(flipped and len(expected) == 1 and expected[0] in (64, 1000)
                    and flipped[0] == flipped[1] == expected[0] and flipped[2] >= expected[0]),
                'no_dma_fault_or_underrun': bool(flipped and not (flipped[5] & FAULT_MASK) and flipped[6:] == (0, 0)),
            })
        if flipped:
            completed, frames = flipped[0], flipped[2]
        if mode in ('manual', 'scale'):
            configured = numbers(r'manual configured=(\d+) idle=(\d+) sync=' + NUMBER + r' auto=' + NUMBER + r' status=' + NUMBER)
            idle = numbers(r'manual idle frames=(\d+) done=' + NUMBER + r' line=(\d+) tear=(\d+)')
            restore = log.find('manual restored sync=1 auto=1')
            source = log.find('source restore pixels=1')
            checks.update({
                'manual_transfer_configured': bool(configured and configured[:4] == (1, 1, 0x180085, 1) and
                    not (configured[4] & ((1 << 26) | 0x300))),
                'no_automatic_transfers': bool(idle and idle[:2] == (0, 0)),
                'automatic_refresh_restored_before_pixels': 0 <= restore < source,
            })
            transfers = [numbers(r'manual transfer n=' + str(n) + r' frame_delta=(\d+) done=' + NUMBER +
                                 r' MISR=' + NUMBER + '/' + NUMBER)
                         for n in ((1, 2) if interrupted else (1, 2, expected[0] if len(expected) == 1 else 0))]
            checks['master_pingpong_completed_with_matching_pixels'] = bool(all(transfers) and
                all(t[:2] == (1, 0x100) and t[2:] == signatures[index % 2] for t, index in
                    zip(transfers, (0, 1) if interrupted else (0, 1, (expected[0] - 1) % 2 if len(expected) == 1 else 0))))
            if not interrupted:
                checks['one_transfer_per_command'] = bool(flipped and flipped[0] == flipped[2])
    if mode == 'scale':
        refs = [numbers(r'scaler native buffer=' + str(n) + r' MISR=' + NUMBER + '/' + NUMBER +
                        r' intf=' + NUMBER + r' status=' + NUMBER) for n in range(2)]
        readbacks = [numbers(r'scaler readback pipe=' + str(n) + r' size=' + NUMBER + r' xy=' + NUMBER +
                             r' pitch=' + NUMBER + r' op=' + NUMBER + r' step=' + NUMBER + '/' + NUMBER +
                             r' preload=' + NUMBER + r' phase=' + NUMBER) for n in range(2)]
        outputs = [numbers(r'scaler interface buffer=' + str(n) + r' actual=' + NUMBER + r' expected=' + NUMBER +
                           r' status=' + NUMBER) for n in range(2)]
        comparisons = [numbers(r'scaler compare buffer=' + str(n) + r' native_mixer_match=(\d+) actual=' + NUMBER + '/' + NUMBER +
                               r' expected=' + NUMBER + '/' + NUMBER) for n in range(2)]
        geometry = log.find('scaler restored registers=1 count=50 before_gop=1')
        auto = log.find('manual restored sync=1 auto=1')
        patterns = [int(a.split('=', 1)[1]) for a in args if re.fullmatch(r'lemonade_display_pattern=\d+', a)]
        expected_geometry = 'scaler configured=1 source=1125x2436 pitch=4544 output=1080x2400 overlap=1 step=2184533/2128609 phase=43690/1092086/15728 pattern='
        checks.update({
            'scaler_geometry_configured': len(patterns) == 1 and patterns[0] in (0,1,2,3) and expected_geometry + str(patterns[0]) + ' padding=poisoned' in log,
            'scaler_parameters_read_back': readbacks == [
                (0x9840233, 0, 4544, 0x20034401, 2184533, 2128609, 0x03050305, 43690),
                (0x9840233, 562, 4544, 0x20034401, 2184533, 2128609, 0x03050305, 1092086)],
            'scaled_final_output_matches_native': bool(all(refs) and all(outputs) and refs[0][2] != refs[1][2] and
                all(refs[n][3] & 0x300 == 0x300 and o[2] & 0x300 == 0x300 and
                    o[0] == o[1] == refs[n][2] for n, o in enumerate(outputs))),
            'mixer_observation_consistent': bool(all(refs) and all(comparisons) and
                all(c[1:3] == signatures[n] and c[3:5] == refs[n][:2] and
                    c[0] == int(c[1:3] == c[3:5]) for n, c in enumerate(comparisons))),
            'geometry_restored_before_automatic_gop': 0 <= geometry < auto < log.find('source restore pixels=1'),
            'interface_measurement_restored': 'interface MISR restored=1 status=0 ' in log,
        })
    return {'passed': all(checks.values()), 'mode': mode, 'interrupted': interrupted, 'checks': checks,
            'completed_flips': completed, 'hardware_frames_during_flips': frames,
            'kernel_sha256': record.get('kernel_sha256'), 'log_sha256': record.get('log_sha256')}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--interrupted', action='store_true', help='require a 2/3-second return during active owned scanout, with pre-cleanup fault evidence')
    args = parser.parse_args()
    record = json.loads(args.log.with_suffix('.manifest.json').read_text())
    result = check(record, args.log.read_bytes(), args.interrupted)
    encoded = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(encoded)
    print(encoded, end='')
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
