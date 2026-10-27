#!/usr/bin/env python3
"""Host checks of imported DTB/firmware inspection and open loader identity generation."""
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import cellular_probe
import modem_firmware

class UpstreamToolsTests(unittest.TestCase):
    def test_public_soc_fixture_roundtrip(self):
        subprocess.run(['bash', str(ROOT / 'tools/soc-args-test.sh')], check=True)

    def test_placeholder_is_valid_c_without_an_image(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            generated = subprocess.check_output([sys.executable, str(ROOT/'xnuloader/mkidentity.py'), '--placeholder'])
            self.assertNotIn(b'Apple device-tree data from', generated)
            (path/'identity.h').write_bytes(generated)
            (path/'test.c').write_text('#include "identity.h"\nint main(void) { return '
                'identity_root[0].length || identity_product[0].length || '
                'identity_haptics[0].length || identity_camera[0].length || '
                'identity_root_data[0] || identity_product_data[0] || '
                'identity_haptics_data[0] || identity_camera_data[0]; }\n')
            subprocess.run([os.environ.get('CC','cc'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                            str(path/'test.c'), '-o', str(path/'test')], check=True)
            subprocess.run([str(path/'test')], check=True)

    def test_im4p_extraction_and_truncation(self):
        def tlv(tag, data): return bytes([tag,len(data)]) + data
        body = tlv(0x16,b'IM4P') + tlv(0x16,b'dtre') + tlv(0x16,b'test') + tlv(4,b'bvx2fixture')
        valid = tlv(0x30,body)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder); source=path/'test.im4p'; out=path/'payload'
            source.write_bytes(valid)
            command=[sys.executable,str(ROOT/'xnuloader/im4p.py'),str(source),str(out)]
            subprocess.run(command,check=True,capture_output=True)
            self.assertEqual(out.read_bytes(),b'bvx2fixture')
            for broken in (valid[:-1], b'\x30\x80', b'\x30\x01\x16',b'\x30\x82\x01'):
                source.write_bytes(broken);out.unlink(missing_ok=True)
                result=subprocess.run(command,capture_output=True)
                self.assertNotEqual(result.returncode,0)
                self.assertFalse(out.exists())

    def test_modem_firmware_bounds_without_phone(self):
        dtb=ROOT/'prebuilt/uefi/redwood.dtb'
        nodes=cellular_probe.fdt_nodes(dtb.read_bytes())
        node=next(n for n in nodes if n['path'].endswith('/qcom,mss@4080000'))
        handle=node['properties']['memory-region']
        memory=next(n for n in nodes if n['properties'].get('phandle')==handle)
        base,size=struct.unpack('>QQ',bytes.fromhex(memory['properties']['reg']))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            header=bytearray(84);header[:6]=b'\x7fELF\x01\x01'
            struct.pack_into('<I',header,28,52);struct.pack_into('<HH',header,42,32,1)
            struct.pack_into('<8I',header,52,1,0,0,base,64,4096,0,0)
            (path/'modem.mdt').write_bytes(header);(path/'modem.b00').write_bytes(bytes(64))
            report=modem_firmware.inspect(dtb,path,node['path'])
            self.assertEqual(report['loadable_segments'],1)
            self.assertEqual(report['file_bytes'],64)
            struct.pack_into('<I',header,52+12,base+size-16)
            (path/'modem.mdt').write_bytes(header)
            with self.assertRaisesRegex(ValueError,'exceeds'):
                modem_firmware.inspect(dtb,path,node['path'])

if __name__=='__main__': unittest.main()
