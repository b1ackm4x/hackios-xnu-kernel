#!/usr/bin/env python3
"""Reject false-positive PCIe acceptance records; never accesses a device."""
import hashlib
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('wifi_check', Path(__file__).with_name('lemonade-check-wifi.py'))
wifi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wifi)


class StageEvidence(unittest.TestCase):
    def record(self, mode='root', extra='', missing=''):
        raw = ('wifi-lemonade: inspection complete; hardware writes=0;\n'
               'wifi-lemonade: stage 7 PHY ready status 0;\n'
               'wifi-lemonade: powered root id 0x10c17cb command 0\n'
               f'wifi-lemonade: stage {wifi.MODES[mode]} passed;\n'
               'wifi-lemonade: GCC votes restored;\n' + extra)
        if missing:
            raw = raw.replace(missing, '')
        raw = raw.encode()
        manifest = {'device': 'lemonade', 'wifi': mode, 'log_sha256': hashlib.sha256(raw).hexdigest(),
                    'result': 'result: returned 123 0', 'cmdline': f'wifi=0 lemonade_wifi={wifi.MODES[mode]}'}
        return manifest, raw

    def test_complete_root_is_not_networking(self):
        report = wifi.check(*self.record())
        self.assertTrue(report['passed'])
        self.assertFalse(report['networking_verified'])

    def test_root_without_phy_is_rejected(self):
        self.assertFalse(wifi.check(*self.record(missing='wifi-lemonade: stage 7 PHY ready '))['passed'])

    def test_timeout_overrides_pass_marker(self):
        self.assertFalse(wifi.check(*self.record(extra='wifi-lemonade: wait failed'))['passed'])

    def test_tampered_log_is_rejected(self):
        manifest, raw = self.record()
        self.assertFalse(wifi.check(manifest, raw + b'changed')['passed'])

    def test_old_root_argument_is_rejected(self):
        manifest, raw = self.record()
        manifest['cmdline'] = 'wifi=0 lemonade_wifi=7'
        self.assertFalse(wifi.check(manifest, raw)['passed'])

    def test_endpoint_requires_bus_master_off(self):
        extra = ('stage 9 radio rails and WLAN_EN ready;\n'
                 'WLAN_EN/CLKREQ restored; endpoint held off\n'
                 'stage 10 PCIe link ready; DMA disabled\n'
                 'endpoint id 0x110317cb command 0 class/revision 0x2800001\n')
        self.assertTrue(wifi.check(*self.record(mode='endpoint', extra=extra))['passed'])
        self.assertFalse(wifi.check(*self.record(mode='endpoint', extra=extra.replace('command 0 ', 'command 0x4 ')))['passed'])


if __name__ == '__main__':
    unittest.main()
