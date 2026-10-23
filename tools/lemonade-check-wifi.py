#!/usr/bin/env python3
"""Validate one bounded Wi-Fi bring-up stage; never equate it with networking."""
import argparse
import hashlib
import json
from pathlib import Path
import re

MODES = {'inspect': 1, 'rails': 2, 'domain': 3, 'clocks': 4,
         'resets': 5, 'controller': 6, 'phy': 7, 'root': 8, 'radio': 9, 'link': 10, 'endpoint': 11, 'mmio': 12, 'smmu': 13}


def check(manifest, raw):
    log = raw.decode(errors='replace')
    mode = manifest.get('wifi')
    checks = {
        'lemonade_record': manifest.get('device') == 'lemonade',
        'known_stage': mode in MODES,
        'log_integrity': hashlib.sha256(raw).hexdigest() == manifest.get('log_sha256'),
        'returned_to_loader': bool(re.search(r'result: returned \d+ 0(?:\s|$)', manifest.get('result', ''))),
        'read_only_survey_completed': 'wifi-lemonade: inspection complete; hardware writes=0;' in log,
        'no_driver_failure': not any(x in log for x in (
            'wifi-lemonade: wait failed', 'request timed out', 'stage 2 failed',
            'stage 3 failed', 'stage 4 failed', 'stage 5 failed', 'stage 6 failed',
            'stage 7 failed', 'stage 8 failed', 'stage 9 failed', 'stage 10 failed', 'stage 11 failed', 'stage 12 failed', 'stage 13 failed', 'root-power failed')),
        'no_kernel_panic': not re.search(r'panic\(cpu|Debugger called: <panic>|uncaught exception', log, re.I),
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
        if mode == 'smmu':
            checks['smmu_survey_without_writes'] = 'stage 13 SMMU survey complete;' in log and 'SMMU writes=0' in log
    return {'passed': all(checks.values()), 'mode': mode, 'checks': checks,
            'scope': 'bounded PCIe bring-up stage only', 'networking_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.log.with_suffix('.manifest.json').read_text())
    report = check(manifest, args.log.read_bytes())
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
