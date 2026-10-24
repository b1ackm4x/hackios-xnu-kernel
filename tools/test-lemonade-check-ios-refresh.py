#!/usr/bin/env python3
"""Reject misleading refresh evidence without accessing a device."""
import hashlib
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('check_ios_refresh', Path(__file__).with_name('lemonade-check-ios-refresh.py'))
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class RefreshEvidence(unittest.TestCase):
    def fixture(self):
        lines = [
            'lemonade-display: refresh measured valid=1 edges=64 elapsed_us=528053 millihz=121200',
            'redwood: single-mode timing parser enabled for measured 120 Hz (protect=0)',
            'refresh-launch: CADisplay refreshRate=0.008333333',
            'refresh-launch: CADisplay hardwareRefreshRate=0.008333333',
            'refresh-launch: CADisplay currentMode rate=120.000000000',
            'refresh-launch: workspace=1 registered=1 opened=1',
            'refresh-app: start max_fps=120 preferred=120/120/120',
            'refresh-app: phase=displaylink at_us=1000000',
            'refresh-app: phase=coreanimation at_us=21000000 count=2400',
        ]
        for index, at in enumerate((11, 16, 26, 31, 36)):
            phase = 0 if at < 21 else 1
            lines.append(f'refresh-app: phase={phase} at_us={at*1000000} frames={(index+1)*600} '
                         'samples=600 fps=120.000 min_us=8300 max_us=8370 late=0 duration_us=8333 target_us=8333 max_fps=120')
        for at in (10, 15, 28, 33):
            lines.append(f'lemonade-display: direct-window frames=600 changed=600 elapsed_us=5000000 millifps=120000 '
                         f'min_us=8200 max_us=16600 active_hz=120 at_us={at*1000000}')
        lines += ['lemonade-display: direct final completed=6000 fallbacks=0 failed=0 mapped_pages=0',
                  'lemonade-display: final irq_count=12000 unexpected=0 presents=0 late=0 dropped=0',
                  'lemonade-display: refresh restored hz=60 commands=1 registers=1',
                  'lemonade-display: source restore pixels=1 address=1/1 flush=0x20000000',
                  'lemonade-display: mapping restore registers=1 tlb=1 fsr=0x400 global=0',
                  'lemonade-display: irq restored tear_enable=0 gic_enable=0 route=0',
                  'lemonade-display: quiesced; resumed probe access disabled']
        return '\n'.join(lines).encode()

    def result(self, raw, snapshots=None):
        snapshots = snapshots or []
        record = {'device': 'lemonade', 'display': 'direct', 'display_hz_requested': 120,
                  'kernel_sha256': '1'*64,
                  'display_fences': True, 'metal_system': 'desktop', 'result': 'result: returned 100 0\n',
                  'log_sha256': hashlib.sha256(raw).hexdigest(),
                  'live_logs': [{'sha256': hashlib.sha256(p).hexdigest()} for p in snapshots]}
        return checker.check(record, raw, snapshots)

    def test_real_counts_pass_and_overlapping_snapshots_do_not_inflate_counts(self):
        raw = self.fixture()
        expected = self.result(raw)
        self.assertTrue(expected['passed'])
        repeated = self.result(raw, [raw, b'[123.456]: ' + raw.replace(b'\n', b'\n[123.456]: ')])
        self.assertEqual(repeated, expected)

    def test_callback_only_and_repeated_frames_fail(self):
        raw = self.fixture()
        for modified in (raw.replace(b'changed=600', b'changed=0'),
                         raw.replace(b'frames=600 changed=600 elapsed_us=5000000',
                                     b'frames=300 changed=300 elapsed_us=5000000')):
            with self.subTest(modified=modified[:10]):
                self.assertFalse(self.result(modified)['passed'])

    def test_60hz_clock_or_electrical_cadence_cannot_pass(self):
        raw = self.fixture()
        for before, after in ((b'fps=120.000', b'fps=60.000'),
                              (b'elapsed_us=528053 millihz=121200', b'elapsed_us=1066667 millihz=60000'),
                              (b'refreshRate=0.008333333', b'refreshRate=0.016666667')):
            with self.subTest(field=before):
                self.assertFalse(self.result(raw.replace(before, after))['passed'])

    def test_fault_or_unproven_restore_fails(self):
        raw = self.fixture()
        for before, after in ((b'fallbacks=0', b'fallbacks=1'), (b'failed=0', b'failed=1'),
                              (b'mapped_pages=0', b'mapped_pages=2741'),
                              (b'commands=1 registers=1', b'commands=0 registers=1'),
                              (b'tlb=1', b'tlb=0')):
            with self.subTest(field=before):
                self.assertFalse(self.result(raw.replace(before, after))['passed'])

    def test_corrupt_or_missing_snapshot_is_not_accepted(self):
        raw = self.fixture()
        record = {'log_sha256': hashlib.sha256(raw).hexdigest(),
                  'live_logs': [{'sha256': hashlib.sha256(raw).hexdigest()}]}
        for final, snapshots in ((raw+b'changed', [raw]), (raw, []), (raw, [raw+b'changed'])):
            self.assertFalse(checker.check(record, final, snapshots)['checks']['same_run_log_integrity'])


if __name__ == '__main__':
    unittest.main()
