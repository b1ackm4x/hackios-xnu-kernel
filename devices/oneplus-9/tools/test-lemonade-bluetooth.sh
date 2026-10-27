#!/bin/bash
# Exercise the actual transport, firmware parser and shared regulator code.
set -euo pipefail
cd "$(dirname "$0")/../../.."
WORK=$(mktemp -d "${TMPDIR:-/tmp}/lemonade-bt-tests.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
CC=${CC:-clang}
FLAGS=(-std=c11 -Wall -Wextra -Werror -Wno-unused-function -Wno-unused-parameter -Wno-sign-compare
       -fsanitize=address,undefined -fno-omit-frame-pointer -pthread -Ixnu/files -Idevices/oneplus-9/tools)
if [[ $(uname -s) == Linux ]]; then FLAGS+=(-D_DEFAULT_SOURCE); fi
for suite in protocol firmware queue uart reconnect le-host; do
    "$CC" "${FLAGS[@]}" "devices/oneplus-9/tools/test-lemonade-bt-$suite.c" -o "$WORK/$suite"
    "$WORK/$suite"
done
"$CC" "${FLAGS[@]}" devices/oneplus-9/tools/test-lemonade-radio.c -o "$WORK/radio"
"$WORK/radio"
python3 devices/oneplus-9/tools/test-lemonade-bt-probe.py
if [[ $(uname -s) == Darwin && $(uname -m) == arm64 ]]; then
    "$CC" "${FLAGS[@]}" devices/oneplus-9/tools/test-lemonade-bt-ioctl.c -o "$WORK/ioctl"
    "$WORK/ioctl"
    "$CC" "${FLAGS[@]}" -framework CoreFoundation -framework IOKit \
        devices/oneplus-9/tools/test-lemonade-bt-compat.c -o "$WORK/compat"
    "$WORK/compat"
fi
