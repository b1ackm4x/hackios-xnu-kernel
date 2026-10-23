#!/bin/bash
# Host-only build probe. Keeps candidates separate; never builds or deploys an OS image.
# Run in the existing Linux build container with THEOS pointing at the iOS 16.5 SDK.
set -euo pipefail
umask 077
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT"
OUT="$ROOT/out/lemonade-metal"
MESA="$ROOT/xnu-deps/build/mesa"
BUILD="$MESA/build-softpipe-freedreno"
SDK="$THEOS/sdks/iPhoneOS16.5.sdk"
mkdir -p "$OUT"
rm -f "$OUT/manifest.json" "$OUT/abi-layout-audit.json"
[[ -f "$BUILD/build.ninja" && -d "$SDK" ]] || {
    echo 'Prepare the existing Mesa build and iOS 16.5 SDK first.' >&2
    exit 1
}
# Do not silently compile a cached copy of the compiler contract.
python3 - "$ROOT" <<'PY'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
source = root / 'tools/mesa/air'
copied = root / 'xnu-deps/build/mesa/mesa-25.0.7/src/compiler/air'
patches = sorted((root / 'tools/mesa').glob('*.patch'))
expected = hashlib.sha256(b''.join(p.read_bytes() for p in patches)).hexdigest()
stamp = root / 'xnu-deps/build/mesa/mesa-25.0.7/.redwood-patches'
if not patches or not stamp.is_file() or stamp.read_text().strip() != expected:
    raise SystemExit('Configured Mesa patch identity differs; rebuild Mesa first')
for p in source.rglob('*'):
    if p.is_file() and (not (copied / p.relative_to(source)).is_file() or
                        p.read_bytes() != (copied / p.relative_to(source)).read_bytes()):
        raise SystemExit(f'Mesa source copy differs: {p.relative_to(root)}; rebuild Mesa first')
paths = ['userland/root.apfs', 'userland/system.apfs', 'local/lemonade/images.json',
         'xnu-deps/build/mesa/out/libOSMesa.8.dylib', 'userland/obj/libOSMesa.8.dylib']
def sha(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()
record = {s: sha(root / s) for s in paths if (root / s).is_file()}
(root / 'out/lemonade-metal/baseline.json').write_text(json.dumps(record, indent=2) + '\n')
PY

# Only these targets; do not replace the already-tested GLES library in mesa/out.
ninja -C "$BUILD" -j"${JOBS:-8}" \
    src/gallium/drivers/freedreno/air_run \
    src/gallium/drivers/freedreno/libredwoodair.dylib > "$OUT/mesa-build.log" 2>&1
llvm-strip -x -o "$OUT/libredwoodair.dylib" \
    "$BUILD/src/gallium/drivers/freedreno/libredwoodair.dylib"
install_name_tool -id /redwood/lib/libredwoodair.dylib "$OUT/libredwoodair.dylib"
cp "$BUILD/src/gallium/drivers/freedreno/air_run" "$OUT/air_run"

DYN=(xnu-clang -arch arm64 -isysroot "$SDK" -O1)
{
    "${DYN[@]}" -dynamiclib -install_name /redwood/lib/libredwoodmetal.dylib \
        -framework Foundation -framework IOKit -framework Metal -lobjc \
        -o "$OUT/libredwoodmetal.dylib" userland/src/redwoodmetal.m userland/src/redwooddrawable.m
    "${DYN[@]}" -bundle -framework Foundation -framework IOKit -framework Metal -lobjc \
        -o "$OUT/RedwoodMetal" userland/src/redwoodmetal.m userland/src/redwooddrawable.m
    "${DYN[@]}" -framework Foundation -framework IOKit -framework Metal -lobjc \
        -o "$OUT/metal_drv_test" userland/src/metal_drv_test.m
    "${DYN[@]}" -framework Foundation -framework UIKit -framework QuartzCore \
        -framework Metal -framework IOSurface -framework IOKit -lobjc \
        -o "$OUT/RedwoodTriangle" userland/src/triangle_app.m
} > "$OUT/frontend-build.log" 2>&1

"${DYN[@]}" -Xclang -fdump-record-layouts-complete -fsyntax-only \
    userland/src/redwoodmetal.m > "$OUT/frontend-layouts.txt" 2> "$OUT/layout-errors.log"
"${DYN[@]}" -Xclang -fdump-record-layouts-complete -fsyntax-only -x c \
    tools/mesa/air/air_metal.h > "$OUT/compiler-layouts.txt" 2>> "$OUT/layout-errors.log"
python3 tools/lemonade-metal-abi.py "$OUT"

python3 - "$ROOT" <<'PY'
import hashlib, json, re, struct, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
root = Path(sys.argv[1]); out = root / 'out/lemonade-metal'
def sha(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()
baseline = json.loads((out / 'baseline.json').read_text())
for path, expected in baseline.items():
    if sha(root / path) != expected:
        raise SystemExit(f'Existing artifact changed unexpectedly: {path}')
artifacts = {}
for name in ['libredwoodair.dylib', 'air_run', 'libredwoodmetal.dylib',
             'RedwoodMetal', 'metal_drv_test', 'RedwoodTriangle']:
    p = out / name
    magic, cpu, subtype, kind = struct.unpack_from('<4I', p.read_bytes())
    if magic != 0xfeedfacf or cpu != 0x100000c or (subtype & 0xffffff) != 0:
        raise SystemExit(f'Not an ordinary ARM64 Mach-O: {name}')
    artifacts[name] = dict(sha256=sha(p), bytes=p.stat().st_size, macho_type=kind)
symbols = subprocess.check_output(['nm', '-gU', str(out / 'libredwoodair.dylib')], text=True)
exports = sorted(set(re.findall(r'\b_rwa_\w+', symbols)))
required = set()
for name in ['redwoodmetal.m', 'redwooddrawable.m', 'metal_drv_test.m']:
    source = (root / 'userland/src' / name).read_text()
    required.update('_' + s for s in re.findall(r'dlsym\([^,]+,\s*"(rwa_\w+)"\)', source))
missing = sorted(required - set(exports))
if missing:
    raise SystemExit(f'AIR library is missing requested exports: {missing}')
sources = {str(p.relative_to(root)): sha(p)
           for p in sorted((root / 'tools/mesa/air').rglob('*')) if p.is_file()}
for p in sorted((root / 'tools/mesa').glob('*.patch')):
    sources[str(p.relative_to(root))] = sha(p)
for p in sorted((root / 'tools/mesa/redwood-drm').rglob('*')):
    if p.is_file():
        sources[str(p.relative_to(root))] = sha(p)
for p in ['userland/src/redwoodmetal.m', 'userland/src/redwooddrawable.m',
          'userland/src/metal_drv_test.m',
          'userland/src/triangle_app.m', 'tools/lemonade-build-metal.sh',
          'tools/lemonade-metal-abi.py']:
    sources[p] = sha(root / p)
record = dict(built_at=datetime.now(timezone.utc).isoformat(),
              result='host_cross_build_pass', target='arm64-apple-ios16.5',
              kernel_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              userland_commit=subprocess.check_output(['common/ugit', 'rev-parse', 'HEAD'], text=True).strip(),
              mesa='25.0.7', sources=sources, artifacts=artifacts,
              rwa_exports=exports, requested_exports_verified=sorted(required),
              abi_layout_audit=json.loads((out / 'abi-layout-audit.json').read_text()),
              existing_artifacts_unchanged=baseline,
              device_tested=False, system_device_discovery_tested=False,
              metal_gpu_rendering_verified=False, packaged_or_deployed=False)
(out / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
print(f'PASS: {len(artifacts)} ARM64 candidates; {len(exports)} AIR exports; existing images/GLES unchanged')
print('Host cross-build only. No device discovery, GPU execution or image deployment.')
PY
