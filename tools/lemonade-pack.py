#!/usr/bin/env python3
"""Build and inspect a loader-only Android boot v3 image for the collected phone."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import zlib

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
baseline = json.loads((ROOT / "local/lemonade/baseline.json").read_text())
usb_serial = "lemonade-" + baseline["hardware_serial_sha256"][:16]
env = dict(os.environ, SKIP="kernel images", LOADER_USB_SERIAL=usb_serial)
if not shutil.which("llvm-objcopy") and Path("/opt/homebrew/opt/llvm/bin/llvm-objcopy").exists():
    env["PATH"] = "/opt/homebrew/opt/llvm/bin:" + env["PATH"]
subprocess.run(["bash", "common/build.sh", "lemonade"], env=env, check=True)

image = ROOT / "out/lemonade-boot.img"
data = image.read_bytes()
if data[:8] != b"ANDROID!" or struct.unpack_from("<I", data, 40)[0] != 3:
    raise SystemExit("Invalid Android boot v3 header")
kernel_size, ramdisk_size = struct.unpack_from("<II", data, 8)
compressed = data[4096:4096 + kernel_size]
decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
payload = decoder.decompress(compressed) + decoder.flush()
boot_dtb = (ROOT / "local/lemonade/boot.dtb").read_bytes()
if decoder.unused_data != boot_dtb:
    raise SystemExit("Packed boot DTB differs from the sanitized device tree")
shim = (ROOT / "Mu-Silicium/BootShim/BootShim.bin").read_bytes()
fd = (ROOT / "Mu-Silicium/Build/lemonadePkg/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd").read_bytes()
if payload[:len(shim)] != shim or payload[len(shim):len(shim) + len(fd)] != fd:
    raise SystemExit("Boot image does not contain the freshly built shim and lemonade UEFI")
container = payload[len(shim) + len(fd):]
if container[:4] != b"XNUP":
    raise SystemExit("Missing XNUP container")
body_size = struct.unpack_from("<I", container, 4)[0]
if 8 + body_size > len(container):
    raise SystemExit("Truncated XNUP container")
entries = {}
pos = 8
while pos + 24 <= body_size + 8:
    name, length, reserved = struct.unpack_from("<16sII", container, pos)
    pos += 24
    name = name.split(b"\0", 1)[0].decode()
    if not name:
        break
    if pos + length > body_size + 8:
        raise SystemExit("Truncated XNUP entry")
    entries[name] = container[pos:pos + length]
    pos += (length + 15) & ~15
if set(entries) != {"loader.efi", "usb"} or entries["usb"] != b"1\0":
    raise SystemExit("Image must contain only the loader and USB mode, not a kernel or installer")
if entries["loader.efi"] != (ROOT / "xnuloader/loader.efi").read_bytes():
    raise SystemExit("Packed EFI loader differs from the freshly built loader")
if usb_serial.encode() not in entries["loader.efi"]:
    raise SystemExit("Per-device loader USB identity is missing")
manifest = {
    "device": "lemonade", "stage": "loader-only", "hardware_boot_tested": False,
    "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "header_version": 3,
    "kernel_bytes": kernel_size, "ramdisk_bytes": ramdisk_size,
    "loader_usb_serial": usb_serial, "uefi_sha256": hashlib.sha256(fd).hexdigest(),
    "loader_sha256": hashlib.sha256(entries["loader.efi"]).hexdigest(),
    "battery_telemetry_supported": "bq28z610-uefi-cache-v2",
    "dtb_sha256": hashlib.sha256(boot_dtb).hexdigest(),
    "kernel_source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "uefi_source_commit": (ROOT / "uefi/BASE").read_text().strip(),
}
(ROOT / "out/lemonade-boot.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(f"Validated loader-only v3 image: {len(data)} bytes, SHA-256 {manifest['sha256']}")
# The upstream return-path test still parses and relocates a kernel, although it
# jumps only to the loader's return stub. Keep this separate from the boot image.
kernel_path = ROOT / "prebuilt/mach.development.bcm2837"
kernel_hash = hashlib.sha256(kernel_path.read_bytes()).hexdigest()
expected_hash = next(line.split()[0] for line in (ROOT / "prebuilt/SHA256SUMS").read_text().splitlines()
                     if line.split() and line.split()[-1] == "prebuilt/mach.development.bcm2837")
if kernel_hash != expected_hash:
    raise SystemExit("Prebuilt kernel checksum mismatch")
cmdline = subprocess.check_output(["bash", "-c",
    '. common/devices/lemonade.conf; printf "%s" "$BOOTARGS"'], text=True)
selftest = ROOT / "out/lemonade-selftest.bin"
subprocess.run(["python3", "xnuloader/mkpayload.py", "-o", str(selftest),
                f"kernel={kernel_path}", "--cmdline", cmdline, "--checkpoint", "999"], check=True)
test_data = selftest.read_bytes()
if len(test_data) > 0x03F00000:
    raise SystemExit("Selftest payload exceeds the loader's USB limit")
(ROOT / "out/lemonade-selftest.manifest.json").write_text(json.dumps({
    "device": "lemonade", "stage": "return-selftest", "hardware_boot_tested": False,
    "loader_usb_serial": usb_serial, "bytes": len(test_data),
    "sha256": hashlib.sha256(test_data).hexdigest(), "kernel_sha256": kernel_hash,
    "cmdline": cmdline, "checkpoint": 999,
}, indent=2) + "\n")
print("Hardware boot test is still required; no device was modified by this build.")
