#!/bin/bash
# Build only the boot initializer and Metal startup helper used by the base.
set -euo pipefail
umask 022
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT"
OUT=out/lemonade-base-runtime
mkdir -p "$OUT"
CC=(xnu-clang -arch arm64 -Os -static -nostdlib -ffreestanding -fno-stack-protector -fno-builtin -Wall -Wextra)
"${CC[@]}" -Iuserland/libmini -c userland/libmini/mini.c -o "$OUT/mini.o"
"${CC[@]}" -c userland/libmini/start.S -o "$OUT/start.o"
"${CC[@]}" -Wno-unused-function -DREDWOOD_RELEASE=1 -Iuserland/libmini \
    -Wl,-e,_start -Wl,-pagezero_size,0x100000000 -Wl,-dead_strip \
    -o "$OUT/init" userland/src/init.c "$OUT/mini.o" "$OUT/start.o"
xnu-clang -arch arm64 -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -Os \
    -Wall -Wextra -Werror -framework CoreFoundation -framework IOKit \
    -o "$OUT/metal-enable" tools/lemonade-metal-enable.c
python3 - <<'PY'
import hashlib,json,struct
from pathlib import Path
out=Path('out/lemonade-base-runtime')
sources=['userland/src/init.c','userland/libmini/mini.c','userland/libmini/mini.h',
         'userland/libmini/start.S','tools/lemonade-metal-enable.c','tools/lemonade-build-base-runtime.sh']
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
artifacts={}
for name in ('init','metal-enable'):
 p=out/name;b=p.read_bytes();assert struct.unpack_from('<4I',b)==(0xfeedfacf,0x100000c,0,2)
 artifacts[name]={'sha256':digest(p),'bytes':len(b)}
for marker in (b'64 MB of anonymous memory',b'read a 300 KB file',b'run a program through dyld',b'300 files in one directory'):
 assert marker not in (out/'init').read_bytes(),marker
(out/'build.json').write_text(json.dumps({'profile':'lemonade-base-runtime-v1','artifacts':artifacts,
 'sources':{p:digest(Path(p)) for p in sources},'boot_self_tests':False},indent=2)+'\n')
print('Built production init and Metal startup helper; boot self-test code absent.')
PY
