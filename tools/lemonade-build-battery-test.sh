#!/bin/bash
# Read-only native power-source acceptance, used only in a disposable overlay.
set -euo pipefail
umask 077
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT"
mkdir -p out
xnu-clang -arch arm64 -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -O1 \
    -Wall -Wextra -Werror -framework CoreFoundation -framework IOKit \
    -o out/lemonade-battery-test tools/lemonade-battery-test.c
python3 - <<'PY'
import hashlib, json, struct
from pathlib import Path
source = Path('tools/lemonade-battery-test.c')
binary = Path('out/lemonade-battery-test')
data = binary.read_bytes()
if struct.unpack_from('<4I', data) != (0xfeedfacf, 0x100000c, 0, 2):
    raise SystemExit('Battery acceptance output is not an arm64 Mach-O executable')
record = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
          'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
          'arch': 'arm64', 'purpose': 'RAM-only read-only native battery acceptance',
          'acceptance_profile': 'kernel-ioregistry-iops-v1'}
Path('out/lemonade-battery-test.manifest.json').write_text(json.dumps(record, indent=2) + '\n')
print('Battery acceptance executable compiled and verified')
PY
