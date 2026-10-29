#!/bin/bash
# Build the offline app restorer and narrowly selected first-run preferences.
set -euo pipefail
umask 077
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT"
OUT=out/lemonade-apps-runtime
mkdir -p "$OUT"
CC=(/usr/bin/clang -target arm64-apple-ios16.5 --ld-path="$XNUDEPS/host/bin/ld64"
    -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -O1 -fblocks -fobjc-arc
    -Wall -Wextra -Werror -Wno-deprecated-declarations)
"${CC[@]}" -framework Foundation -o "$OUT/lemonade-apps" userland/src/lemonade_apps.m
"$THEOS/toolchain/linux/iphone/bin/ldid" -Suserland/src/lemonade_apps.entitlements.plist "$OUT/lemonade-apps"
"${CC[@]}" -dynamiclib -install_name /redwood/lib/apps.dylib -framework Foundation \
    -framework UIKit -framework OpenGLES -o "$OUT/apps.dylib" userland/src/lemonade_apps_setup.m
"$THEOS/toolchain/linux/iphone/bin/ldid" -S "$OUT/apps.dylib"
python3 - <<'PY'
import hashlib,json,struct
from pathlib import Path
out=Path('out/lemonade-apps-runtime')
sources=['userland/src/lemonade_apps.m','userland/src/lemonade_apps_setup.m',
         'userland/src/lemonade_apps.entitlements.plist','devices/oneplus-9/tools/lemonade-build-apps-runtime.sh']
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
artifacts={}
for name,kind in [('lemonade-apps',2),('apps.dylib',6)]:
 p=out/name;data=p.read_bytes();assert struct.unpack_from('<4I',data)==(0xfeedfacf,0x100000c,0,kind)
 artifacts[name]={'sha256':digest(p),'bytes':len(data)}
(out/'build.json').write_text(json.dumps({'profile':'lemonade-apps-runtime-v1',
 'sources':{n:digest(Path(n)) for n in sources},'artifacts':artifacts,
 'network_requests':False,'foreground_launches':False,'hardware_verified':False},indent=2)+'\n')
print('Built signed offline app restorer and app preferences; device validation pending.')
PY
