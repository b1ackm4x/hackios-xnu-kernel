#!/bin/bash
# Linux build environment; THEOS supplies the matching iOS 16.5 SDK and cctools.
# Produces a separate checked kernel without replacing the upstream prebuilt.
set -euo pipefail
umask 077
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/xnu-env.sh"
cd "$ROOT"
command -v gawk >/dev/null || { echo 'Install gawk: mawk misgenerates XNU syscall casts.' >&2; exit 1; }
[[ -d "$XNU_SRC" ]] || xnu/apply.sh
[[ -x xnu-deps/host/bin/ld64 ]] || xnu-deps/setup.sh
cp xnu/files/bsd/dev/redwood_adreno.c "$XNU_SRC/bsd/dev/redwood_adreno.c"
cp xnu/files/bsd/dev/redwood_cpufreq.c "$XNU_SRC/bsd/dev/redwood_cpufreq.c"
for path in bsd/dev/lemonade_touch.c bsd/dev/lemonade_touch_protocol.h bsd/dev/lemonade_wifi.c bsd/dev/redwood_wifi.c bsd/dev/redwood_goodix.c bsd/dev/redwood_buttons.c bsd/dev/redwood_input.c bsd/sys/redwood_input.h osfmk/arm64/loader_return.c; do
    cp "xnu/files/$path" "$XNU_SRC/$path"
done
python3 - "$XNU_SRC/bsd/conf/files" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1]); s = p.read_text()
for name in ('lemonade_touch.c', 'lemonade_wifi.c'):
    if 'bsd/dev/' + name not in s:
        s += '\nbsd/dev/' + name + '\tstandard\n'
p.write_text(s)
PY
mkdir -p out local/lemonade
LOG="local/lemonade/gpu-kernel-build-$(date -u +%Y%m%dT%H%M%SZ).log"
touch out/.lemonade-kernel-start
status=0
./xnu-build.sh -j"${JOBS:-8}" -k > "$LOG" 2>&1 || status=$?
python3 - "$LOG" "$status" <<'PY'
import hashlib, json, os, shutil, struct, sys
from datetime import datetime, timezone
from pathlib import Path
log, status = Path(sys.argv[1]), int(sys.argv[2])
source = Path(os.environ['XNU_SRC']) / 'BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837'
if not source.exists() or source.stat().st_mtime < Path('out/.lemonade-kernel-start').stat().st_mtime:
    raise SystemExit(f'No freshly linked kernel. Build exit {status}; see {log}')
if any('error:' in line.lower() for line in log.read_text(errors='replace').splitlines()):
    raise SystemExit(f'Build diagnostic contains an error; see {log}')
data = source.read_bytes()
magic, cpu, sub, kind = struct.unpack_from('<4I', data)
if (magic, cpu, kind) != (0xfeedfacf, 0x100000c, 2):
    raise SystemExit('Output is not an arm64 Mach-O executable')
if status and (status != 2 or 'exported name not in import list:' not in log.read_text()):
    raise SystemExit(f'Unexpected build failure {status}; see {log}')
shutil.copy2(source, 'out/lemonade-kernel')
record = {'device': 'lemonade', 'stage': 'xnu-kernel',
          'built_at': datetime.now(timezone.utc).isoformat(), 'bytes': len(data),
          'sha256': hashlib.sha256(data).hexdigest(), 'arch': 'arm64', 'base': 'xnu-8796.121.2',
          'gpu_driver_sha256': hashlib.sha256(Path('xnu/files/bsd/dev/redwood_adreno.c').read_bytes()).hexdigest(),
          'cpu_driver_sha256': hashlib.sha256(Path('xnu/files/bsd/dev/redwood_cpufreq.c').read_bytes()).hexdigest(),
          'cpu_policy_supported': 'sm8350-capped-epss-v1',
          'touch_driver_supported': 'lemonade-s6sy792-v1',
          'touch_driver_sha256': hashlib.sha256(Path('xnu/files/bsd/dev/lemonade_touch.c').read_bytes()).hexdigest(),
          'touch_protocol_sha256': hashlib.sha256(Path('xnu/files/bsd/dev/lemonade_touch_protocol.h').read_bytes()).hexdigest(),
          'wifi_modes_supported': ['inspect', 'rails', 'domain', 'clocks', 'resets', 'controller', 'phy', 'root', 'radio', 'link', 'endpoint', 'mmio', 'smmu'],
          'wifi_driver_sha256': hashlib.sha256(Path('xnu/files/bsd/dev/lemonade_wifi.c').read_bytes()).hexdigest(),
          'build_log': str(log), 'gpu_hardware_verified': False}
Path('out/lemonade-kernel.manifest.json').write_text(json.dumps(record, indent=2) + '\n')
print(f'Checked lemonade XNU: {len(data)} bytes, SHA-256 {record["sha256"]}')
PY
