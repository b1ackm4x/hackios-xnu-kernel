"""Private, short-lived network input for the exact phone's RAM test.

Never pass network values through arguments, environment variables or logs.
"""
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET


def unpack(data):
    if (len(data) < 15 or data[:4] != b'LNW1' or not 1 <= data[4] <= 32 or
            not 8 <= data[5] <= 63 or len(data) != 6 + data[4] + data[5]):
        raise ValueError('Invalid private network record')
    return data[6:6 + data[4]], data[6 + data[4]:]


def select(status, xml):
    match = re.search(r'WifiInfo: SSID: (.*?), BSSID:', status)
    if not match:
        raise ValueError('Android has no connected Wi-Fi network')
    current = match[1]
    if current.startswith('"'):
        current = json.loads(current)
    matches = []
    for config in ET.fromstring(xml).iter('WifiConfiguration'):
        name = config.find("string[@name='SSID']")
        key = config.find("string[@name='PreSharedKey']")
        if name is None or key is None or not name.text or not key.text:
            continue
        try:
            decoded_name = json.loads(name.text)
        except (ValueError, TypeError):
            continue
        if decoded_name != current:
            continue
        try:
            decoded_key = json.loads(key.text)
        except (ValueError, TypeError):
            raise ValueError('Current network has no supported plaintext passphrase') from None
        if not isinstance(decoded_key, str):
            raise ValueError('Current network key is unsupported')
        name_bytes, key_bytes = decoded_name.encode(), decoded_key.encode()
        if not 1 <= len(name_bytes) <= 32 or not 8 <= len(key_bytes) <= 63:
            raise ValueError('Current network name or passphrase length is unsupported')
        matches.append(b'LNW1' + bytes((len(name_bytes), len(key_bytes))) + name_bytes + key_bytes)
    if len(matches) != 1:
        raise ValueError('Exactly one matching current network is required')
    return matches[0]


def read_current(root):
    expected = json.loads((root / 'local/lemonade/baseline.json').read_text())['hardware_serial_sha256']
    def command(args):
        result = subprocess.run(args, capture_output=True, timeout=20)
        if result.returncode:
            raise ValueError('Private Android network read failed; response omitted')
        return result.stdout
    serial = None
    for line in command(['adb', 'devices', '-l']).decode().splitlines()[1:]:
        fields = line.split()
        if (len(fields) >= 3 and fields[1] == 'device' and
                (fields[2].startswith('usb:') or re.fullmatch(r'\d+-\d+(?:\.\d+)*', fields[2])) and
                hashlib.sha256(fields[0].encode()).hexdigest() == expected):
            serial = fields[0]
    if serial is None:
        raise ValueError('The baseline Android phone is not connected over USB')
    prefix = ['adb', '-s', serial]
    if command(prefix + ['shell', 'getprop', 'sys.boot_completed']).strip() != b'1':
        raise ValueError('Android boot has not completed')
    status = command(prefix + ['shell', 'cmd', 'wifi', 'status']).decode()
    xml = command(prefix + ['exec-out', "su -c 'cat /data/misc/apexdata/com.android.wifi/WifiConfigStore.xml'"])
    return select(status, xml)


def redact(log, record):
    name, key = unpack(record)
    for value in sorted((name, key), key=len, reverse=True):
        log = log.replace(value, b'[private network value omitted]')
    # Network identifiers can also be emitted by stock iOS components.
    return re.sub(rb'(?i)(?<![0-9a-f])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f])',
                  b'[radio address omitted]', log)
