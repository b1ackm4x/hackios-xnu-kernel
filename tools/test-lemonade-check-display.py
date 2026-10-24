#!/usr/bin/env python3
"""Reject incomplete, contradictory, or tampered hardware acceptance records."""
import hashlib
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('display_check', Path(__file__).with_name('lemonade-check-display.py'))
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

LOG = '''lemonade-display: identity table verified pages=2532 tables=20 guards=unmapped
lemonade-display: buffer 0 pages=2532 above4G=2532 iova=0x10000000 guards=unmapped
lemonade-display: buffer 1 pages=2532 above4G=2532 iova=0x12000000 guards=unmapped
lemonade-display: initial MISR=0x1111/0x2222
lemonade-display: mapping samples=32/32 frames=64 MISR=0x1111/0x2222 fsr=0x400 global=0 sctlr=0x127
lemonade-display: buffer 0 MISR=0x3333/0x3333 flush=0x20000000
lemonade-display: buffer 1 MISR=0x4444/0x4444 flush=0x20000000
lemonade-display: flip completed=64/64 frames=128 MISR=0x4444/0x4444 fsr=0x400 global=0 underrun=0
lemonade-display: source restore pixels=1 address=1/1 flush=0x20000000
lemonade-display: mapping restore registers=1 tlb=1 fsr=0x400 global=0
lemonade-display: MISR control restored=1/1 status=0/0 prior_status=0x200/0x200
lemonade-display: final irq_count=229 unexpected=0 presents=0 late=0 dropped=0
lemonade-display: irq restored tear_enable=0 gic_enable=0 route=0
lemonade-display: bus vote 1 restored enable=0 expected=0
lemonade-display: bus vote 0 restored enable=0 expected=0
'''


class DisplayEvidence(unittest.TestCase):
    def checked(self, log=LOG, interrupted=False, **overrides):
        raw = log.encode()
        record = {'device': 'lemonade', 'display': 'flip', 'kernel_sha256': 'a' * 64,
                  'log_sha256': hashlib.sha256(raw).hexdigest(), 'result': 'result: returned 2048 0\n',
                  'cmdline': 'ufs_write=0 lemonade_display=7 lemonade_display_flips=64'}
        record.update(overrides)
        return checker.check(record, raw, interrupted)

    def interrupted(self, log=None, **overrides):
        if log is None:
            log = self.interrupted_log()
        record = {'seconds': 3, 'cmdline': 'ufs_write=0 loader_return_after=3 lemonade_display=7 lemonade_display_flips=64'}
        record.update(overrides)
        return self.checked(log, interrupted=True, **record)

    def interrupted_log(self):
        line = 'lemonade-display: flip completed=64/64 frames=128 MISR=0x4444/0x4444 fsr=0x400 global=0 underrun=0\n'
        snapshot = ('--- loader_return_after expired ---\n'
                    'lemonade-display: mapping cleanup stopping=1 mapped=1 flipping=1 source=0x12000000/0x12000000 '
                    'fsr=0x400 global=0 underrun=0\n')
        return LOG.replace(line, snapshot) + 'lemonade-display: quiesced; resumed probe access disabled\n'

    def test_valid_output_and_read_only_fsr_bits(self):
        self.assertTrue(self.checked()['passed'])
        self.assertTrue(self.checked(LOG + LOG)['passed'])

    def test_rejects_tampered_or_wrong_device_record(self):
        for change in ({'log_sha256': '0' * 64}, {'device': 'other'}, {'display': 'sync'},
                       {'result': 'result: panic 1 1'}, {'kernel_sha256': ''}):
            self.assertFalse(self.checked(**change)['passed'])

    def test_rejects_false_completion_and_unmapped_buffers(self):
        for old, new in (('completed=64/64', 'completed=63/64'), ('frames=128', 'frames=0'),
                         ('above4G=2532', 'above4G=0'), ('iova=0x12000000', 'iova=0xe4d00000')):
            self.assertFalse(self.checked(LOG.replace(old, new))['passed'])

    def test_rejects_fault_underrun_and_failed_rollback(self):
        for old, new in (('fsr=0x400', 'fsr=0x402'), ('global=0', 'global=2'), ('underrun=0', 'underrun=1'),
                         ('registers=1', 'registers=0'), ('tlb=1', 'tlb=0'), ('pixels=1', 'pixels=0'),
                         ('control restored=1/1', 'control restored=1/0'), ('late=0', 'late=1')):
            self.assertFalse(self.checked(LOG.replace(old, new))['passed'])

    def test_rejects_stale_picture(self):
        self.assertFalse(self.checked(LOG.replace('buffer 1 MISR=0x4444/0x4444', 'buffer 1 MISR=0x3333/0x3333'))['passed'])

    def test_rejects_late_contradictory_result(self):
        self.assertFalse(self.checked(LOG + 'lemonade-display: mapping restore registers=0 tlb=1 fsr=0x400 global=0\n')['passed'])

    def test_original_picture_must_precede_unmap(self):
        line = 'lemonade-display: source restore pixels=1 address=1/1 flush=0x20000000\n'
        self.assertFalse(self.checked(LOG.replace(line, '') + line)['passed'])

    def test_rejects_missing_evidence(self):
        for line in LOG.splitlines():
            self.assertFalse(self.checked(LOG.replace(line + '\n', ''))['passed'], line)

    def test_interrupted_return_needs_separate_acceptance(self):
        self.assertTrue(self.interrupted()['passed'])
        self.assertFalse(self.checked(self.interrupted_log())['passed'])
        self.assertFalse(self.interrupted(LOG)['passed'])

    def test_rejects_pre_cleanup_faults_even_if_restored_state_is_clean(self):
        log = self.interrupted_log()
        for old, new in (('source=0x12000000/0x12000000 fsr=0x400', 'source=0x12000000/0x12000000 fsr=0x402'),
                         ('global=0 underrun=0', 'global=1 underrun=0'),
                         ('underrun=0', 'underrun=1')):
            self.assertFalse(self.interrupted(log.replace(old, new))['passed'])

    def test_requires_actual_interrupted_owned_scanout(self):
        log = self.interrupted_log()
        for old, new in (('stopping=1', 'stopping=0'), ('mapped=1', 'mapped=0'), ('flipping=1', 'flipping=0'),
                         ('source=0x12000000/0x12000000', 'source=0xe4d00000/0xe4d00000'),
                         ('source=0x12000000/0x12000000', 'source=0x12000000/0x10000000'),
                         ('--- loader_return_after expired ---', ''),
                         ('quiesced; resumed probe access disabled', '')):
            self.assertFalse(self.interrupted(log.replace(old, new))['passed'])
        for line in log.splitlines():
            self.assertFalse(self.interrupted(log.replace(line+'\n', ''))['passed'], line)

    def test_interrupted_cleanup_must_follow_timer(self):
        log = self.interrupted_log()
        timer = '--- loader_return_after expired ---\n'
        self.assertFalse(self.interrupted(log.replace(timer, '')+timer)['passed'])

    def test_normal_return_also_checks_pre_cleanup_faults(self):
        snapshot = ('lemonade-display: mapping cleanup stopping=0 mapped=1 flipping=1 '
                    'source=0x12000000/0x12000000 fsr=0x400 global=0 underrun=0\n')
        self.assertTrue(self.checked(LOG+snapshot)['passed'])
        self.assertFalse(self.checked(LOG+snapshot.replace('fsr=0x400', 'fsr=0x402'))['passed'])

    def test_manual_commands_require_idle_and_one_completed_transfer_each(self):
        setup = ('lemonade-display: manual configured=1 idle=1 sync=0x180085 auto=0x1 status=0\n'
                 'lemonade-display: manual idle frames=0 done=0 line=1 tear=2400\n'
                 'lemonade-display: manual transfer n=1 frame_delta=1 done=0x100 MISR=0x3333/0x3333\n'
                 'lemonade-display: manual transfer n=2 frame_delta=1 done=0x100 MISR=0x4444/0x4444\n'
                 'lemonade-display: manual transfer n=64 frame_delta=1 done=0x100 MISR=0x4444/0x4444\n')
        cleanup = ('lemonade-display: mapping cleanup stopping=0 mapped=1 flipping=1 '
                   'source=0x12000000/0x12000000 fsr=0x400 global=0 underrun=0\n'
                   'lemonade-display: manual restored sync=1 auto=1\n')
        source = 'lemonade-display: source restore '
        log = setup + LOG.replace('frames=128', 'frames=64').replace(source, cleanup+source)
        def checked(value):
            return self.checked(value, display='manual', cmdline='ufs_write=0 lemonade_display=8 lemonade_display_flips=64')['passed']
        self.assertTrue(checked(log))
        for old,new in (('configured=1','configured=0'),('auto=0x1','auto=0x80000001'),
                        ('idle frames=0','idle frames=1'),('done=0 line=1','done=0x300 line=1'),
                        ('sync=1 auto=1','sync=1 auto=0'),('frames=64 MISR=0x4444','frames=128 MISR=0x4444'),
                        ('done=0x100','done=0'),('frame_delta=1','frame_delta=2'),('transfer n=64','transfer n=63')):
            self.assertFalse(checked(log.replace(old,new)))
        restore = 'lemonade-display: manual restored sync=1 auto=1\n'
        self.assertFalse(checked(log.replace(restore,'')+restore))
        self.assertFalse(checked(log+'lemonade-display: manual configured=0 idle=0 sync=0 auto=0 status=0\n'))

    def test_scaler_needs_native_comparison_and_full_geometry_restoration(self):
        setup = ('lemonade-display: manual configured=1 idle=1 sync=0x180085 auto=0x1 status=0\n'
                 'lemonade-display: manual idle frames=0 done=0 line=1 tear=2400\n'
                 'lemonade-display: scaler configured=1 source=1125x2436 pitch=4544 output=1080x2400 overlap=1 step=2184533/2128609 phase=43690/1092086/15728 pattern=0 padding=poisoned\n')
        for n, phase in enumerate((43690,1092086)):
            setup += f'lemonade-display: scaler readback pipe={n} size=0x9840233 xy={n*562} pitch=4544 op=0x20034401 step=2184533/2128609 preload=0x3050305 phase={phase}\n'
        for n, value in enumerate(('0x3333', '0x4444')):
            setup += f'lemonade-display: scaler native buffer={n} MISR={value}/{value} intf={value} status=0x81000301\n'
            setup += f'lemonade-display: scaler interface buffer={n} actual={value} expected={value} status=0x81000301\n'
            setup += f'lemonade-display: scaler compare buffer={n} native_mixer_match=1 actual={value}/{value} expected={value}/{value}\n'
        for n, value in ((1,'0x3333'), (2,'0x4444'), (64,'0x4444')):
            setup += f'lemonade-display: manual transfer n={n} frame_delta=1 done=0x100 MISR={value}/{value}\n'
        cleanup = ('lemonade-display: mapping cleanup stopping=0 mapped=1 flipping=1 source=0x12000000/0x12000000 fsr=0x400 global=0 underrun=0\n'
                   'lemonade-display: scaler restored registers=1 count=50 before_gop=1\n'
                   'lemonade-display: interface MISR restored=1 status=0 prior_status=0\n'
                   'lemonade-display: manual restored sync=1 auto=1\n')
        log = setup + LOG.replace('buffer 0 pages=2532','buffer 0 pages=2703').replace('buffer 1 pages=2532','buffer 1 pages=2703').replace('frames=128','frames=64').replace('lemonade-display: source restore ', cleanup+'lemonade-display: source restore ')
        def checked(value):
            return self.checked(value, display='scale', cmdline='ufs_write=0 lemonade_display=9 lemonade_display_flips=64 lemonade_display_pattern=0')['passed']
        self.assertTrue(checked(log))
        for old,new in (('native_mixer_match=1','native_mixer_match=0'), ('count=50','count=48'), ('pitch=4544','pitch=4320'),
                        ('expected=0x4444/0x4444','expected=0x4445/0x4444'), ('actual=0x3333/0x3333','actual=0x3333/0x3334'),
                        ('scaler restored registers=1','scaler restored registers=0'), ('padding=poisoned','padding=uniform'), ('status=0x81000301','status=0x81000101'), ('interface MISR restored=1','interface MISR restored=0'), ('preload=0x3050305','preload=0x2040204')):
            self.assertFalse(checked(log.replace(old,new)), (old,new))
        for line in setup.splitlines()+cleanup.splitlines():
            self.assertFalse(checked(log.replace(line+'\n','')), line)
        # A real alpha-only mixer difference is allowed only when independently
        # measured final interface output remains exact and the observations agree.
        alpha = log.replace('buffer=0 MISR=0x3333/0x3333 intf=', 'buffer=0 MISR=0x7777/0x7777 intf=')
        alpha = alpha.replace('buffer=0 native_mixer_match=1 actual=0x3333/0x3333 expected=0x3333/0x3333',
                              'buffer=0 native_mixer_match=0 actual=0x3333/0x3333 expected=0x7777/0x7777')
        self.assertTrue(checked(alpha))
        self.assertFalse(checked(alpha.replace('buffer=0 actual=0x3333 expected=0x3333 status=',
                                             'buffer=0 actual=0x3334 expected=0x3333 status=')))
        restore='lemonade-display: scaler restored registers=1 count=50 before_gop=1\n'
        self.assertFalse(checked(log.replace(restore,'')+restore))


if __name__ == '__main__':
    unittest.main()
