#!/usr/bin/env python3
"""Read-only OnePlus 9 baseline and verified boot-chain backup.

Acquire the Android Device Hub lease before using this script. Private output
lives below the repository-ignored local/lemonade/ directory with mode 0700.
No device files, partitions, settings or services are changed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[3]
PROPERTIES = (
    "ro.product.manufacturer", "ro.product.model", "ro.product.device",
    "ro.board.platform", "ro.soc.model", "ro.boot.slot_suffix",
    "ro.boot.flash.locked", "ro.boot.verifiedbootstate",
    "ro.build.version.release", "ro.build.version.security_patch",
    "ro.build.display.id", "sys.boot_completed",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True, help="Explicit ADB transport")
    parser.add_argument("--backup", action="store_true", help="Read both slots' boot-chain images")
    args = parser.parse_args()
    os.umask(0o077)

    def adb(command, *, root=False, timeout=30):
        invocation = ["adb", "-s", args.serial, "exec-out"]
        # exec-out preserves argv; unlike adb shell it must not be shell-quoted again.
        invocation += ["su", "-c", command] if root else ["sh", "-c", command]
        return subprocess.check_output(invocation, timeout=timeout)

    props = {key: adb("getprop " + key).decode().strip() for key in PROPERTIES}
    if (props["ro.product.manufacturer"], props["ro.product.model"], props["ro.soc.model"]) != ("OnePlus", "LE2110", "SM8350"):
        raise SystemExit("Refusing: expected OnePlus LE2110 / SM8350")
    if not adb("id", root=True).startswith(b"uid=0("):
        raise SystemExit("Root access is required to read device tree and boot images")
    serial = adb("getprop ro.serialno").decode().strip()
    if not serial:
        raise SystemExit("Cannot establish hardware identity")
    props["hardware_serial_sha256"] = hashlib.sha256(serial.encode()).hexdigest()
    out = ROOT / "local/lemonade"
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o700)
    (out / "baseline.json").write_text(json.dumps(props, indent=2) + "\n")
    readouts = {
        "input-devices.txt": "cat /proc/bus/input/devices",
        "memory.txt": "head -n 3 /proc/meminfo",
        "kernel.txt": "uname -a",
        "partitions.txt": "ls -l /dev/block/by-name",
        "battery.txt": "cat /sys/class/power_supply/battery/capacity",
    }
    for name, command in readouts.items():
        (out / name).write_bytes(adb(command, root=True))

    manifest_path = out / "sha256.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    sources = {"device.dtb": "/sys/firmware/fdt"}
    if args.backup:
        sources.update({f"{p}_{slot}.img": f"/dev/block/by-name/{p}_{slot}"
                        for slot in ("a", "b")
                        for p in ("boot", "vendor_boot", "dtbo", "vbmeta", "vbmeta_system", "vbmeta_vendor", "xbl", "xbl_config")})
    for filename, source in sources.items():
        dest = out / filename
        remote_sha = adb("sha256sum " + shlex.quote(source), root=True, timeout=180).decode().split()[0]
        if dest.exists():
            with dest.open("rb") as stream:
                local_sha = hashlib.file_digest(stream, "sha256").hexdigest()
            if local_sha != remote_sha:
                raise SystemExit(f"Refusing to overwrite different backup: {filename}")
        else:
            temporary = dest.with_suffix(dest.suffix + ".partial")
            with temporary.open("wb") as stream:
                subprocess.run(["adb", "-s", args.serial, "exec-out", "su", "-c",
                                "cat " + shlex.quote(source)],
                               stdout=stream, check=True, timeout=240)
            with temporary.open("rb") as stream:
                local_sha = hashlib.file_digest(stream, "sha256").hexdigest()
            if local_sha != remote_sha:
                raise SystemExit(f"Checksum mismatch: {filename}; partial file retained")
            temporary.rename(dest)
        manifest[filename] = {"bytes": dest.stat().st_size, "sha256": remote_sha,
                              "device_hash_verified": True}
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"verified {filename}: {dest.stat().st_size} bytes sha256={remote_sha}", flush=True)
    print(f"Read-only baseline complete: {out}")


if __name__ == "__main__":
    main()
