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
# Read to EOF and replace atomically: shared-folder cp can observe a stale size
# immediately after a host edit. The same bytes are checked again after linking.
mkdir -p out
python3 - <<'COPY'
import hashlib, json, os
from pathlib import Path
paths = ['bsd/dev/redwood_adreno.c', 'bsd/dev/redwood_cpufreq.c',
	 'bsd/dev/lemonade_display.c', 'bsd/sys/lemonade_display.h', 'iokit/Kernel/MiniIOMobileFramebuffer.cpp',
         'bsd/dev/lemonade_touch.c', 'bsd/dev/lemonade_touch_protocol.h',
         'bsd/dev/lemonade_wifi.c', 'bsd/dev/lemonade_mhi_protocol.h', 'bsd/dev/redwood_wifi.c', 'bsd/dev/redwood_goodix.c',
         'bsd/dev/redwood_lx.c', 'bsd/sys/redwood_lx.h', 'bsd/sys/redwood_qrtr.h', 'bsd/sys/lemonade_wifi.h',
         'bsd/dev/redwood_buttons.c', 'bsd/dev/redwood_input.c', 'bsd/sys/redwood_input.h',
         'osfmk/arm64/loader_return.c']
inputs = {}
for relative in paths:
    source = Path('xnu/files') / relative
    data = source.read_bytes()
    target = Path(os.environ['XNU_SRC']) / relative
    if not target.exists() or target.read_bytes() != data:
        temporary = target.with_suffix(target.suffix + '.lemonade-tmp')
        temporary.write_bytes(data)
        temporary.replace(target)
    inputs[relative] = hashlib.sha256(data).hexdigest()
    if hashlib.sha256(target.read_bytes()).hexdigest() != inputs[relative]:
        raise SystemExit('Kernel input copy mismatch: ' + relative)
Path('out/lemonade-build-inputs.json').write_text(json.dumps(inputs, indent=2) + '\n')
COPY
python3 - "$XNU_SRC/bsd/conf/files" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1]); original = p.read_text(); s = original
for name in ('lemonade_touch.c', 'lemonade_wifi.c', 'lemonade_display.c'):
    if 'bsd/dev/' + name not in s:
        s += '\nbsd/dev/' + name + '\tstandard\n'
if s != original:
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
inputs = json.loads(Path('out/lemonade-build-inputs.json').read_text())
for relative, expected in inputs.items():
    for copied in (Path('xnu/files') / relative, Path(os.environ['XNU_SRC']) / relative):
        if hashlib.sha256(copied.read_bytes()).hexdigest() != expected:
            raise SystemExit('Kernel source changed during build: ' + str(copied))
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
          'gpu_clock_mhz_supported': [315, 379, 443, 540],
          'cpu_driver_sha256': hashlib.sha256(Path('xnu/files/bsd/dev/redwood_cpufreq.c').read_bytes()).hexdigest(),
          'cpu_policy_supported': 'sm8350-capped-epss-v1',
          'display_modes_supported': ['inspect', 'clocks', 'irq', 'sync', 'smmu', 'map', 'flip', 'manual'],
          'display_driver_sha256': hashlib.sha256(Path('xnu/files/bsd/dev/lemonade_display.c').read_bytes()).hexdigest(),
          'touch_driver_supported': 'lemonade-s6sy792-v1',
          'touch_driver_sha256': hashlib.sha256(Path('xnu/files/bsd/dev/lemonade_touch.c').read_bytes()).hexdigest(),
          'touch_protocol_sha256': hashlib.sha256(Path('xnu/files/bsd/dev/lemonade_touch_protocol.h').read_bytes()).hexdigest(),
          'wifi_modes_supported': ['inspect', 'rails', 'domain', 'clocks', 'resets', 'controller', 'phy', 'root', 'radio', 'link', 'endpoint', 'mmio', 'smmu', 'sid', 'dma', 'dma-fault', 'bhi', 'mhi', 'bhie', 'ipcr', 'qmi', 'driver', 'scan', 'station'],
          'wifi_driver_sha256': hashlib.sha256(Path('xnu/files/bsd/dev/lemonade_wifi.c').read_bytes()).hexdigest(),
          'build_log': str(log), 'gpu_hardware_verified': False}
ath = Path('xnu-deps/build/ath11k/out/ath11k.o')
profile = Path('xnu-deps/build/ath11k/obj/.source-profile')
if ath.exists() and profile.exists() and profile.read_text().strip() == 'src-lemonade':
    record['wifi_driver_profile'] = 'qca6490-hw10-v1'
    record['wifi_ath11k_sha256'] = hashlib.sha256(ath.read_bytes()).hexdigest()
Path('out/lemonade-kernel.manifest.json').write_text(json.dumps(record, indent=2) + '\n')
print(f'Checked lemonade XNU: {len(data)} bytes, SHA-256 {record["sha256"]}')
PY
