#!/bin/bash
# Build only the GLES bridge and its readback diagnostic against the checked
# Mesa output. Run inside the Linux build environment with THEOS set.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT/userland"
M=../xnu-deps/build/mesa/out
SDK="$THEOS/sdks/iPhoneOS16.5.sdk"
[[ -f "$M/libOSMesa.8.dylib" ]] || { echo 'Build Mesa with tools/mk-mesa.sh first.' >&2; exit 1; }
[[ "$(cat "$M/built-from")" == "$(cat ../tools/mesa/*.patch ../tools/mesa/redwood-drm/* | sha256sum)" ]] || {
    echo 'Mesa source/patch manifest mismatch; rebuild Mesa.' >&2; exit 1;
}
mkdir -p obj
./mkeagl.py src/eagl_slots.txt "$M/gl.xml" "$M/exported-gl.txt" src/eagl.c > obj/eagl_gen.h
xnu-clang -arch arm64 -isysroot "$SDK" -O1 -dynamiclib \
    -install_name /redwood/lib/libredwood-gl.dylib -Iobj \
    -framework CoreFoundation -framework IOKit -framework IOSurface -lobjc \
    "$M/libOSMesa.8.dylib" -o obj/libredwood-gl.dylib src/eagl.c src/gl_osmesa.c
xnu-clang -arch arm64 -isysroot "$SDK" -O1 -framework CoreFoundation -framework IOKit \
    "$M/libOSMesa.8.dylib" -o obj/gl_test src/gl_test.c
cp "$M/libOSMesa.8.dylib" obj/
echo 'Built GLES bridge, Mesa library, and GPU pixel-readback diagnostic.'
