#!/usr/bin/env python3
"""Make a separate, pinned ath11k source copy for the QCA6490 major-1 port.

The original Linux 7.0.14 source remains untouched. Register/RX definitions are
the audited WCN6855 definitions; CE2 gets the original QCA6490 3200-byte limit.
This is an experimental profile, not a claim of complete silicon equivalence.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

HASHES = {
    'core.c': '89f618dc1986bd1e218b38c4555a4dd19c160c3ad38681dfb9f85324a983266f',
    'core.h': '8e284199835898c9405a904ac25e0b90c946a8748f8e0fb23d19c5e22658e86a',
    'ce.c': '4e97917f2478531f00377d7ecaabed601021284b9c79635a8216651222aa129e',
    'ce.h': 'b46cc3e5f9bce723b8f500a97dd93ddb2d156a8b6a2dded1bdb45b936929ef9d',
    'hw.c': 'b84c11bfa5ec3ea12b6908c30453a9c2b8efc09226c2091d3203ae8ac6a7a1df',
}


def transform(files):
    out = dict(files)
    out['core.h'] = out['core.h'].replace('enum ath11k_hw_rev {',
        'enum ath11k_hw_rev {\n\tATH11K_HW_QCA6490_HW10 = 100,\n\t/* Preserve upstream revision values. */')
    out['core.h'] = out['core.h'].replace('\tATH11K_HW_IPQ8074,', '\tATH11K_HW_IPQ8074 = 0,', 1)
    start = files['core.c'].index('\n\t{\n\t\t.name = "wcn6855 hw2.0"')
    end = files['core.c'].index('\n\t},', start) + len('\n\t},')
    profile = files['core.c'][start:end]
    for old, new in (
        ('wcn6855 hw2.0', 'qca6490 hw1.0 (lemonade)'),
        ('ATH11K_HW_WCN6855_HW20', 'ATH11K_HW_QCA6490_HW10'),
        ('WCN6855/hw2.0', 'QCA6490/hw1.0'),
        ('ath11k_host_ce_config_qca6390', 'ath11k_host_ce_config_qca6490'),
        ('ath11k_target_ce_config_wlan_qca6390', 'ath11k_target_ce_config_wlan_qca6490'),
        ('.internal_sleep_clock = true', '.internal_sleep_clock = false'),
        ('.idle_ps = true', '.idle_ps = false'),
        ('.supports_sta_ps = true', '.supports_sta_ps = false'),
        ('.supports_suspend = true', '.supports_suspend = false'),
        ('.global_reset = true', '.global_reset = false'),
    ):
        if old not in profile:
            raise ValueError('Pinned profile field missing: ' + old)
        profile = profile.replace(old, new)
    out['core.c'] = files['core.c'][:start] + profile + files['core.c'][start:]
    for name, symbol, field in (
        ('ce.c', 'ath11k_host_ce_config_qca6390', r'(\.src_sz_max = )2048'),
        ('hw.c', 'ath11k_target_ce_config_wlan_qca6390', r'(\.nbytes_max = __cpu_to_le32\()2048'),
    ):
        text = files[name]
        match = re.search(r'const struct \w+ ' + symbol + r'\[\] = \{.*?\n\};', text, re.S)
        if not match:
            raise ValueError('Pinned CE table missing')
        table = match[0].replace(symbol, symbol.replace('qca6390', 'qca6490'))
        a = table.index('/* CE2:'); b = table.index('/* CE3:', a)
        ce2, count = re.subn(field, r'\g<1>3200', table[a:b])
        if count != 1:
            raise ValueError('Unexpected CE2 layout')
        table = table[:a] + ce2 + table[b:]
        out[name] = text[:match.end()] + '\n\n' + table + text[match.end():]
    for name, symbol in (('ce.h', 'ath11k_host_ce_config_qca6390'),
                         ('core.h', 'ath11k_target_ce_config_wlan_qca6390')):
        text = out[name]
        declaration = re.search(r'extern const struct \w+ ' + symbol + r'\[\];', text)[0]
        out[name] = text.replace(declaration, declaration + '\n' + declaration.replace('qca6390', 'qca6490'))
    return out


def main():
    source, output = map(Path, sys.argv[1:])
    if source.resolve() == output.resolve():
        raise SystemExit('A separate output tree is required')
    files = {}
    for name, expected in HASHES.items():
        data = (source / 'ath11k' / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise SystemExit('Source differs from pinned Linux 7.0.14: ' + name)
        files[name] = data.decode()
    stamp = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if (output / '.lemonade-profile').exists() and (output / '.lemonade-profile').read_text() == stamp:
        return
    shutil.copytree(source, output, dirs_exist_ok=True)
    for name, data in transform(files).items():
        (output / 'ath11k' / name).write_text(data)
    (output / '.lemonade-profile').write_text(stamp)
    (output / '.lemonade-provenance.json').write_text(json.dumps({'linux': '7.0.14', 'inputs': HASHES,
        'profile': 'QCA6490 major 1', 'script_sha256': stamp, 'hardware_acceptance': 'separate'}, indent=2) + '\n')


if __name__ == '__main__':
    main()
