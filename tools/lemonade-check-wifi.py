#!/usr/bin/env python3
"""Validate staged bring-up; networking additionally needs live DHCP and traffic."""
import argparse
import hashlib
import json
from pathlib import Path
import re

MODES = {'inspect': 1, 'rails': 2, 'domain': 3, 'clocks': 4,
         'resets': 5, 'controller': 6, 'phy': 7, 'root': 8, 'radio': 9, 'link': 10, 'endpoint': 11, 'mmio': 12, 'smmu': 13, 'sid': 14, 'dma': 15, 'dma-fault': 16, 'bhi': 17, 'mhi': 18, 'bhie': 19, 'ipcr': 20, 'qmi': 21, 'driver': 22, 'scan': 23, 'station': 24}


def check(manifest, raw):
    log = raw.decode(errors='replace')
    mode = manifest.get('wifi')
    checks = {
        'lemonade_record': manifest.get('device') == 'lemonade',
        'known_stage': mode in MODES,
        'log_integrity': hashlib.sha256(raw).hexdigest() == manifest.get('log_sha256'),
        'returned_to_loader': bool(re.search(r'result: returned \d+ 0(?:\s|$)', manifest.get('result', ''))),
        'read_only_survey_completed': 'wifi-lemonade: inspection complete; hardware writes=0;' in log,
        'no_driver_failure': not re.search(r'wifi-lemonade: (?:wait failed|stage \d+ failed|root-power failed)|request timed out|DMA cleanup failed|restoration failed|release failed', log),
        'no_kernel_panic': not re.search(r'panic\(cpu|Debugger called: <panic>|Debugger message:\s*panic|uncaught exception', log, re.I),
        'redwood_radio_disabled': 'wifi=0' in manifest.get('cmdline', '').split(),
        'matching_stage_argument': ('lemonade_wifi=' + str(MODES.get(mode))) in manifest.get('cmdline', '').split(),
    }
    if mode in MODES and mode != 'inspect':
        checks['requested_stage_completed'] = f'wifi-lemonade: stage {MODES[mode]} passed;' in log
        if MODES[mode] >= 3:
            checks['registers_restored'] = 'wifi-lemonade: GCC votes restored;' in log
        if MODES[mode] >= 7:
            checks['phy_ready_before_dbi'] = 'wifi-lemonade: stage 7 PHY ready ' in log
        if MODES[mode] >= 8:
            checks['root_id_matches_android'] = 'powered root id 0x10c17cb ' in log
        if MODES[mode] >= 9:
            checks['radio_power_and_gpio_ready'] = 'stage 9 radio rails and WLAN_EN ready;' in log
            checks['radio_gpio_restored'] = 'WLAN_EN/CLKREQ restored; endpoint held off' in log
        if MODES[mode] >= 10:
            checks['pcie_link_ready'] = 'stage 10 PCIe link ready; DMA disabled' in log
        if MODES[mode] >= 11:
            checks['endpoint_id_matches_android'] = 'endpoint id 0x110317cb ' in log
            command = re.search(r'endpoint id 0x110317cb command (0x[0-9a-f]+|0) ', log)
            checks['endpoint_bus_master_disabled'] = command is not None and not (int(command[1], 0) & 4)
        if MODES[mode] >= 12:
            checks['mhi_registers_readable'] = 'stage 12 BAR/MHI registers readable; endpoint bus master disabled' in log
            checks['bar_and_decode_restored'] = 'endpoint BAR and memory decode restored' in log
        if MODES[mode] >= 13:
            checks['smmu_survey_without_writes'] = 'stage 13 SMMU survey complete;' in log and 'SMMU writes=0' in log
        if MODES[mode] >= 14:
            checks['sid_table_verified'] = 'stage 14 BDF 0x100 -> SID 0x1c01 hash 0x15 table verified; bus master disabled' in log
            checks['sid_table_restored'] = 'BDF/SID table restored after endpoint reset' in log
        if MODES[mode] >= 15:
            checks['dma_tables_validated'] = 'stage 15 SMMU configured; table walk verified 128 pages and unmapped guards; bus master disabled' in log
            checks['dma_context_restored'] = 'DMA context/stream release verified; translation registers restored after endpoint reset' in log
        if mode == 'dma-fault':
            checks['endpoint_guard_fault_confirmed'] = 'stage 16 endpoint DMA guard blocked by owned SMMU context; SID 0x1c01 confirmed' in log
            fault = re.search(r'DMA guard result FSR (0x[0-9a-f]+|0) FAR (0x[0-9a-f]+|0) SID (0x[0-9a-f]+|0) global (0x[0-9a-f]+|0)', log)
            checks['guard_fault_registers_match'] = bool(fault and int(fault[1], 0) & 2 and
                int(fault[2], 0) == 0xa0084000 and int(fault[3], 0) == 0x1c01 and int(fault[4], 0) == 0)
        if MODES[mode] >= 16:
            checks['dma_stopped_before_unmap'] = 'endpoint DMA stopped before unmap' in log
            checks['endpoint_bus_master_cleared'] = 'endpoint bus master clear verified' in log
        if mode in ('bhi', 'mhi', 'bhie', 'ipcr', 'qmi', 'driver', 'scan', 'station'):
            checks['firmware_reached_sbl'] = 'stage 17 BHI transfer complete; execution environment SBL; no SMMU fault' in log
            status = re.search(r'BHI result status (0x[0-9a-f]+|0) EE (\d+) MHI (0x[0-9a-f]+|0) FSR (0x[0-9a-f]+|0) global (0x[0-9a-f]+|0)', log)
            checks['bhi_status_and_fault_registers'] = bool(status and int(status[1], 0) >> 30 == 2 and
                int(status[2]) == 1 and not (int(status[4], 0) & 0xc00001fe) and int(status[5], 0) == 0)
            checks['guard_evidence_matches_kernel'] = manifest.get('dma_guard', {}).get('kernel_sha256') == manifest.get('kernel_sha256') and bool(manifest.get('kernel_sha256'))
            checks['original_firmware_verified'] = manifest.get('wifi_firmware', {}).get('firmware', {}).get('amss.bin', {}).get('sha256') == '71e701a8cd8b5d37da939e5eccc4f7786c1f031eb92084b6a616bc0f6c1ed521'
        if mode in ('mhi', 'bhie', 'ipcr', 'qmi', 'driver', 'scan', 'station'):
            checks['mhi_dma_arena_verified'] = 'MHI DMA arena verified 2048 pages with outer guards; control uses uncached alias' in log
            checks['mhi_m0_event_confirmed'] = 'stage 18 MHI M0 confirmed by device-written event; polling transport ready' in log
            event = re.search(r'MHI ready result status (0x[0-9a-f]+|0) EE (\d+) events (\d+) M0events (\d+) EEevents (\d+) FSR (0x[0-9a-f]+|0) global (0x[0-9a-f]+|0)', log)
            checks['mhi_registers_and_event_count'] = bool(event and (int(event[1], 0) >> 8) & 255 == 2 and
                int(event[3]) > 0 and int(event[4]) > 0 and not (int(event[6], 0) & 0xc00001fe) and int(event[7], 0) == 0)
            checks['no_mhi_transport_failure'] = not re.search(r'MHI DMA fault|invalid read pointer|unsupported MHI event|IPCR transfer rejected|IPCR command completion mismatch|PCI MSI restore readback failed', log)
        if mode in ('bhie', 'ipcr', 'qmi', 'driver', 'scan', 'station'):
            checks['full_firmware_event_confirmed'] = 'stage 19 full firmware running; mission environment confirmed by event; no SMMU fault' in log
            result = re.search(r'BHIE result status (0x[0-9a-f]+|0) EE (\d+) MHI (0x[0-9a-f]+|0) events (\d+) mission_event (\d+) FSR (0x[0-9a-f]+|0) global (0x[0-9a-f]+|0)', log)
            checks['bhie_status_and_fault_registers'] = bool(result and int(result[1], 0) >> 30 == 2 and
                int(result[2]) in (2, 4) and int(result[5]) == 1 and not (int(result[6], 0) & 0xc00001fe) and int(result[7], 0) == 0)
        if mode in ('ipcr', 'qmi', 'driver', 'scan', 'station'):
            checks['msi_restore_readback'] = 'PCI MSI restore readback verified' in log
            checks['ipcr_channel_commands_completed'] = all(f'IPCR channel {channel} START completion 1' in log for channel in (20, 21))
            checks['qrtr_wlfw_discovered'] = 'stage 20 IPCR channels 20/21 active; QRTR bidirectional and WLFW service found' in log
            result = re.search(r'IPCR result tx (\d+) completed (\d+) rx (\d+) hellos (\d+) services (\d+) WLFW (\d+) instance (\d+) FSR (0x[0-9a-f]+|0) global (0x[0-9a-f]+|0)', log)
            checks['ipcr_bidirectional_counters'] = bool(result and int(result[1]) > 0 and int(result[2]) > 0 and
                int(result[3]) > 0 and int(result[5]) > 0 and int(result[6]) == 1 and
                not (int(result[8], 0) & 0xc00001fe) and int(result[9], 0) == 0)
        if mode == 'qmi':
            checks['wlfw_capability_response'] = 'stage 21 WLFW capability request/response successful over QMI; no SMMU fault' in log
            checks['qmi_success_result'] = 'WLFW capability response result 0 error 0' in log
            result = re.search(r'QMI result response (\d+) success (\d+) tx_completed (\d+) rx (\d+) FSR (0x[0-9a-f]+|0) global (0x[0-9a-f]+|0)', log)
            checks['qmi_reply_and_no_fault'] = bool(result and int(result[1]) == 1 and int(result[2]) == 1 and
                int(result[3]) > 0 and int(result[4]) > 0 and not (int(result[5], 0) & 0xc00001fe) and int(result[6], 0) == 0)
        if mode in ('driver', 'scan', 'station'):
            checks['dedicated_qca6490_profile'] = 'qca6490 hw1.0 (lemonade)' in log
            checks['ath11k_unified_ready'] = 'IS UP as far as it is built: the firmware has said "unified ready"' in log
            checks['driver_and_transport_success'] = bool(re.search(r'ath11k driver result 0 transport_failed 0 FSR (?:0x[246]00|0) global 0', log))
            checks['driver_cleanup_no_timeout'] = 'driver worker stop timed out' not in log
            checks['driver_startup_marker'] = 'stage 22 QCA6490 major-1 ath11k startup completed' in log
        if mode == 'station':
            checks['ios_station_offered'] = 'station offered to iOS; runtime kept until normal stop' in log
            checks['ios_interface_attached'] = 'net: en0 attached on the phone\'s radio (ath11k) (a radio)' in log
            checks['normal_stop_completed'] = 'emergency endpoint reset' not in log
        if mode == 'scan':
            checks['scan_completed'] = 'ms later the scan is OVER' in log
            result = re.search(r'THE SCAN HEARD (\d+) NETWORKS', log)
            checks['scan_received_networks'] = bool(result and int(result[1]) > 0)
    network = bool(manifest.get('network_test'))
    if network:
        checks['network_test_used_station'] = mode == 'station'
        checks['private_network_handling'] = bool(manifest.get('network_identity_redacted') and
            manifest.get('wifi_firmware', {}).get('network_private'))
        checks['association_completed'] = ('lx: THE NETWORK IS JOINED: frames can pass' in log and
            bool(re.search(r'lemonade-net: association result 1 after [0-9.]+ s', log)))
        checks['dhcp_accepted'] = 'lemonade-net: DHCP request accepted 1 transport 0 result 0 status 0' in log
        checks['usable_ipv4'] = 'lemonade-net: en0 usable IPv4 1 after ' in log
        checks['dns_resolved'] = 'lemonade-net: DNS resolved IPv4' in log
        for attempt in range(1, 4):
            checks[f'http_attempt_{attempt}'] = bool(re.search(
                rf'lemonade-net: HTTP attempt {attempt} en0_bound 1 status 200 bytes [1-9][0-9]* expected_body 1', log))
        checks['network_summary_success'] = 'lemonade-net: acceptance association 1 dhcp 1 ipv4 1 http 3/3' in log
        # The initial printf is in XNU's rolling msgbuf, not the live trace.
        # A successful Apple80211 ioctl on en0 is stronger evidence: net_ioctl
        # rejects it for non-wireless links. Require actual en0-bound traffic
        # as well, so a missing startup message alone never becomes success.
        if mode == 'station' and not checks['ios_interface_attached']:
            checks['ios_interface_attached'] = all(checks[name] for name in
                ('association_completed', 'dhcp_accepted', 'usable_ipv4',
                 'http_attempt_1', 'http_attempt_2', 'http_attempt_3'))
        if manifest.get('wifi_firmware', {}).get('network_reconnect_test'):
            checks['disconnect_completed'] = 'lemonade-net: intentional disconnect reached idle 1' in log
            checks['reconnect_completed'] = 'lemonade-net: reconnect complete association 1 dhcp 1 ipv4 1 http 3/3' in log
    passed = all(checks.values())
    return {'passed': passed, 'mode': mode, 'checks': checks,
            'scope': 'iOS association, DHCP and en0-bound traffic' if network else 'bounded PCIe bring-up stage only',
            'networking_verified': network and passed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--record-guard', action='store_true', help='record a passed real-DMA guard test for this exact kernel')
    args = parser.parse_args()
    manifest = json.loads(args.log.with_suffix('.manifest.json').read_text())
    report = check(manifest, args.log.read_bytes())
    print(json.dumps(report, indent=2))
    if args.record_guard:
        root = Path(__file__).resolve().parents[1]
        log = args.log.resolve()
        if not report['passed'] or report['mode'] != 'dma-fault' or not log.is_relative_to(root / 'local/lemonade'):
            raise SystemExit('Only a passed private endpoint-DMA guard test can authorize the firmware stage')
        evidence = {'kernel_sha256': manifest['kernel_sha256'], 'log': str(log.relative_to(root)),
                    'log_sha256': manifest['log_sha256'], 'scope': 'endpoint DMA guard only'}
        (root / 'local/lemonade/wifi/dma-guard-verified.json').write_text(json.dumps(evidence, indent=2) + '\n')
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
