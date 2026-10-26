#!/bin/bash
set -euo pipefail
umask 077
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT"
OUT=out/lemonade-bt-compat
mkdir -p "$OUT"
/usr/bin/clang -target arm64-apple-ios16.5 --ld-path="$XNUDEPS/host/bin/ld64" \
    -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -O1 -Wall -Wextra -Werror \
    -dynamiclib -install_name /redwood/lib/liblemonade-bt-compat.dylib \
    -framework CoreFoundation -framework IOKit \
    -o "$OUT/liblemonade-bt-compat.dylib" userland/src/lemonade_bt_compat.c
"$THEOS/toolchain/linux/iphone/bin/ldid" -S "$OUT/liblemonade-bt-compat.dylib"
python3 - <<'PY'
import hashlib,json,struct
from pathlib import Path
out=Path('out/lemonade-bt-compat');binary=out/'liblemonade-bt-compat.dylib'
data=binary.read_bytes()
if struct.unpack_from('<4I',data)!=(0xfeedfacf,0x100000c,0,6):
 raise SystemExit('Bluetooth compatibility library is not arm64 Mach-O')
native=Path('ios165/rootfs/usr/sbin/bluetoothd')
expected='a315f41598a7d6271cafbdc11204944f3e75e6d10cd6375b322aafc34aef6889'
if hashlib.sha256(native.read_bytes()).hexdigest()!=expected:
 raise SystemExit('Unsupported native bluetoothd binary')
sources=['userland/src/lemonade_bt_compat.c','tools/lemonade-build-bt-compat.sh']
(out/'build.json').write_text(json.dumps({
 'profile':'lemonade-native-bluetooth-compat-v1','hardware_verified':False,
 'preserves_real_hci_responses':True,'native_sha256':expected,
 'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),
 'sources':{s:hashlib.sha256(Path(s).read_bytes()).hexdigest() for s in sources}},indent=2)+'\n')
print('Built exact-ABI optional HCI capability gate; device validation pending.')
PY
