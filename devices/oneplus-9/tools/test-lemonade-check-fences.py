#!/usr/bin/env python3
"""Falsify premature release, false success and incomplete fence evidence."""
import hashlib
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('fences', Path(__file__).with_name('lemonade-check-fences.py'))
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def fixture():
    lines = ['cmdline: ufs_write=0 fb_fences=1 lemonade_display=4',
             'MiniIOMobileFramebuffer: producer release fences enabled (CPU copy, default wait 5000 ms)']
    for n in range(1, 102):
        t = n * 100
        lines += [f'tl: q {n} at {t}', f'tl: wr {n} 0x80000000 0xe00002d5 at {t+1}',
                  f'tl: v {n} ready {t+2} shown {t+3} done {t+10}', f'tl: r {n} at {t+11}',
                  f'tl: wr {n} 0 0 at {t+12}']
    lines += ['lemonade-display: final irq_count=200 unexpected=0 presents=101 late=0 dropped=0',
              'lemonade-display: irq restored tear_enable=0 gic_enable=0 route=0',
              'lemonade-display: bus vote 0 restored enable=0 expected=0',
              'lemonade-display: bus vote 1 restored enable=0 expected=0']
    return '\n'.join(lines) + '\n'


class FenceEvidence(unittest.TestCase):
    def check(self, text, **changes):
        raw = text.encode()
        record = {'device': 'lemonade', 'display': 'sync', 'display_fences': True,
                  'log_sha256': hashlib.sha256(raw).hexdigest(), 'kernel_sha256': 'a' * 64,
                  'result': 'result: returned 5000 0', 'cmdline': 'ufs_write=0 fb_fences=1 lemonade_display=4'}
        record.update(changes)
        return checker.check(record, raw)

    def test_accepts_complete_evidence_and_printf_duplicate(self):
        self.assertTrue(self.check(fixture())['passed'])
        self.assertTrue(self.check(fixture() * 2)['passed'])

    def test_rejects_early_release_and_early_success(self):
        for before, after in [('tl: r 1 at 111', 'tl: r 1 at 109'),
                              ('tl: wr 1 0 0 at 112', 'tl: wr 1 0 0 at 109')]:
            self.assertFalse(self.check(fixture().replace(before, after))['passed'])

    def test_rejects_missing_conflicting_and_malformed_events(self):
        for changed in [fixture().replace('tl: r 1 at 111\n', ''),
                        fixture().replace('tl: q 1 at 100\n', ''),
                        fixture() + 'tl: r 1 at 109\n',
                        fixture() + 'tl: wr garbled\n']:
            self.assertFalse(self.check(changed)['passed'])

    def test_rejects_false_busy_and_blocking_busy(self):
        for old, new in [('0xe00002d5 at 101', '0xe00002d5 at 113'),
                         ('0x80000000 0xe00002d5', '0 0xe00002d5')]:
            self.assertFalse(self.check(fixture().replace(old, new))['passed'])

    def test_rejects_unproven_polling_and_timeout(self):
        self.assertFalse(self.check(fixture().replace('0xe00002d5', '0'))['passed'])
        self.assertFalse(self.check(fixture().replace('tl: wr 1 0 0', 'tl: wr 1 0 0xe00002d6'))['passed'])

    def test_rejects_bad_manifest_crash_and_failed_restoration(self):
        for changes in ({'log_sha256': 'f' * 64}, {'display_fences': False}, {'device': 'other'},
                        {'kernel_sha256': ''}, {'result': 'panic'}, {'cmdline': 'fb_fences=1'}):
            self.assertFalse(self.check(fixture(), **changes)['passed'])
        for old, new in [('unexpected=0', 'unexpected=1'), ('late=0', 'late=1'),
                         ('restored enable=0', 'restored enable=1')]:
            self.assertFalse(self.check(fixture().replace(old, new))['passed'])
        self.assertFalse(self.check(fixture() + 'panic(cpu 0)\n')['passed'])
        self.assertFalse(self.check(fixture() + 'aec548): kmem_alloc failed\nDebugger message: panic\n')['passed'])
        self.assertFalse(self.check(fixture().replace('fb_fences=1', 'fb_fences=0'))['passed'])
        self.assertFalse(self.check(fixture().replace('fb_fences=1', 'fb_fences=1 fb_fences=0'))['passed'])

    def test_drain_cannot_complete_with_any_source_in_use(self):
        self.assertFalse(self.check(fixture() + 'tl: wr 0 0x2 0 at 105\n')['passed'])
        self.assertTrue(self.check(fixture() + 'tl: wr 0 0x2 0 at 10112\n')['passed'])


if __name__ == '__main__':
    unittest.main()
