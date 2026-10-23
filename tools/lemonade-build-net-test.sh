#!/bin/bash
# Run inside the existing Linux iOS toolchain; contains no network credentials.
set -euo pipefail
umask 077
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT"
mkdir -p out
xnu-clang -arch arm64 -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -O1 \
    -Wall -Wextra -Werror -fobjc-arc -framework Foundation \
    -o out/lemonade-net-test tools/lemonade-net-test.m
python3 - <<'PY'
import hashlib, json, struct
from pathlib import Path
source = Path('tools/lemonade-net-test.m')
binary = Path('out/lemonade-net-test')
data = binary.read_bytes()
if struct.unpack_from('<4I', data)[0:4:1] != (0xfeedfacf, 0x100000c, 0, 2):
    raise SystemExit('Network acceptance output is not an arm64 Mach-O executable')
record = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
          'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
          'arch': 'arm64', 'purpose': 'RAM-only iOS network acceptance',
          'acceptance_profile': 'apple80211-dhcp-http-reconnect-v1'}
Path('out/lemonade-net-test.manifest.json').write_text(json.dumps(record, indent=2) + '\n')
print('Network acceptance executable compiled and verified')
PY
