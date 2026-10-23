#!/usr/bin/env python3
"""Apply the HackiOS UEFI hooks to the pinned lemonade port, locally only."""
from pathlib import Path
import os
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MU = ROOT / "Mu-Silicium"
PIN = (ROOT / "uefi/BASE").read_text().strip()
os.umask(0o077)


def replace_once(path, old, new):
    text = path.read_text()
    if new in text:
        return
    if text.count(old) != 1:
        raise SystemExit(f"Expected exactly one patch anchor: {path}")
    path.write_text(text.replace(old, new, 1))


if subprocess.check_output(["git", "-C", str(MU), "rev-parse", "HEAD"], text=True).strip() != PIN:
    raise SystemExit("Mu-Silicium must be at uefi/BASE before applying this port")
patch = ROOT / "uefi/mu-silicium.patch"
if subprocess.run(["git", "-C", str(MU), "apply", "--check", str(patch)], capture_output=True).returncode == 0:
    subprocess.run(["git", "-C", str(MU), "apply", "--whitespace=nowarn", str(patch)], check=True)
elif "XnuLoaderMain ();" not in (MU / "Silicon/Silicium/SiliciumPkg/Library/BootManagerLib/BootManager.c").read_text():
    raise SystemExit("UEFI hooks do not apply cleanly; inspect the local tree")

lib = "Silicon/Silicium/SiliciumPkg/Library/BootManagerLib"
for filename in ("XnuLoader.c", "XnuLoader.h"):
    dest = MU / lib / filename
    if not dest.exists():
        shutil.copy2(ROOT / "uefi/files" / lib / filename, dest)
replace_once(MU / lib / "XnuLoader.c", "  BlockIo = FindPartition (XNU_LOG_PARTITION);",
             "  // Initial lemonade sessions keep all diagnostics in RAM/USB.\n"
             "  if (XNU_NO_PERSISTENT_LOGS) {\n    return;\n  }\n\n"
             "  BlockIo = FindPartition (XNU_LOG_PARTITION);")
replace_once(MU / lib / "XnuLoader.h", "#define _XNU_LOADER_H_",
             "#define _XNU_LOADER_H_\n\n#ifndef XNU_NO_PERSISTENT_LOGS\n"
             "#define XNU_NO_PERSISTENT_LOGS 0\n#endif")
memory = MU / "Platforms/OnePlus/lemonadePkg/Library/MemoryMapLib/MemoryMapLib.c"
replace_once(memory, "  // Other Memory Regions",
             '  {"XNU Payload",       0xF0000000, 0x04000000, AddMem, SYS_MEM, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},\n\n'
             "  // Other Memory Regions")
dsc = MU / "Platforms/OnePlus/lemonadePkg/lemonade.dsc"
replace_once(dsc, "[PcdsFixedAtBuild]",
             "[BuildOptions]\n  *_*_AARCH64_CC_FLAGS = -march=armv8.4-a+crypto+rcpc -DXNU_NO_PERSISTENT_LOGS=1\n\n[PcdsFixedAtBuild]")
replace_once(MU / "Platforms/OnePlus/lemonadePkg/DeviceBuild.py",
             'RequiredSubmodule ("Mu_Basecore", True)',
             'RequiredSubmodule ("Mu_Basecore", False)')
# Populate the actual target dependencies separately (including Brotli, libfdt,
# oniguruma and mipisyst). Recursive libspdm unit-test dependencies are not used
# by this lemonade FDF and otherwise pull full OpenSSL/mbedTLS histories.

private = ROOT / "local/lemonade"
dtb = private / "device.dtb"
if not dtb.exists():
    raise SystemExit("First collect the phone's read-only baseline")
boot_dtb = private / "boot.dtb"
shutil.copyfile(dtb, boot_dtb)
subprocess.run(["fdtput", "-r", str(boot_dtb), "/chosen"], check=True)
# The source contains __symbols__ paths to /chosen on some firmware versions;
# symbols are unnecessary at boot, and the result must contain no runtime identity.
nodes = subprocess.check_output(["fdtget", "-l", str(boot_dtb), "/"], text=True).splitlines()
if "__symbols__" in nodes:
    subprocess.run(["fdtput", "-r", str(boot_dtb), "/__symbols__"], check=True)
print("Prepared lemonade UEFI hooks, reserved XNUP RAM, disabled stage-0 disk logs, sanitized boot DTB.")
