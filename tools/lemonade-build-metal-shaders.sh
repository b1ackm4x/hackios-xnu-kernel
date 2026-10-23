#!/bin/bash
# Run on macOS with Apple's separately downloaded Metal toolchain.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
COMPILER=${METAL_COMPILER:-$ROOT/local/lemonade/toolchain/MetalToolchain/mounted/Metal.xctoolchain/usr/bin/metal}
[[ -x "$COMPILER" ]] || {
    echo 'Set METAL_COMPILER to the installed official Metal compiler, or mount the exported Metal toolchain read-only.' >&2
    exit 1
}
mkdir -p out/lemonade-metal
"$COMPILER" -target air64-apple-ios16.5 -std=ios-metal2.4 \
    -o out/lemonade-metal/lemonade_test.metallib userland/src/lemonade_metal_test.metal
python3 - "$COMPILER" <<'PY'
import hashlib, json, subprocess, sys
from pathlib import Path
source = Path('userland/src/lemonade_metal_test.metal')
binary = Path('out/lemonade-metal/lemonade_test.metallib')
data = binary.read_bytes()
if data[:4] != b'MTLB': raise SystemExit('Not a Metal library')
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
record = {'target': 'air64-apple-ios16.5', 'language': 'ios-metal2.4',
          'source': str(source), 'source_sha256': digest(source),
          'artifact': binary.name, 'artifact_sha256': digest(binary),
          'compiler_sha256': digest(Path(sys.argv[1]).resolve()),
          'compiler_version': subprocess.check_output([sys.argv[1], '--version'], text=True).strip()}
Path('out/lemonade-metal/shaders.json').write_text(json.dumps(record, indent=2) + '\n')
print('Compiled iOS Metal test library and verified its build receipt.')
PY
