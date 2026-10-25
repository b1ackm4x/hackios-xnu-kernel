#!/bin/bash
# Matching Metal frontend and AIR compiler. RUNTIME_ONLY=1 omits diagnostics/shaders.
set -euo pipefail
umask 022
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT"
M=xnu-deps/build/mesa/out
[[ -f "$M/libredwoodair.dylib" ]] || { echo 'Build with AIR=1 tools/mk-mesa.sh first.' >&2; exit 1; }
[[ "$(cat "$M/built-from-air")" == "$(cat tools/mesa/air/* | sha256sum)" ]] || {
    echo 'AIR library differs from the current frontend sources; rebuild it.' >&2; exit 1;
}
[[ "$(cat "$M/built-from")" == "$(cat tools/mesa/*.patch tools/mesa/redwood-drm/* | sha256sum)" ]] || {
    echo 'Mesa platform patches differ from the current library; rebuild it.' >&2; exit 1;
}
OUT=out/lemonade-metal
mkdir -p "$OUT"
export RUNTIME_ONLY=${RUNTIME_ONLY:-0}
[[ "$RUNTIME_ONLY" = 0 || "$RUNTIME_ONLY" = 1 ]] || { echo 'RUNTIME_ONLY must be 0 or 1.' >&2; exit 1; }
CC=(xnu-clang -arch arm64 -isysroot "$THEOS/sdks/iPhoneOS16.5.sdk" -O1)
"${CC[@]}" -dynamiclib -install_name /redwood/lib/libredwoodmetal.dylib \
    -framework Foundation -framework IOKit -framework Metal -lobjc \
    -o "$OUT/libredwoodmetal.dylib" userland/src/redwoodmetal.m userland/src/redwooddrawable.m
mkdir -p "$OUT/RedwoodMetal.bundle"
"${CC[@]}" -bundle -framework Foundation -framework IOKit -framework Metal -lobjc \
    -o "$OUT/RedwoodMetal.bundle/RedwoodMetal" userland/src/redwoodmetal.m userland/src/redwooddrawable.m
cp userland/src/metal_plugin.plist "$OUT/RedwoodMetal.bundle/Info.plist"
"${CC[@]}" -dynamiclib -install_name /redwood/lib/libredwood.dylib \
    -framework CoreFoundation -framework IOKit -lobjc -o "$OUT/libredwood.dylib" userland/src/inject.c
if [[ "$RUNTIME_ONLY" = 0 ]]; then
"${CC[@]}" -framework CoreFoundation -framework IOKit -o "$OUT/metal_props" userland/src/metal_props.c
"${CC[@]}" -DLEMONADE_NATIVE_MAIN -framework Foundation -framework Metal -framework IOSurface -lobjc \
    -o "$OUT/lemonade_metal_features" userland/src/lemonade_metal_features.m
"${CC[@]}" -framework Foundation -framework Metal -framework MetalPerformanceShaders -lobjc \
    -o "$OUT/lemonade_metal_mps" userland/src/lemonade_metal_mps.m
"${CC[@]}" -framework Foundation -framework Metal -framework QuartzCore -framework CoreGraphics -lobjc \
    -o "$OUT/lemonade_metal_ca" userland/src/lemonade_metal_ca.m
"${CC[@]}" -framework Foundation -framework IOKit -framework Metal -framework IOSurface -lobjc \
    -o "$OUT/metal_drv_test" userland/src/metal_drv_test.m userland/src/lemonade_metal_raster.m userland/src/lemonade_metal_features.m
"${CC[@]}" -Wno-four-char-constants -framework CoreFoundation -framework IOSurface \
    -framework IOKit -o "$OUT/drawable_test" userland/src/drawable_test.c userland/src/redwooddrawable.m
fi
cp "$M/libredwoodair.dylib" "$OUT/"
python3 - <<'PY'
import hashlib, json, os, struct
from pathlib import Path
out = Path('out/lemonade-metal')
runtime_only = os.environ['RUNTIME_ONLY'] == '1'
names = ['libredwoodmetal.dylib', 'libredwoodair.dylib', 'libredwood.dylib', 'RedwoodMetal.bundle/RedwoodMetal']
if not runtime_only:
    names += ['metal_drv_test', 'drawable_test', 'metal_props', 'lemonade_metal_features', 'lemonade_metal_mps', 'lemonade_metal_ca']
artifacts = {}
for name in names:
    data = (out / name).read_bytes()
    header = struct.unpack_from('<4I', data)
    expected = 8 if '.bundle/' in name else 6 if name.endswith('.dylib') else 2
    if header != (0xfeedfacf, 0x100000c, 0, expected):
        raise SystemExit('Unexpected arm64 Mach-O type: ' + name)
    artifacts[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    if name == 'libredwood.dylib': artifacts[name]['system_only'] = True
sources = ['userland/src/redwoodmetal.m', 'userland/src/redwooddrawable.m', 'userland/src/redwood_drawable.h',
           'userland/src/metal_plugin.plist', 'tools/lemonade-build-metal.sh']
if not runtime_only:
    sources += ['userland/src/metal_drv_test.m', 'userland/src/drawable_test.c', 'userland/src/lemonade_metal_raster.m',
                'userland/src/lemonade_metal_features.m', 'userland/src/lemonade_metal_test.metal',
                'userland/src/metal_props.c', 'userland/src/lemonade_metal_mps.m', 'userland/src/lemonade_metal_ca.m']
sources += ['userland/src/inject.c', 'userland/src/redwood_gl.h', 'userland/src/redwood_input.h']
plist = out / 'RedwoodMetal.bundle/Info.plist'
artifacts['RedwoodMetal.bundle/Info.plist'] = {'sha256': hashlib.sha256(plist.read_bytes()).hexdigest(), 'bytes': plist.stat().st_size}
sources += [str(p) for p in sorted(Path('tools/mesa/air').iterdir()) if p.is_file()]
sources += [str(p) for p in sorted(Path('tools/mesa').glob('*.patch'))]
sources += [str(p) for p in sorted(Path('tools/mesa/redwood-drm').iterdir()) if p.is_file()]
record = {'arch': 'arm64', 'profile': 'metal-runtime-v1' if runtime_only else 'metal-baseline-v1', 'artifacts': artifacts,
          'sources': {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sources}}
if not runtime_only:
    shader = out / 'lemonade_test.metallib'
    shader_bytes = shader.read_bytes()
    if shader_bytes[:4] != b'MTLB': raise SystemExit('Build the iOS test metallib first')
    shader_record = json.loads((out / 'shaders.json').read_text())
    if (shader_record.get('target') != 'air64-apple-ios16.5' or
        shader_record.get('source') != 'userland/src/lemonade_metal_test.metal' or
        shader_record.get('source_sha256') != hashlib.sha256(Path(shader_record['source']).read_bytes()).hexdigest() or
        shader_record.get('artifact_sha256') != hashlib.sha256(shader_bytes).hexdigest()):
        raise SystemExit('Test shader source or artifact changed; run tools/lemonade-build-metal-shaders.sh')
    artifacts[shader.name] = {'sha256': hashlib.sha256(shader_bytes).hexdigest(), 'bytes': len(shader_bytes)}
    record['shader_build'] = shader_record
(out / 'build.json').write_text(json.dumps(record, indent=2) + '\n')
print('Built matching Metal/AIR runtime' + ('.' if runtime_only else ' and hardware diagnostics.') + ' Device validation pending.')
PY
