#!/bin/bash
# Fresh-workspace build with hash-pinned, credential-free public runtime inputs.
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${THEOS:?Set THEOS to a fresh build-toolchain directory}"
export JOBS=${JOBS:-4}
export LEMONADE_WIFI=1
python3 tools/ci/setup-toolchain.py
bash xnu/apply.sh
bash xnu-deps/setup.sh
bash tools/mk-ath11k.sh
bash tools/mk-wlfw.sh
bash tools/lemonade-build-kernel.sh
LOADER_USB_SERIAL=lemonade-ci bash xnuloader/build.sh
AIR=1 bash tools/mk-mesa.sh
RUNTIME_ONLY=1 bash tools/lemonade-build-metal.sh
bash tools/lemonade-build-gl.sh
bash tools/lemonade-build-base-runtime.sh
python3 tools/ci/fetch-runtime-inputs.py
python3 tools/ci/package-lemonade.py
