#!/bin/bash
# Linux build environment: GLES bridge, hardware diagnostic and checked APFS.
set -euo pipefail
# These modes become the filesystem seen by iOS mobile daemons. Host-private
# evidence lives under local/lemonade; making the APFS tree private prevents
# backboardd from loading the render libraries.
umask 022
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
cd "$ROOT"
python3 - <<'PY'
import hashlib, json
from pathlib import Path
for name, info in json.loads(Path('local/lemonade/gpu-firmware.json').read_text()).items():
    data = (Path('fw-gpu') / name).read_bytes()
    if not info['device_hash_verified'] or len(data) != info['bytes'] or hashlib.sha256(data).hexdigest() != info['sha256']:
        raise SystemExit(f'GPU firmware differs from the device-verified copy: {name}')
PY
bash devices/oneplus-9/tools/lemonade-build-gl.sh
TZ=${TZ:-Asia/Shanghai} SKIP="kernel loader boot" common/build.sh lemonade \
    GL=1 RC_JOBS="${LEMONADE_JOBS:-../devices/oneplus-9/lemonade-gpu.rc.jobs}"
python3 - <<'PY'
from pathlib import Path
for name in ('root', 'root/lib', 'root/lib/libredwood.dylib', 'root/lib/libredwood-sb.dylib',
             'root/lib/libredwood-gl.dylib', 'root/lib/libOSMesa.8.dylib'):
    path = Path('userland') / name
    if path.stat().st_mode & 0o5 != 0o5:
        raise SystemExit(f'iOS mobile daemons cannot read/traverse: {path}')
PY
tools/apfs/check-image.sh userland/root.apfs userland/root
umask 077
python3 - <<'PY'
import hashlib, json, subprocess
from pathlib import Path
path = Path('local/lemonade/images.json')
record = json.loads(path.read_text()) if path.exists() else {}
for name in ('userland/root.apfs', 'userland/system.apfs'):
    image = Path(name)
    digest = hashlib.sha256()
    with image.open('rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    previous = record.get(name, {})
    if name == 'userland/system.apfs':
        if (previous.get('bytes') == image.stat().st_size and previous.get('sha256') == digest.hexdigest()
                and 'apfsck clean' in previous.get('verification', '')):
            print('System APFS unchanged: rechecked SHA-256, retained its successful filesystem verification.')
        else:
            subprocess.run(['xnu-deps/build/apfsprogs/apfsck/apfsck', '-cw', str(image)], check=True)
    record[name] = {'bytes': image.stat().st_size, 'sha256': digest.hexdigest(),
                    'verification': 'apfsck clean; root also read back and compared entry by entry'}
record['root_jobs'] = {'sha256': hashlib.sha256(Path('userland/root/etc/rc.jobs').read_bytes()).hexdigest()}
path.write_text(json.dumps(record, indent=2) + '\n')
print('Checked GPU userland images and refreshed their manifest.')
PY
