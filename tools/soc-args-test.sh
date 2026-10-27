#!/bin/sh
# Check that tools/soc-args.py still reproduces the POCO X5 Pro's soc_* arguments from its own
# device tree, and that devices/poco-x5-pro/soc.args is in step with it. A guard against a change
# to the generator (or the parser) silently shifting the addresses a port would get.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
dtb="$root/prebuilt/uefi/redwood.dtb"
want="$root/common/devices/redwood.soc.args"
got=$(python3 "$root/tools/soc-args.py" "$dtb")

# the values the kernel carries as defaults (bsd/dev/redwood_mpss.c, redwood_ipa.c): the generator
# must reproduce exactly these for the first phone, or a port built from a real tree is suspect
expected='soc_ipa_base=0x1e40000
soc_gsi_base=0x1e04000
soc_ipcc=0x408000
soc_aoss=0xc300000
soc_rsc=0x18220000
soc_apps_smmu=0x15000000
soc_cmd_db=0x80860000
soc_mpss_mem=0x8b800000
modem_ipa_fw_at=0x8b710000
soc_smem=0x80900000
soc_imem=0x14600000
soc_ipa_stream=0x480
soc_ipa_stream_uc=0x482
soc_pas_mpss=0x4
soc_pas_ipa=0xf
soc_ipa_fw=yupik_ipa_fws'

if [ "$got" != "$expected" ]; then
	echo "soc-args-test: FAIL -- the generator no longer reproduces the POCO X5 Pro's values"
	tmp=$(mktemp); printf '%s\n' "$expected" > "$tmp"
	printf '%s\n' "$got" | diff -u "$tmp" - || true
	rm -f "$tmp"
	exit 1
fi
if [ "$got" != "$(cat "$want")" ]; then
	echo "soc-args-test: FAIL -- devices/poco-x5-pro/soc.args is out of step with the generator (regenerate it)"
	exit 1
fi
echo "soc-args-test: the generator reproduces all 16 POCO X5 Pro soc_* values, and soc.args matches"
