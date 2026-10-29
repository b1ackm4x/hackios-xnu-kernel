#!/bin/bash
# Build the opt-in native Bluetooth keyboard event adapter.
set -euo pipefail
umask 077
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT"
OUT=out/lemonade-bt-hid
mkdir -p "$OUT"
/usr/bin/clang -target arm64-apple-ios16.5 --ld-path="$XNUDEPS/host/bin/ld64" \
    -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -O1 -fblocks -std=c11 \
    -Wall -Wextra -Werror -dynamiclib -install_name /redwood/lib/liblemonade-bt-hid.dylib \
    -framework CoreFoundation -framework IOKit \
    -o "$OUT/liblemonade-bt-hid.dylib" userland/src/lemonade_bt_hid.c
"$THEOS/toolchain/linux/iphone/bin/ldid" -S "$OUT/liblemonade-bt-hid.dylib"
/usr/bin/clang -target arm64-apple-ios16.5 --ld-path="$XNUDEPS/host/bin/ld64" \
    -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -O1 -fblocks -std=c11 \
    -Wall -Wextra -Werror -Wno-unused-function -DLEMONADE_BT_HID_SERVER \
    -framework CoreFoundation -framework IOKit \
    -o "$OUT/lemonade-bt-hid" userland/src/lemonade_bt_hid.c
"$THEOS/toolchain/linux/iphone/bin/ldid" -Suserland/src/lemonade_bt_hid.entitlements.plist "$OUT/lemonade-bt-hid"
python3 - <<'PY'
import hashlib,json,struct
from pathlib import Path
out=Path('out/lemonade-bt-hid');binary=out/'liblemonade-bt-hid.dylib'
data=binary.read_bytes()
assert struct.unpack_from('<4I',data)==(0xfeedfacf,0x100000c,0,6)
helper=(out/'lemonade-bt-hid').read_bytes()
assert struct.unpack_from('<4I',helper)==(0xfeedfacf,0x100000c,0,2)
sources=['userland/src/lemonade_bt_hid.c','userland/src/lemonade_bt_hid_report.h',
         'userland/src/lemonade_bt_hid_ipc.h','userland/src/lemonade_bt_hid.entitlements.plist',
         'devices/oneplus-9/tools/lemonade-build-bt-hid.sh']
(out/'build.json').write_text(json.dumps({
 'profile':'lemonade-bluetooth-keyboard-v1','hardware_verified':False,
 'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),
 'helper_sha256':hashlib.sha256(helper).hexdigest(),
 'sources':{s:hashlib.sha256(Path(s).read_bytes()).hexdigest() for s in sources}},indent=2)+'\n')
print('Built native Bluetooth keyboard adapter; device validation pending.')
PY
