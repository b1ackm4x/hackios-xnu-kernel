#!/usr/bin/env python3
"""The payload of an IM4P container (an Image4 payload: what the files of Firmware/ in an IPSW are).
usage: im4p.py IN.im4p OUT
IM4P is DER: SEQUENCE { IA5String "IM4P", IA5String type, IA5String description, OCTET STRING payload, ... }.
The payload comes out as it is in the container -- for a device tree that is an LZFSE stream ("bvx2"),
to be decoded by lzfse(1), compression_tool(1) of macOS, or the lzfse Python module (see mkidentity.sh).
"""
import sys


def tlv(data, pos):
    """One DER element: (tag, value start, value end)."""
    if pos < 0 or len(data) - pos < 2:
        raise ValueError("truncated DER header")
    tag = data[pos]
    length = data[pos + 1]
    pos += 2
    if length & 0x80:
        n = length & 0x7f
        if n == 0 or n > 8 or n > len(data) - pos:
            raise ValueError("invalid DER length")
        length = int.from_bytes(data[pos:pos + n], 'big')
        pos += n
    if length > len(data) - pos:
        raise ValueError("truncated DER value")
    return tag, pos, pos + length


def main():
    data = open(sys.argv[1], 'rb').read()
    tag, pos, end = tlv(data, 0)
    if tag != 0x30:
        sys.exit('im4p.py: not a DER sequence')
    strings = []
    while pos < end:
        tag, start, stop = tlv(data, pos)
        if stop > end:
            raise ValueError("DER element exceeds IM4P sequence")
        if tag == 0x16:
            strings.append(data[start:stop].decode('ascii', 'replace'))
        elif tag == 0x04:
            if len(strings) < 2 or strings[0] != 'IM4P':
                sys.exit('im4p.py: not an IM4P (%r)' % strings)
            with open(sys.argv[2], 'wb') as out:
                out.write(data[start:stop])
            print('im4p.py: %s (%s): payload of %d bytes%s' % (strings[1], strings[2] if len(strings) > 2 else '',
                  stop - start, ', LZFSE' if data[start:start + 3] == b'bvx' else ''))
            return
        pos = stop
    sys.exit('im4p.py: no payload')


if __name__ == '__main__':
    main()
