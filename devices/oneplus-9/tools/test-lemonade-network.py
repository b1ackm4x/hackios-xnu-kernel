#!/usr/bin/env python3
"""Synthetic-only credential selection and privacy regression checks."""
import importlib.util
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('network', Path(__file__).with_name('lemonade-network.py'))
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)


def config(entries):
    root = ET.Element('NetworkList')
    for name, key in entries:
        node = ET.SubElement(root, 'WifiConfiguration')
        ET.SubElement(node, 'string', name='SSID').text = json.dumps(name, ensure_ascii=False)
        if key is not None:
            ET.SubElement(node, 'string', name='PreSharedKey').text = json.dumps(key)
    return ET.tostring(root)


class PrivateNetwork(unittest.TestCase):
    def test_selects_only_connected_network_and_preserves_bytes(self):
        name, key = 'test-中文\\"', 'synthetic-passphrase'
        result = network.select('WifiInfo: SSID: ' + json.dumps(name) + ', BSSID: ignored',
            config([('unrelated', 'different-passphrase'), (name, key)]))
        self.assertEqual(network.unpack(result), (name.encode(), key.encode()))

    def test_missing_duplicate_and_encrypted_keys_fail_closed(self):
        status = 'WifiInfo: SSID: test, BSSID: ignored'
        for entries in ([], [('other', 'synthetic-password')], [('test', None)],
                        [('test', 'synthetic-password')] * 2, [('test', 'short')], [('test', 'x' * 64)]):
            with self.subTest(entries=len(entries)), self.assertRaises(ValueError):
                network.select(status, config(entries))

    def test_record_must_have_exact_bounds(self):
        good = b'LNW1\x04\x08testabcdefgh'
        self.assertEqual(network.unpack(good), (b'test', b'abcdefgh'))
        for bad in (b'', good[:-1], good + b'X', b'LNW1\x00\x08abcdefgh', b'BAD!' + good[4:]):
            with self.assertRaises(ValueError):
                network.unpack(bad)

    def test_redacts_credentials_and_radio_addresses(self):
        record = b'LNW1\x04\x08testabcdefgh'
        output = network.redact(b'network test key abcdefgh bssid 00:1A:22:3b:44:55 status 200', record)
        for value in (b'test', b'abcdefgh', b'00:1A:22:3b:44:55'):
            self.assertNotIn(value, output)
        self.assertIn(b'status 200', output)


if __name__ == '__main__':
    unittest.main()
