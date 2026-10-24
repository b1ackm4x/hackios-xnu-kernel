#!/usr/bin/env python3
"""Falsification scenarios for direct-display evidence; no hardware access."""
import hashlib
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('direct', Path(__file__).with_name('lemonade-check-direct.py'))
direct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(direct)


def fixture():
    cmdline = 'lemonade_display=10 fb_fences=1 ufs_write=0'
    lines = ['cmdline: ' + cmdline]
    for n in range(1, 121):
        t = n * 10000
        lines += [f'tl: q {n} at {t}', f'tl: v {n} ready {t+10} shown {t+20} done {t+30}',
                  f'tl: r {n} at {t+40}', f'tl: wr {n} 0x80000000 0xe00002d5 at {t+5}',
                  f'tl: wr {n} 0 0 at {t+50}']
        if n <= 8 or n == 120:
            lines += [f'lemonade-display: direct swap={n} surface=7 count={n} pitch=4608 pages=2741 frame_delta=1 done=0x100 intf={hex(n*77)} unmapped=1 us=16000']
    for pipe, xy, phase in ((0, 0, 43690), (1, 562, 1092086)):
        lines += [f'lemonade-display: scaler readback pipe={pipe} size=0x9840233 xy={xy} pitch=4608 op=0x20034401 step=2184533/2128609 preload=0x3050305 phase={phase}']
    lines += ['lemonade-display: direct final completed=120 fallbacks=0 failed=0 mapped_pages=0',
              'lemonade-display: mapping cleanup stopping=1 mapped=1 flipping=1 source=0x10000000/0x10000000 fsr=0x400 global=0 underrun=0',
              'lemonade-display: scaler restored registers=1 count=50 before_gop=1',
              'lemonade-display: manual restored sync=1 auto=1',
              'lemonade-display: source restore pixels=1 address=1/1 flush=0',
              'lemonade-display: mapping restore registers=1 tlb=1 fsr=0x400 global=0',
              'lemonade-display: MISR control restored=1/1 status=0/0 prior_status=0/0',
              'lemonade-display: interface MISR restored=1 status=0 prior_status=0',
              'lemonade-display: final irq_count=200 unexpected=0 presents=0 late=0 dropped=0',
              'lemonade-display: irq restored tear_enable=0 gic_enable=0 route=0',
              'lemonade-display: bus vote 1 restored enable=0 expected=0',
              'lemonade-display: bus vote 0 restored enable=0 expected=0',
              'lemonade-display: quiesced; resumed probe access disabled']
    record = dict(device='lemonade', kernel_sha256='a'*64, display='direct', display_fences=True,
                  cmdline=cmdline, result='result: returned 19 0', live_logs=[])
    return record, '\n'.join(lines).encode()


class DirectEvidence(unittest.TestCase):
    def check(self, record, raw, live=()):
        record = dict(record, log_sha256=hashlib.sha256(raw).hexdigest())
        return direct.check(record, raw, live)

    def test_complete_transfer_and_release_evidence(self):
        r, raw = fixture()
        result = self.check(r, raw)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['completed_direct_frames'], 120)

    def test_missing_intermediate_frame_cannot_pass_as_timed_return(self):
        r, raw = fixture()
        raw = b'\n'.join(line for line in raw.splitlines() if not line.startswith((b'tl: q 70 ', b'tl: v 70 ', b'tl: r 70 ', b'tl: wr 70 ')))
        result = self.check(r, raw)
        self.assertFalse(result['checks']['complete_retirement_trace'])

    def test_last_completed_surface_may_remain_held_at_return(self):
        r, raw = fixture()
        raw = b'\n'.join(line for line in raw.splitlines() if not line.startswith((b'tl: r 120 ', b'tl: wr 120 0 0 ')))
        self.assertTrue(self.check(r, raw)['passed'])

    def test_false_completion_mapping_or_restoration_is_rejected(self):
        changes = [(b'done=0x100', b'done=0'), (b'unmapped=1', b'unmapped=0'),
                   (b'frame_delta=1', b'frame_delta=2'), (b'fsr=0x400', b'fsr=0x402'),
                   (b'pixels=1', b'pixels=0'), (b'tlb=1', b'tlb=0'),
                   (b'preload=0x3050305', b'preload=0x2040204'),
                   (b'phase=1092086', b'phase=43690'), (b'auto=1', b'auto=0'),
                   (b'completed=120', b'completed=900'), (b'fallbacks=0', b'fallbacks=1')]
        for old, new in changes:
            with self.subTest(change=old):
                r, raw = fixture()
                self.assertFalse(self.check(r, raw.replace(old, new, 1))['passed'])

    def test_early_producer_release_is_rejected(self):
        r, raw = fixture()
        raw = raw.replace(b'tl: r 70 at 700040', b'tl: r 70 at 700015')
        result = self.check(r, raw)
        self.assertFalse(result['checks']['release_and_wait_order'])

    def test_live_log_integrity_and_mode_are_required(self):
        r, raw = fixture()
        r['live_logs'] = [{'sha256': 'b'*64}]
        self.assertFalse(self.check(r, raw, [b'unverified'])['checks']['all_logs_integrity'])
        r, raw = fixture()
        r['display'] = 'sync'
        self.assertFalse(self.check(r, raw)['passed'])


if __name__ == '__main__':
    unittest.main()
