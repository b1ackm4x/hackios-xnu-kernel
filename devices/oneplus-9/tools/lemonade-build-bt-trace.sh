#!/bin/bash
# Build the explicitly enabled, payload-free Bluetooth interface probe.
set -euo pipefail
umask 077
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT"
OUT=out/lemonade-bt-trace
mkdir -p "$OUT"
/usr/bin/clang -target arm64-apple-ios16.5 --ld-path="$XNUDEPS/host/bin/ld64" \
    -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -O1 \
    -Wall -Wextra -Werror -dynamiclib -install_name /redwood/lib/liblemonade-bt-trace.dylib \
    -framework CoreFoundation -framework IOKit \
    -o "$OUT/liblemonade-bt-trace.dylib" userland/src/lemonade_bt_trace.c
"$THEOS/toolchain/linux/iphone/bin/ldid" -S "$OUT/liblemonade-bt-trace.dylib"
python3 - <<'PY'
import hashlib,json,struct
from pathlib import Path
out=Path('out/lemonade-bt-trace');binary=out/'liblemonade-bt-trace.dylib'
data=binary.read_bytes()
if struct.unpack_from('<4I',data)!=(0xfeedfacf,0x100000c,0,6):
 raise SystemExit('Bluetooth trace library is not arm64 Mach-O')
sources=['userland/src/lemonade_bt_trace.c','userland/src/lemonade_bt_ioctl_bridge.h',
         'xnu/files/bsd/dev/lemonade_bt_protocol.h','devices/oneplus-9/tools/lemonade-build-bt-trace.sh']
(out/'build.json').write_text(json.dumps({
 'profile':'lemonade-bluetooth-trace-v1','observation_only':True,'hardware_verified':False,
 'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),
 'sources':{s:hashlib.sha256(Path(s).read_bytes()).hexdigest() for s in sources}},indent=2)+'\n')
print('Built observation-only Bluetooth interface probe; device validation pending.')
PY
