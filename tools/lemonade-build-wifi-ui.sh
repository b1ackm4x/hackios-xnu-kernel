#!/bin/bash
# Temporary UI navigation only; no network identity or password is embedded.
set -euo pipefail
umask 077
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT"
xnu-clang -arch arm64 -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -O1 \
    -Wall -Wextra -Werror -framework CoreFoundation \
    -o out/lemonade-wifi-ui tools/lemonade-wifi-ui.c
"$THEOS/toolchain/linux/iphone/bin/ldid" -Suserland/src/battery_test.entitlements out/lemonade-wifi-ui
python3 - <<'PY'
import hashlib, json, struct
from pathlib import Path
data = Path('out/lemonade-wifi-ui').read_bytes()
assert struct.unpack_from('<4I', data) == (0xfeedfacf, 0x100000c, 0, 2)
record = {'sha256': hashlib.sha256(data).hexdigest(), 'arch': 'arm64',
          'source_sha256': hashlib.sha256(Path('tools/lemonade-wifi-ui.c').read_bytes()).hexdigest()}
Path('out/lemonade-wifi-ui.manifest.json').write_text(json.dumps(record, indent=2) + '\n')
print('Real Wi-Fi Settings navigation helper compiled and signed')
PY
