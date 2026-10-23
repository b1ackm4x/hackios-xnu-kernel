#!/usr/bin/env python3
"""Reject false-positive PCIe acceptance records; never accesses a device."""
import hashlib
import importlib.util
from pathlib import Path
import re
import unittest

spec = importlib.util.spec_from_file_location('wifi_check', Path(__file__).with_name('lemonade-check-wifi.py'))
wifi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wifi)


class StageEvidence(unittest.TestCase):
    def test_smmu_format_bits_are_not_faults(self):
        source = (Path(__file__).resolve().parents[1] / 'xnu/files/bsd/dev/lemonade_wifi.c').read_text()
        mask = int(re.search(r'#define WIFI_FSR_FAULT (0x[0-9a-f]+)u', source)[1], 16)
        for no_fault in (0, 0x200, 0x400, 0x600):
            self.assertEqual(no_fault & mask, 0)
        for fault in (0x402, 0x408, 0x410, 0x80000402):
            self.assertNotEqual(fault & mask, 0)

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

    def dma_record(self, missing='', extra=''):
        evidence = ('stage 9 radio rails and WLAN_EN ready;\n'
                    'WLAN_EN/CLKREQ restored; endpoint held off\n'
                    'stage 10 PCIe link ready; DMA disabled\n'
                    'endpoint id 0x110317cb command 0 class/revision 0x2800001\n'
                    'stage 12 BAR/MHI registers readable; endpoint bus master disabled\n'
                    'endpoint BAR and memory decode restored\n'
                    'stage 13 SMMU survey complete; SMMU writes=0\n'
                    'stage 14 BDF 0x100 -> SID 0x1c01 hash 0x15 table verified; bus master disabled\n'
                    'BDF/SID table restored after endpoint reset\n'
                    'stage 15 SMMU configured; table walk verified 128 pages and unmapped guards; bus master disabled\n'
                    'DMA context/stream release verified; translation registers restored after endpoint reset\n')
        return self.record(mode='dma', extra=evidence + extra, missing=missing)

    def test_dma_requires_all_pages_and_guards(self):
        self.assertTrue(wifi.check(*self.dma_record())['passed'])
        for marker in ('128 pages', 'unmapped guards', 'hash 0x15 table verified'):
            self.assertFalse(wifi.check(*self.dma_record(missing=marker))['passed'])

    def test_dma_requires_restoration(self):
        self.assertFalse(wifi.check(*self.dma_record(missing='DMA context/stream release verified'))['passed'])
        self.assertFalse(wifi.check(*self.dma_record(extra='DMA cleanup failed'))['passed'])

    def test_any_later_failure_overrides_pass(self):
        self.assertFalse(wifi.check(*self.dma_record(extra='wifi-lemonade: stage 15 failed;'))['passed'])

    def test_real_dma_requires_fault_and_shutdown_evidence(self):
        manifest, raw = self.dma_record()
        manifest['wifi'] = 'dma-fault'
        manifest['cmdline'] = 'wifi=0 lemonade_wifi=16'
        raw = raw.replace(b'stage 15 passed;', b'stage 16 passed;')
        manifest['log_sha256'] = hashlib.sha256(raw).hexdigest()
        self.assertFalse(wifi.check(manifest, raw)['passed'])
        raw += (b'stage 16 endpoint DMA guard blocked by owned SMMU context; SID 0x1c01 confirmed\n'
                b'DMA guard result FSR 0x80000402 FAR 0xa0084000 SID 0x1c01 global 0\n'
                b'endpoint bus master clear verified\n'
                b'endpoint DMA stopped before unmap\n')
        manifest['log_sha256'] = hashlib.sha256(raw).hexdigest()
        self.assertTrue(wifi.check(manifest, raw)['passed'])
        self.assertFalse(wifi.check(manifest, raw)['networking_verified'])
        wrong_sid = raw.replace(b'SID 0x1c01 global', b'SID 0x1c00 global')
        manifest['log_sha256'] = hashlib.sha256(wrong_sid).hexdigest()
        self.assertFalse(wifi.check(manifest, wrong_sid)['passed'])

    def test_firmware_success_requires_sbl_and_no_real_fault(self):
        manifest, raw = self.dma_record()
        manifest.update(wifi='bhi', cmdline='wifi=0 lemonade_wifi=17', kernel_sha256='same-kernel',
                        dma_guard={'kernel_sha256': 'same-kernel'},
                        wifi_firmware={'firmware': {'amss.bin': {'sha256':
                            '71e701a8cd8b5d37da939e5eccc4f7786c1f031eb92084b6a616bc0f6c1ed521'}}})
        raw = raw.replace(b'stage 15 passed;', b'stage 17 passed;')
        raw += (b'endpoint bus master clear verified\nendpoint DMA stopped before unmap\n'
                b'BHI result status 0x80000001 EE 1 MHI 0x101 FSR 0x400 global 0 error 0\n'
                b'stage 17 BHI transfer complete; execution environment SBL; no SMMU fault\n')
        manifest['log_sha256'] = hashlib.sha256(raw).hexdigest()
        self.assertTrue(wifi.check(manifest, raw)['passed'])
        for bad in (raw.replace(b'EE 1 ', b'EE 0 '), raw.replace(b'FSR 0x400 ', b'FSR 0x402 ')):
            manifest['log_sha256'] = hashlib.sha256(bad).hexdigest()
            self.assertFalse(wifi.check(manifest, bad)['passed'])


    def test_mhi_requires_device_event_and_bhie_requires_mission_event(self):
        manifest, raw = self.dma_record()
        manifest.update(wifi='mhi', cmdline='wifi=0 lemonade_wifi=18', kernel_sha256='same-kernel',
                        dma_guard={'kernel_sha256': 'same-kernel'},
                        wifi_firmware={'firmware': {'amss.bin': {'sha256':
                            '71e701a8cd8b5d37da939e5eccc4f7786c1f031eb92084b6a616bc0f6c1ed521'}}})
        raw = raw.replace(b'stage 15 passed;', b'stage 18 passed;')
        raw += (b'endpoint bus master clear verified\nendpoint DMA stopped before unmap\n'
                b'BHI result status 0x80000001 EE 1 MHI 0x101 FSR 0x400 global 0 error 0\n'
                b'stage 17 BHI transfer complete; execution environment SBL; no SMMU fault\n'
                b'MHI DMA arena verified 2048 pages with outer guards; control uses uncached alias\n'
                b'MHI ready result status 0x201 EE 1 events 2 M0events 1 EEevents 1 FSR 0x400 global 0\n'
                b'stage 18 MHI M0 confirmed by device-written event; polling transport ready\n')
        manifest['log_sha256'] = hashlib.sha256(raw).hexdigest()
        self.assertTrue(wifi.check(manifest, raw)['passed'])
        for bad in (raw.replace(b'M0events 1', b'M0events 0'), raw.replace(b'FSR 0x400', b'FSR 0x402'),
                    raw + b'MHI event ring 0 invalid read pointer 0xa0203021\n'):
            manifest['log_sha256'] = hashlib.sha256(bad).hexdigest()
            self.assertFalse(wifi.check(manifest, bad)['passed'])
        manifest.update(wifi='bhie', cmdline='wifi=0 lemonade_wifi=19')
        raw = raw.replace(b'stage 18 passed;', b'stage 19 passed;')
        raw += (b'BHIE result status 0x80000002 EE 2 MHI 0x201 events 3 mission_event 1 FSR 0x400 global 0\n'
                b'stage 19 full firmware running; mission environment confirmed by event; no SMMU fault\n')
        manifest['log_sha256'] = hashlib.sha256(raw).hexdigest()
        self.assertTrue(wifi.check(manifest, raw)['passed'])
        for bad in (raw.replace(b'mission_event 1', b'mission_event 0'), raw.replace(b'EE 2 MHI', b'EE 3 MHI'),
                    raw.replace(b'BHIE result status 0x80000002', b'BHIE result status 0xc0000002')):
            manifest['log_sha256'] = hashlib.sha256(bad).hexdigest()
            self.assertFalse(wifi.check(manifest, bad)['passed'])

    def transport_record(self, mode):
        manifest, raw = self.dma_record()
        manifest.update(wifi=mode, cmdline='wifi=0 lemonade_wifi=' + str(wifi.MODES[mode]),
                        kernel_sha256='same-kernel', dma_guard={'kernel_sha256': 'same-kernel'},
                        wifi_firmware={'firmware': {'amss.bin': {'sha256':
                            '71e701a8cd8b5d37da939e5eccc4f7786c1f031eb92084b6a616bc0f6c1ed521'}}})
        raw = raw.replace(b'stage 15 passed;', f'stage {wifi.MODES[mode]} passed;'.encode())
        raw += (b'endpoint bus master clear verified\nendpoint DMA stopped before unmap\n'
                b'BHI result status 0x80000001 EE 1 MHI 0x101 FSR 0x400 global 0 error 0\n'
                b'stage 17 BHI transfer complete; execution environment SBL; no SMMU fault\n'
                b'MHI DMA arena verified 2048 pages with outer guards; control uses uncached alias\n'
                b'MHI ready result status 0x201 EE 1 events 2 M0events 1 EEevents 1 FSR 0x400 global 0\n'
                b'stage 18 MHI M0 confirmed by device-written event; polling transport ready\n'
                b'BHIE result status 0x80000002 EE 2 MHI 0x201 events 3 mission_event 1 FSR 0x400 global 0\n'
                b'stage 19 full firmware running; mission environment confirmed by event; no SMMU fault\n'
                b'PCI MSI restore readback verified\n'
                b'IPCR channel 20 START completion 1\nIPCR channel 21 START completion 1\n'
                b'IPCR result tx 2 completed 2 rx 2 hellos 1 services 1 WLFW 1 instance 1 FSR 0x400 global 0\n'
                b'stage 20 IPCR channels 20/21 active; QRTR bidirectional and WLFW service found\n')
        if mode == 'qmi':
            raw += (b'WLFW capability response result 0 error 0\n'
                    b'QMI result response 1 success 1 tx_completed 3 rx 3 FSR 0x400 global 0\n'
                    b'stage 21 WLFW capability request/response successful over QMI; no SMMU fault\n')
        manifest['log_sha256'] = hashlib.sha256(raw).hexdigest()
        return manifest, raw

    def test_ipcr_needs_both_channels_actual_receive_and_completed_send(self):
        manifest, raw = self.transport_record('ipcr')
        self.assertTrue(wifi.check(manifest, raw)['passed'])
        for bad in (raw.replace(b'channel 21 START completion 1', b'channel 21 START completion 0'),
                    raw.replace(b'tx 2 completed 2', b'tx 2 completed 0'),
                    raw.replace(b'completed 2 rx 2', b'completed 2 rx 0'),
                    raw.replace(b'PCI MSI restore readback verified', b'PCI MSI restore readback failed')):
            manifest['log_sha256'] = hashlib.sha256(bad).hexdigest()
            self.assertFalse(wifi.check(manifest, bad)['passed'])

    def test_qmi_reply_with_firmware_error_is_not_success(self):
        manifest, raw = self.transport_record('qmi')
        report = wifi.check(manifest, raw)
        self.assertTrue(report['passed'])
        self.assertFalse(report['networking_verified'])
        for bad in (raw.replace(b'capability response result 0 error 0', b'capability response result 1 error 90'),
                    raw.replace(b'response 1 success 1', b'response 1 success 0'),
                    raw.replace(b'response 1 success 1', b'response 0 success 0')):
            manifest['log_sha256'] = hashlib.sha256(bad).hexdigest()
            self.assertFalse(wifi.check(manifest, bad)['passed'])


if __name__ == '__main__':
    unittest.main()
