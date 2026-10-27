#!/bin/bash
# Build the OnePlus-specific firmware bootstrap XPC service.
set -euo pipefail
umask 077
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT"
OUT=out/lemonade-bt-bootstrap
mkdir -p "$OUT"
/usr/bin/clang -target arm64-apple-ios16.5 --ld-path="$XNUDEPS/host/bin/ld64" \
    -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -O1 -fblocks \
    -Wall -Wextra -Werror -framework CoreFoundation -framework IOKit \
    -o "$OUT/lemonade-bt-bootstrap" userland/src/lemonade_bt_bootstrap.c
"$THEOS/toolchain/linux/iphone/bin/ldid" -S "$OUT/lemonade-bt-bootstrap"
python3 - <<'PY'
import hashlib,json,struct
from pathlib import Path
out=Path('out/lemonade-bt-bootstrap');binary=out/'lemonade-bt-bootstrap'
data=binary.read_bytes()
assert struct.unpack_from('<4I',data)==(0xfeedfacf,0x100000c,0,2)
sources=['userland/src/lemonade_bt_bootstrap.c','devices/oneplus-9/tools/lemonade-build-bt-bootstrap.sh']
(out/'build.json').write_text(json.dumps({
 'profile':'lemonade-native-bluetooth-bootstrap-v1','requires_kernel_verified_firmware':True,
 'hardware_verified':False,'sha256':hashlib.sha256(data).hexdigest(),
 'sources':{s:hashlib.sha256(Path(s).read_bytes()).hexdigest() for s in sources}},indent=2)+'\n')
print('Built board Bluetooth firmware bootstrap service.')
PY
