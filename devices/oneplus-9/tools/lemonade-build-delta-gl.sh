#!/bin/bash
# Build the opt-in Delta OpenGL loader and startup diagnostics on Linux.
set -euo pipefail
umask 077
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT"
OUT=out/lemonade-delta-gl
mkdir -p "$OUT"
/usr/bin/clang -target arm64-apple-ios16.5 --ld-path="$XNUDEPS/host/bin/ld64" \
    -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -O1 -fblocks -fobjc-arc \
    -Wall -Wextra -Werror -Wno-deprecated-declarations -dynamiclib \
    -install_name /redwood/lib/delta.dylib -framework Foundation -framework OpenGLES \
    -o "$OUT/delta.dylib" userland/src/lemonade_delta_gl.m
"$THEOS/toolchain/linux/iphone/bin/ldid" -S "$OUT/delta.dylib"
python3 - <<'PY'
import hashlib,json,struct
from pathlib import Path
out=Path('out/lemonade-delta-gl');binary=out/'delta.dylib';data=binary.read_bytes()
assert struct.unpack_from('<4I',data)==(0xfeedfacf,0x100000c,0,6)
offset=32;signature=False
for _ in range(struct.unpack_from('<I',data,16)[0]):
    command,length=struct.unpack_from('<II',data,offset)
    signature|=command==0x1d;offset+=length
assert signature
sources=['userland/src/lemonade_delta_gl.m','devices/oneplus-9/tools/lemonade-build-delta-gl.sh']
(out/'build.json').write_text(json.dumps({
    'profile':'lemonade-delta-gl-v1','sha256':hashlib.sha256(data).hexdigest(),
    'bytes':len(data),'hardware_verified':False,
    'sources':{s:hashlib.sha256(Path(s).read_bytes()).hexdigest() for s in sources}
},indent=2)+'\n')
print('Built signed arm64 Delta OpenGL loader; this build alone is not device validation.')
PY
