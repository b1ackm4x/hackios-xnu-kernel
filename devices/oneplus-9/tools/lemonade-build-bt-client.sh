#!/bin/bash
# Build the explicit, bounded native BluetoothManager test client.
set -euo pipefail
umask 077
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT"
OUT=out/lemonade-bt-client
mkdir -p "$OUT"
/usr/bin/clang -target arm64-apple-ios16.5 --ld-path="$XNUDEPS/host/bin/ld64" \
    -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -O1 -fobjc-arc -fblocks \
    -Wall -Wextra -Werror -framework Foundation \
    -o "$OUT/lemonade-bt-client" userland/src/lemonade_bt_client.m
"$THEOS/toolchain/linux/iphone/bin/ldid" -Suserland/src/lemonade_bt_client.entitlements.plist "$OUT/lemonade-bt-client"
python3 - <<'PY'
import hashlib,json,struct
from pathlib import Path
out=Path('out/lemonade-bt-client');binary=out/'lemonade-bt-client'
data=binary.read_bytes()
assert struct.unpack_from('<4I',data)==(0xfeedfacf,0x100000c,0,2)
sources=['userland/src/lemonade_bt_client.m','userland/src/lemonade_bt_client.entitlements.plist','devices/oneplus-9/tools/lemonade-build-bt-client.sh']
(out/'build.json').write_text(json.dumps({
 'profile':'lemonade-native-bluetooth-client-v1','bounded':True,'hardware_verified':False,
 'sha256':hashlib.sha256(data).hexdigest(),
 'sources':{s:hashlib.sha256(Path(s).read_bytes()).hexdigest() for s in sources}},indent=2)+'\n')
print('Built bounded native BluetoothManager test client.')
PY
