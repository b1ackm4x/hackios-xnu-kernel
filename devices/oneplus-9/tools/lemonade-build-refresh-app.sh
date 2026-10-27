#!/bin/bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT"
OUT=out/lemonade-refresh-app
mkdir -p "$OUT/LemonadeRefresh.app"
xnu-clang -arch arm64 -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -O2 -fobjc-arc \
    -Wall -Wextra -framework UIKit -framework QuartzCore -framework Foundation -lobjc \
    -o "$OUT/LemonadeRefresh.app/LemonadeRefresh" userland/src/lemonade_refresh.m
python3 - <<'PY'
import plistlib
from pathlib import Path
out=Path('out/lemonade-refresh-app')
p=plistlib.loads(Path('userland/src/triangle_app.plist').read_bytes())
p.update(CFBundleDisplayName='Refresh',CFBundleExecutable='LemonadeRefresh',CFBundleName='LemonadeRefresh',
 CFBundleIdentifier='com.redwood.refresh',CADisableMinimumFrameDurationOnPhone=True)
p.pop('CFBundleIcons',None)
p['CFBundleURLTypes']=[{'CFBundleURLName':'com.redwood.refresh','CFBundleURLSchemes':['lemonaderefresh']}]
(out/'LemonadeRefresh.app/Info.plist').write_bytes(plistlib.dumps(p))
e={'application-identifier':'com.redwood.refresh','com.apple.private.security.no-container':True,'platform-application':True}
(out/'entitlements.plist').write_bytes(plistlib.dumps(e))
PY
"$THEOS/toolchain/linux/iphone/bin/ldid" -S"$OUT/entitlements.plist" "$OUT/LemonadeRefresh.app/LemonadeRefresh"
xnu-clang -arch arm64 -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -O2 -fobjc-arc \
    -framework Foundation -framework UIKit -framework IOKit -lobjc -o "$OUT/refresh-launch" userland/src/lemonade_refresh_launch.m
"$THEOS/toolchain/linux/iphone/bin/ldid" -Suserland/src/battery_test.entitlements "$OUT/refresh-launch"
python3 - <<'PY'
import hashlib,json
from pathlib import Path
out=Path('out/lemonade-refresh-app')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
sources=['userland/src/lemonade_refresh.m','userland/src/triangle_app.plist','devices/oneplus-9/tools/lemonade-build-refresh-app.sh',
 'userland/src/lemonade_refresh_launch.m','userland/src/battery_test.entitlements']
(out/'build.json').write_text(json.dumps({'profile':'lemonade-refresh-app-v1',
 'sources':{p:sha(Path(p)) for p in sources},
 'artifacts':{p.name:sha(p) for p in sorted((out/'LemonadeRefresh.app').iterdir())},
 'helpers':{'refresh-launch':sha(out/'refresh-launch')}},indent=2)+'\n')
PY
