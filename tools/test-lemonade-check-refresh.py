#!/usr/bin/env python3
"""Reject requested-only rates, repeated output, missing completions and bad recovery."""
import hashlib
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('refresh_check', Path(__file__).with_name('lemonade-check-refresh.py'))
checker = importlib.util.module_from_spec(spec); spec.loader.exec_module(checker)

ARGS = 'lemonade_display=8 lemonade_display_hz=120 lemonade_panel=655 lemonade_display_flips=1000 ufs_write=0 loader_return_after=20'
PREFIX = 'lemonade-display: '

def fixture():
    lines = [
        'refresh switch requested=120 commands=1 sync=0x180043 height=65535 external_te=1',
        'refresh measured valid=1 edges=64 elapsed_us=533333 millihz=120000',
        'animation summary completed=1000 changed=999 intervals=999 elapsed_us=8325000 millifps=120000 min_us=8200 max_us=8450',
        'flip completed=1000/1000 frames=1000 MISR=0x1234/0x5678 fsr=0x400 global=0 underrun=0',
        'refresh restored hz=60 commands=1 registers=1',
        'manual restored sync=1 auto=1',
        'source restore pixels=1 address=1/1 flush=0x20000000',
        'mapping restore registers=1 tlb=1 fsr=0x400 global=0',
        'MISR control restored=1/1 status=0/0 prior_status=0/0',
        'final irq_count=1171 unexpected=0 presents=0 late=0 dropped=0',
        'irq restored tear_enable=0 gic_enable=0 route=0',
        'bus vote 0 restored enable=0 expected=0', 'bus vote 1 restored enable=0 expected=0',
        'quiesced; resumed probe access disabled',
    ]
    lines += [f'animation n={i} us={i*8333} frame={i} MISR={i}/{i+100}' for i in range(1, 17)]
    raw = ('cmdline: '+ARGS+'\n'+'\n'.join(PREFIX+line for line in lines)).encode()
    record = {'device': 'lemonade', 'display': 'manual', 'display_hz_requested': 120, 'cmdline': ARGS,
              'kernel_sha256': 'a'*64, 'result': 'result: returned 1234 0', 'seconds': 20}
    return record, raw

class Evidence(unittest.TestCase):
    def run_check(self, record, raw):
        record = dict(record, log_sha256=hashlib.sha256(raw).hexdigest())
        return checker.check(record, raw)

    def test_complete_evidence(self):
        self.assertTrue(self.run_check(*fixture())['passed'])

    def test_requested_or_invented_rate_is_not_measurement(self):
        record, raw = fixture()
        for old, new in ((b'valid=1 edges=64', b'valid=0 edges=0'),
                         (b'elapsed_us=533333', b'elapsed_us=1066666'),
                         (b'external_te=1', b'external_te=0'),
                         (b'height=65535', b'height=2400')):
            self.assertFalse(self.run_check(record, raw.replace(old, new))['passed'])

    def test_duplicates_dropped_frames_and_long_stalls_fail(self):
        record, raw = fixture()
        for old, new in ((b'changed=999', b'changed=500'), (b'frames=1000', b'frames=999'),
                         (b'max_us=8450', b'max_us=33000'), (b'underrun=0', b'underrun=1'),
                         (b'fsr=0x400', b'fsr=0x402')):
            self.assertFalse(self.run_check(record, raw.replace(old, new))['passed'])

    def test_recovery_is_mandatory(self):
        record, raw = fixture()
        for old, new in ((b'hz=60 commands=1', b'hz=60 commands=0'), (b'pixels=1', b'pixels=0'),
                         (b'auto=1', b'auto=0'), (b'tlb=1', b'tlb=0'),
                         (b'bus vote 1 restored enable=0', b'bus vote 1 restored enable=1')):
            self.assertFalse(self.run_check(record, raw.replace(old, new))['passed'])

    def test_corrupt_log_and_conflicting_summaries_fail(self):
        record, raw = fixture(); record['log_sha256']='0'*64
        self.assertFalse(checker.check(record, raw)['passed'])
        raw += b'\nlemonade-display: refresh measured valid=1 edges=64 elapsed_us=999999 millihz=64000'
        self.assertFalse(self.run_check(record, raw)['passed'])

if __name__ == '__main__': unittest.main()
