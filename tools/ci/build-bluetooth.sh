#!/bin/bash
# Compile the production Bluetooth helpers without consuming an Apple image.
# The private runtime packer separately binds the exact native binary and board.
set -euo pipefail
cd "$(dirname "$0")/../.."
source xnu-env.sh
OUT=out/lemonade-bt-runtime
mkdir -p "$OUT"
CC=(/usr/bin/clang -target arm64-apple-ios16.5 --ld-path="$XNUDEPS/host/bin/ld64"
    -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -O1 -Wall -Wextra -Werror)
"${CC[@]}" -fblocks -framework CoreFoundation -framework IOKit \
    -o "$OUT/lemonade-bt-bootstrap" userland/src/lemonade_bt_bootstrap.c
"${CC[@]}" -dynamiclib -install_name /redwood/lib/liblemonade-bt-compat.dylib \
    -framework CoreFoundation -framework IOKit \
    -o "$OUT/liblemonade-bt-compat.dylib" userland/src/lemonade_bt_compat.c
for binary in lemonade-bt-bootstrap liblemonade-bt-compat.dylib; do
    "$THEOS/toolchain/linux/iphone/bin/ldid" -S "$OUT/$binary"
done
python3 - <<'PY'
import hashlib,json,struct
from pathlib import Path
out=Path('out/lemonade-bt-runtime')
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
artifacts={}
for name in ('lemonade-bt-bootstrap','liblemonade-bt-compat.dylib'):
    path=out/name; data=path.read_bytes()
    if struct.unpack_from('<4I',data)!=(0xfeedfacf,0x100000c,0,6 if name.endswith('.dylib') else 2):
        raise SystemExit('Expected an ARM64 Mach-O: '+name)
    artifacts[name]={'sha256':digest(path),'bytes':len(data)}
sources=('userland/src/lemonade_bt_bootstrap.c','userland/src/lemonade_bt_compat.c',
         'tools/ci/build-bluetooth.sh')
(out/'build.json').write_text(json.dumps({
    'profile':'lemonade-bluetooth-components-v1','hardware_verified':False,
    'requires_exact_native_image_and_board':True,'artifacts':artifacts,
    'sources':{p:digest(Path(p)) for p in sources}},indent=2)+'\n')
print('Built generic production Bluetooth helpers; no native image or device input consumed.')
PY
