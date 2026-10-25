#!/usr/bin/env python3
"""Check the pinned public firmware's I2C ABI and SE2 routing; no device I/O.

Offsets are for Mu-Silicium e51cd877's lemonade binaries and are protected by
SHA-256 checks. This is a narrow audit of the selected bus, not a DAL parser.
"""
import hashlib
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]
DRIVERS = ROOT / 'Mu-Silicium/Binaries/lemonade/QcomPkg/Drivers'
EXPECTED = {
    'DALSYSDxe': '9f18b235958eb8a5cbf0020fab1d0eb9b6e45d992880622173ece5d226c33679',
    'I2CDxe': '2f5d02a7dc5c24d699efe58ca5d73ba3c8a57c8700db9741e4133555828eb580',
    'ChargerExDxe': '650803bb3d42bb25c208a5b8f17af3141ea46202b685f500ea10e130d3e25276',
}


def check():
    blobs = {}
    for name, sha in EXPECTED.items():
        data = (DRIVERS / name / (name + '.efi')).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValueError(name + ': firmware differs from the audited ABI/routing')
        blobs[name] = data
    dal, i2c = blobs['DALSYSDxe'], blobs['I2CDxe']
    # DALProps: property directory, struct table, string-device count, device table.
    directory, structures, count, devices = struct.unpack_from('<4Q', dal, 0x7950)
    assert (directory, structures, count, devices) == (0x21e28, 0x219d8, 17, 0x22248)
    name, _, prop_offset = struct.unpack_from('<QII', dal, devices + 5 * 40)
    assert dal[name:dal.index(b'\0', name)] == b'/dev/i2c3'
    kind, index, end = struct.unpack_from('<3I', dal, directory + prop_offset)
    assert (kind, index, end) == (0x128000a2, 7, 0xff00ff00)
    # DAL struct descriptors are {size, pointer}, not a flat pointer array.
    _, config = struct.unpack_from('<2Q', dal, structures + index * 16)
    qup_base, _, se_offset = struct.unpack_from('<QQI', dal, config)
    assert qup_base + se_offset == 0x988000
    pins = struct.unpack_from('<2I', dal, config + 0x20)
    assert pins == (0x180d1, 0x180c1)
    clock_list = struct.unpack_from('<Q', dal, config + 0x40)[0]
    clock_name = struct.unpack_from('<Q', dal, clock_list)[0]
    assert dal[clock_name:dal.index(b'\0', clock_name)] == b'gcc_qupv3_wrap0_s2_clk'
    assert struct.unpack_from('<6Q', i2c, 0x70a8) == (0x10000, 0x2014, 0x2280, 0x2684, 0x2390, 0x27d8)
    # The first slot at 0x147c accepts BOOLEAN*, asks PMIC USB + charger DC
    # presence, ORs their one-byte outputs, and returns EFI_STATUS.
    assert struct.unpack_from('<6Q', blobs['ChargerExDxe'], 0xa0d8) == (0x10004, 0x147c, 0x15b4, 0x167c, 0x1744, 0x1944)
    print('PASS: pinned I2C revision 1.0; /dev/i2c3 -> SE2 @ 0x988000, GPIO 13/12, wrap0_s2 clock')
    print('PASS: pinned ChargerEx 1.4 presence-query ABI and function table')
    print('This verifies firmware configuration only; gauge response and runtime ownership require device validation.')


if __name__ == '__main__':
    check()
