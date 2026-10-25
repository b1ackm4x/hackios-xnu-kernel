#!/bin/bash
# Fresh-workspace component build. No system image or device files are consumed.
set -Eeuo pipefail
cd "$(dirname "$0")/../.."
# GitHub images also ship unrelated compilers/Python versions ahead of apt's
# tools. Match the Ubuntu 24.04 toolchain validated by the cold local build.
export PATH="/usr/bin:/bin:$PATH"
report_failure() {
    local status=$?
    set +e
    for log in xnu-deps/exporthdrs.log xnu-deps/setup.log local/lemonade/gpu-kernel-build-*.log xnu-deps/build/mesa/meson-setup.log xnu-deps/build/mesa/ninja.log; do
        if [[ -f "$log" ]]; then
            printf 'Build diagnostics: %s\n' "$log"
            tail -n 100 "$log"
        fi
    done
    exit "$status"
}
trap report_failure ERR
: "${THEOS:?Set THEOS to a fresh build-toolchain directory}"
export JOBS=${JOBS:-4}
export LEMONADE_WIFI=1
clang --version
python3 --version
python3 tools/ci/setup-toolchain.py
bash xnu/apply.sh
bash xnu-deps/setup.sh
bash tools/mk-ath11k.sh
# WLFW emits a generated header; a cold build has no BSD object directory yet.
mkdir -p xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT
bash tools/mk-wlfw.sh
bash tools/lemonade-build-kernel.sh
LOADER_USB_SERIAL=lemonade-ci bash xnuloader/build.sh
AIR=1 bash tools/mk-mesa.sh
RUNTIME_ONLY=1 bash tools/lemonade-build-metal.sh
bash tools/lemonade-build-gl.sh
bash tools/lemonade-build-base-runtime.sh
python3 tools/ci/package-lemonade.py
