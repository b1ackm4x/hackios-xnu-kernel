#!/bin/bash
# Check a single-repository checkout and its host dependencies.
set -e
cd "$(dirname "$(readlink -f "$0")")/.."
[ -f userland/build.sh ] && [ -f sysimage/fetch.sh ] || {
    echo "setup: incomplete monorepo; clone the complete repository" >&2; exit 1;
}
git log -1 --format='setup: HackiOS source %h (%cs) %s'

# host check: what is missing only matters for the step that needs it
need() { # need <command> <what for>
	command -v "$1" >/dev/null 2>&1 && return 0
	printf '  %-22s missing -- needed %s\n' "$1" "$2"; MISSING=1
}
echo "setup: checking the host"
MISSING=0
need python3 "everywhere"
need git "everywhere"
need curl "to fetch iOS, XNU and dependencies"
need 7z "by sysimage/fetch.sh (7-Zip 23+, reads APFS)"
need gcc "by sysimage/fetch.sh (builds the LZBITMAP decoder)"
need qemu-system-aarch64 "to run in QEMU (DEVICE=qemu-virt)"
need fastboot "to boot a phone"
need adb "to take a phone's own firmware off it"
need clang "to build the kernel and the loader (not needed with prebuilt/)"
need lld-link "to build the loader (not needed with prebuilt/)"
[ -d "${THEOS:-$HOME/theos}/sdks/iPhoneOS16.5.sdk" ] ||
	echo "  theos + iPhoneOS16.5.sdk missing -- needed only to rebuild the kernel and our programs (prebuilt/ has them)"
[ $MISSING = 0 ] && echo "  all there"
cat <<EOF

Next:
  sysimage/fetch.sh                   # once: takes iOS 16.5 out of Apple's IPSW onto this machine (7.4 GB download)
  common/build.sh qemu-virt         # images (+ kernel and loader when the toolchain is there)
  common/run.sh qemu-virt           # iOS in a QEMU window
See README.md; porting to another device: docs/PORTING.md and common/new-device.sh.
EOF
