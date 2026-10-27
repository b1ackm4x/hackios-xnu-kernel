#!/bin/bash
# Development-only observation of native Settings startup and table structure.
set -euo pipefail
umask 077
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT"
OUT=out/lemonade-bt-ui-trace
mkdir -p "$OUT"
/usr/bin/clang -target arm64-apple-ios16.5 --ld-path="$XNUDEPS/host/bin/ld64" \
    -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -O1 -fobjc-arc -fblocks \
    -Wall -Wextra -Werror -dynamiclib -install_name /redwood/lib/liblemonade-bt-ui-trace.dylib \
    -framework Foundation -framework UIKit \
    -o "$OUT/liblemonade-bt-ui-trace.dylib" userland/src/lemonade_bt_ui_trace.m
"$THEOS/toolchain/linux/iphone/bin/ldid" -S "$OUT/liblemonade-bt-ui-trace.dylib"
python3 - <<'PY'
import hashlib,json,struct
from pathlib import Path
out=Path('out/lemonade-bt-ui-trace');binary=out/'liblemonade-bt-ui-trace.dylib'
data=binary.read_bytes()
assert struct.unpack_from('<4I',data)==(0xfeedfacf,0x100000c,0,6)
sources=['userland/src/lemonade_bt_ui_trace.m','devices/oneplus-9/tools/lemonade-build-bt-ui-trace.sh']
(out/'build.json').write_text(json.dumps({
 'profile':'lemonade-native-bluetooth-ui-trace-v1','observation_only':True,'bounded':True,
 'sha256':hashlib.sha256(data).hexdigest(),
 'sources':{s:hashlib.sha256(Path(s).read_bytes()).hexdigest() for s in sources}},indent=2)+'\n')
print('Built bounded Preferences UI observer.')
PY
