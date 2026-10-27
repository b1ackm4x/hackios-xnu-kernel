#!/bin/bash
# Build the XNU loader as a freestanding AArch64 UEFI application.
set -e
cd "$(dirname "$0")"
EXTRA_CFLAGS=()
# CI and open-kernel builds use a transient placeholder without overwriting the
# maintainer's locally generated iOS identity header.
if [ "${LOADER_PLACEHOLDER:-0}" = 1 ]; then
  IDENTITY_BUILD=$(mktemp -d "${TMPDIR:-/tmp}/hackios-identity.XXXXXX")
  trap 'rm -rf "$IDENTITY_BUILD"' EXIT
  python3 mkidentity.py --placeholder > "$IDENTITY_BUILD/identity.h"
  EXTRA_CFLAGS+=("-DHACKIOS_IDENTITY_HEADER=\"$IDENTITY_BUILD/identity.h\"")
else
  [ -f identity.h ] || bash mkidentity.sh
fi
if [ -n "${LOADER_USB_SERIAL:-}" ]; then
  [[ "$LOADER_USB_SERIAL" =~ ^[a-zA-Z0-9_-]{1,31}$ ]] || { echo "invalid LOADER_USB_SERIAL" >&2; exit 1; }
  EXTRA_CFLAGS+=("-DHACKIOS_USB_SERIAL=\"$LOADER_USB_SERIAL\"")
fi
clang --target=aarch64-unknown-windows -ffreestanding -fno-builtin -fno-stack-protector -fshort-wchar \
  -mgeneral-regs-only -O1 -Wall -Wextra -Wno-unused-parameter "${EXTRA_CFLAGS[@]}" -c loader.c -o loader.obj
clang --target=aarch64-unknown-windows -ffreestanding -fno-builtin -fno-stack-protector -fshort-wchar \
  -mgeneral-regs-only -O1 -Wall -Wextra -Werror -c lemonade_battery.c -o lemonade_battery.obj
# /base: UEFI loads an image at its preferred address when that is free. The PE default (0x140000000)
# is in the middle of the phone's largest block of RAM, which XNU needs in one piece.
lld-link /subsystem:efi_application /entry:efi_main /nodefaultlib /base:0xA1000000 /out:loader.efi loader.obj lemonade_battery.obj
ls -la loader.efi
