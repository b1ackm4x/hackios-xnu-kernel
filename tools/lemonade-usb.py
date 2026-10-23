#!/usr/bin/env python3
"""Exact-device, temporary loader bring-up. No flash/erase/slot-changing commands.

The device is selected by the hardware serial hash in the private baseline,
never by the first Android device in a list. Acquire its Device Hub lease first.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]


def command(argv, timeout=15, check=True):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=check)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "boot", "log", "memmap", "selftest", "run", "ramdisk", "screenshot", "reboot"))
    parser.add_argument("--checkpoint", type=int, default=0, help="XNU bring-up checkpoint; 0 runs normally")
    parser.add_argument("--seconds", type=int, help="XNU timed return, 1..300 seconds; up to 900 for performance + physical touch; default 8 or 300 with --profile")
    parser.add_argument("--userland", action="store_true", help="include root.apfs; upload system.apfs with ramdisk first")
    parser.add_argument("--kernel", choices=("prebuilt", "built"),
                        help="use the upstream kernel or the locally built, manifest-verified lemonade kernel")
    parser.add_argument("--gpu", action="store_true", help="enable the adapted Adreno 660 for rendering (requires --kernel built)")
    parser.add_argument("--gpu-step", type=int, default=0, help="run Adreno bring-up through step 1..22, then return")
    parser.add_argument("--boot-cpu", type=int, choices=range(8), help="physical boot core, 0..7; 7 is the SM8350 prime core")
    parser.add_argument("--cpus", type=int, choices=range(1, 9), help="maximum XNU core count; a resident loader core is excluded")
    parser.add_argument("--metrics", action="store_true", help="capture per-frame timing, input and CPU load for performance analysis")
    parser.add_argument("--touch", choices=("inspect", "on"), help="Samsung S6SY792 touch inspection or input (adapted kernel only)")
    parser.add_argument("--wifi", choices=("inspect", "rails", "domain", "clocks", "resets", "controller", "phy", "root", "radio", "link", "endpoint", "mmio", "smmu"), help="bounded PCIe bring-up stages; none enables networking")
    parser.add_argument("--cpu-policy", choices=("firmware", "inspect", "balanced"),
                        help="SM8350 EPSS policy: inspect only, or capped stock OPPs with restore on return")
    parser.add_argument("--profile", choices=("performance",),
                        help="bounded desktop session: adapted kernel, four big cores, capped CPU policy and GPU")
    args = parser.parse_args()
    if args.profile:
        if args.action != "run" or args.checkpoint or args.gpu_step:
            parser.error("Performance profile requires a normal run, without a checkpoint or GPU step")
        for key, value in (("kernel", "built"), ("boot_cpu", 7), ("cpus", 4), ("cpu_policy", "balanced")):
            if getattr(args, key) is not None and getattr(args, key) != value:
                parser.error(f"--{key.replace('_', '-')} conflicts with the performance profile")
            setattr(args, key, value)
        args.gpu = args.userland = True
    args.kernel = args.kernel or "prebuilt"
    args.cpu_policy = args.cpu_policy or "firmware"
    if args.seconds is None:
        args.seconds = 300 if args.profile else 8
    if (args.boot_cpu is not None or args.cpus is not None) and (args.action != "run" or args.kernel != "built"):
        parser.error("CPU topology tests require run --kernel built")
    if args.metrics and (args.action != "run" or args.kernel != "built"):
        parser.error("Performance metrics require run --kernel built")
    if args.touch and (args.action != "run" or args.kernel != "built" or args.checkpoint or args.gpu_step):
        parser.error("Touch requires run --kernel built without a checkpoint or GPU step")
    if args.wifi and (args.action != "run" or args.kernel != "built" or args.checkpoint or args.gpu_step):
        parser.error("Wi-Fi requires run --kernel built without a checkpoint or GPU step")
    if args.wifi and args.wifi != "inspect" and (args.touch or args.gpu or args.cpu_policy != "firmware"):
        parser.error("Wi-Fi root-power probe must run without touch, GPU or CPU-policy changes")
    if args.cpu_policy != "firmware" and (args.action != "run" or args.kernel != "built"):
        parser.error("CPU policy requires run --kernel built")
    session_limit = 900 if args.profile == "performance" and args.touch == "on" else 300
    if not 0 <= args.checkpoint <= 44 or not 1 <= args.seconds <= session_limit:
        parser.error(f"checkpoint must be 0..44 and seconds must be 1..{session_limit}")
    if not 0 <= args.gpu_step <= 22 or ((args.gpu or args.gpu_step) and
            (args.action != "run" or args.kernel != "built" or args.checkpoint)) or (args.gpu and args.gpu_step):
        parser.error("GPU work requires run --kernel built, no XNU checkpoint, and either --gpu or --gpu-step 1..22")
    baseline = json.loads((ROOT / "local/lemonade/baseline.json").read_text())
    expected = baseline["hardware_serial_sha256"]
    loader_serial = "lemonade-" + expected[:16]

    def matches(serial):
        return hashlib.sha256(serial.encode()).hexdigest() == expected

    def usb_adb():
        for line in command(["adb", "devices", "-l"]).stdout.splitlines()[1:]:
            cells = line.split()
            # Recent libusb-backed adb prints a bare topology path (2-1.1),
            # while other backends print usb:<path>. Neither occurs on TCP/TLS.
            physical = len(cells) >= 3 and (cells[2].startswith("usb:") or
                                          re.fullmatch(r"\d+-\d+(?:\.\d+)*", cells[2]))
            if physical and cells[1] == "device" and matches(cells[0]):
                return cells[0]
        return None

    def usb_fastboot():
        for line in command(["fastboot", "devices"]).stdout.splitlines():
            cells = line.split()
            if len(cells) >= 2 and cells[1] == "fastboot" and (matches(cells[0]) or cells[0] == loader_serial):
                return cells[0]
        return None

    def fb(serial, *parts, timeout=15):
        return command(["fastboot", "-s", serial, *parts], timeout=timeout)

    def product(serial):
        r = fb(serial, "getvar", "product")
        for line in (r.stdout + r.stderr).splitlines():
            if "product:" in line:
                return line.split("product:", 1)[1].strip()
        return "unknown"

    serial = usb_fastboot()
    android_serial = usb_adb()
    if args.action == "status":
        print(json.dumps({"usb_adb": bool(android_serial), "usb_fastboot": bool(serial),
                          "fastboot_product": product(serial) if serial else None,
                          "identity": "baseline SHA-256 match required"}, indent=2))
        return

    if args.action == "boot":
        image = ROOT / "out/lemonade-boot.img"
        manifest = json.loads((ROOT / "out/lemonade-boot.manifest.json").read_text())
        if manifest.get("device") != "lemonade" or manifest.get("stage") != "loader-only":
            raise SystemExit("Refusing: no verified lemonade loader-only artifact")
        if manifest.get("loader_usb_serial") != loader_serial:
            raise SystemExit("Refusing: boot image was built for a different device identity")
        raw = image.read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
            raise SystemExit("Refusing: boot image checksum differs from its build manifest")
        if raw[:8] != b"ANDROID!" or struct.unpack_from("<I", raw, 40)[0] != 3:
            raise SystemExit("Refusing: boot image is not Android header v3")
        if not serial and android_serial:
            for key, value in (("ro.product.model", "LE2110"), ("ro.soc.model", "SM8350"),
                               ("ro.boot.flash.locked", "0")):
                result = command(["adb", "-s", android_serial, "shell", "getprop", key]).stdout.strip()
                if result != value:
                    raise SystemExit(f"Refusing: unexpected {key}")
            print("Verified OnePlus 9 USB identity; entering bootloader.", flush=True)
            command(["adb", "-s", android_serial, "reboot", "bootloader"])
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not serial:
                time.sleep(1)
                serial = usb_fastboot()
        if not serial:
            raise SystemExit("OnePlus 9 is not connected by USB; no boot command sent")
        detected = product(serial)
        # LE2110's stock bootloader reports the SoC name lahaina. The hardware
        # serial must already match this phone's collected baseline above.
        if detected not in ("lemonade", "OnePlus9", "LE2110", "lahaina"):
            raise SystemExit(f"Refusing boot: unverified fastboot product {detected!r}")
        print("Booting the verified loader image into RAM.", flush=True)
        r = fb(serial, "boot", str(image), timeout=60)
        print(r.stdout + r.stderr)
        return

    if not serial or product(serial) != "xnuloader":
        raise SystemExit("The baseline device is not running xnuloader over USB")
    if args.action == "reboot":
        fb(serial, "reboot")
        print("Loader reboot requested; verify Android boot completion separately.")
        return
    if args.action == "screenshot":
        r = fb(serial, "getvar", "screen")
        geometry = next(line.split("screen:", 1)[1].strip() for line in (r.stdout + r.stderr).splitlines()
                        if "screen:" in line)
        if len(geometry.split()) != 3:
            raise SystemExit("Loader did not return framebuffer dimensions; inspect its log/reply buffer")
        width, height, stride = map(int, geometry.split())
        if not (0 < width <= 4096 and 0 < height <= 4096 and width * 4 <= stride <= 32768):
            raise SystemExit("Unexpected framebuffer geometry")
        base = ROOT / "local/lemonade" / f"{int(time.time())}-screen"
        raw_path, png_path = base.with_suffix(".raw"), base.with_suffix(".png")
        fb(serial, "oem", "screenshot")
        fb(serial, "get_staged", str(raw_path), timeout=60)
        raw = raw_path.read_bytes()
        if len(raw) != height * stride:
            raise SystemExit("Truncated framebuffer readback")
        rows = bytearray()
        for y in range(height):
            line = raw[y * stride:y * stride + width * 4]
            rgb = bytearray(width * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = line[2::4], line[1::4], line[0::4]
            rows += b"\0" + rgb
        def png_chunk(kind, body):
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        png_path.write_bytes(b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                             + png_chunk(b"IDAT", zlib.compress(rows, 6)) + png_chunk(b"IEND", b""))
        png_path.chmod(0o600)
        raw_path.unlink()
        print(f"Saved {width}x{height} framebuffer: {png_path}")
        return
    def verified_image(relative):
        info = json.loads((ROOT / "local/lemonade/images.json").read_text())[relative]
        path = ROOT / relative
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
        if path.stat().st_size != info["bytes"] or digest.hexdigest() != info["sha256"]:
            raise SystemExit(f"Refusing: image differs from its checked manifest: {relative}")
        return path

    if args.action == "ramdisk":
        system_image = verified_image("userland/system.apfs")
        print(f"Uploading {system_image.stat().st_size} bytes to resident RAM.", flush=True)
        fb(serial, "oem", "ramdisk", timeout=30)
        r = fb(serial, "stage", str(system_image), timeout=300)
        print(r.stdout + r.stderr)
        print("Resident RAM upload finished; it must be repeated after a loader reboot.")
        return
    result = None
    if args.action == "run":
        if args.kernel == "built":
            kernel = ROOT / "out/lemonade-kernel"
            kernel_manifest = json.loads((ROOT / "out/lemonade-kernel.manifest.json").read_text())
            if kernel_manifest.get("device") != "lemonade" or kernel_manifest.get("stage") != "xnu-kernel":
                raise SystemExit("Refusing: no verified lemonade kernel build")
            if args.cpu_policy != "firmware" and kernel_manifest.get("cpu_policy_supported") != "sm8350-capped-epss-v1":
                raise SystemExit("Refusing: kernel has no verified SM8350 CPU policy support")
            if args.touch and kernel_manifest.get("touch_driver_supported") != "lemonade-s6sy792-v1":
                raise SystemExit("Refusing: kernel has no lemonade S6SY792 touch support")
            if args.wifi and args.wifi not in kernel_manifest.get("wifi_modes_supported", []):
                raise SystemExit("Refusing: kernel has no requested lemonade Wi-Fi mode")
            expected_kernel = kernel_manifest["sha256"]
        else:
            kernel = ROOT / "prebuilt/mach.development.bcm2837"
            expected_kernel = next(line.split()[0] for line in (ROOT / "prebuilt/SHA256SUMS").read_text().splitlines()
                                   if line.split() and line.split()[-1] == "prebuilt/mach.development.bcm2837")
        if hashlib.sha256(kernel.read_bytes()).hexdigest() != expected_kernel:
            raise SystemExit("Refusing: kernel checksum mismatch")
        cmdline = command(["bash", "-c", 'cd "$1"; . common/devices/lemonade.conf; printf "%s" "$BOOTARGS"',
                           "lemonade", str(ROOT)]).stdout
        if args.userland:
            cmdline = " ".join(arg for arg in cmdline.split() if arg != "-v")
        for key, value in (("bootcpu", args.boot_cpu), ("cpus", args.cpus)):
            if value is not None:
                cmdline = " ".join(arg for arg in cmdline.split() if not arg.startswith(key + "="))
                cmdline += f" {key}={value}"
        if args.metrics:
            cmdline += " touch_lat=2 cpuload=500"
        if args.touch:
            cmdline = " ".join(arg for arg in cmdline.split() if not arg.startswith("touchscreen="))
            cmdline += " touchscreen=" + ("2" if args.touch == "inspect" else "1")
        if args.wifi:
            cmdline = " ".join(arg for arg in cmdline.split() if not arg.startswith(("wifi=", "wifi_step=", "lemonade_wifi=")))
            cmdline += " wifi=0 lemonade_wifi=" + str({"inspect": 1, "rails": 2, "domain": 3, "clocks": 4, "resets": 5, "controller": 6, "phy": 7, "root": 8, "radio": 9, "link": 10, "endpoint": 11, "mmio": 12, "smmu": 13}[args.wifi])
        if args.cpu_policy != "firmware":
            cmdline = " ".join(arg for arg in cmdline.split() if not arg.startswith("cpufreq="))
            cmdline += " cpufreq=" + ("2" if args.cpu_policy == "inspect" else "1") + " ddr=0"
        if args.gpu or args.gpu_step:
            cmdline = " ".join(arg for arg in cmdline.split() if not arg.startswith("gpu="))
            if args.gpu:
                cmdline += " gpu=1 gpu_log=1"
            else:
                cmdline += f" gpu=0 adreno={args.gpu_step} adreno_pause=0 adreno_watch=1 adreno_return=1"
        cmdline = f"loader_return_after={args.seconds} epoch={int(time.time())} sb_delay=8 " + cmdline
        payload = ROOT / "out/lemonade-xnu.bin"
        extra_images = [f"ramdisk={verified_image('userland/root.apfs')}"] if args.userland or args.gpu or args.gpu_step else []
        command(["python3", str(ROOT / "xnuloader/mkpayload.py"), "-o", str(payload),
                 f"kernel={kernel}", *extra_images, "--cmdline", cmdline], timeout=30)
        # The optional root disk and resident system disk both live in RAM.
        # Peripheral and flash access remains disabled by the lemonade profile.
        record = {"device": "lemonade", "stage": "xnu-checkpoint", "checkpoint": args.checkpoint,
                  "seconds": args.seconds, "userland": args.userland, "cmdline": cmdline, "kernel_sha256": expected_kernel,
                  "payload_sha256": hashlib.sha256(payload.read_bytes()).hexdigest()}
        record.update({"kernel_source": args.kernel, "gpu_enabled": args.gpu, "gpu_step": args.gpu_step})
        record.update({"boot_cpu": args.boot_cpu, "cpus_requested": args.cpus})
        record["performance_metrics"] = args.metrics
        record["metrics_mode"] = "frame-input-present-v2" if args.metrics else None
        record["cpu_policy"] = args.cpu_policy
        record["profile"] = args.profile
        record["touch"] = args.touch
        record["wifi"] = args.wifi
        record["live_logs"] = []
        if args.userland or args.gpu or args.gpu_step:
            image_manifest = json.loads((ROOT / "local/lemonade/images.json").read_text())
            record["images"] = {name: image_manifest[name] for name in
                                ("userland/root.apfs", "userland/system.apfs")}
        (ROOT / "out/lemonade-xnu.manifest.json").write_text(json.dumps(record, indent=2) + "\n")
        fb(serial, "stage", str(payload), timeout=60)
    if args.action == "selftest":
        payload = ROOT / "out/lemonade-selftest.bin"
        manifest = json.loads((ROOT / "out/lemonade-selftest.manifest.json").read_text())
        if (manifest.get("device") != "lemonade" or manifest.get("stage") != "return-selftest"
                or manifest.get("checkpoint") != 999 or manifest.get("loader_usb_serial") != loader_serial):
            raise SystemExit("Refusing: no verified return-path selftest for this device")
        if hashlib.sha256(payload.read_bytes()).hexdigest() != manifest["sha256"]:
            raise SystemExit("Refusing: selftest checksum differs from its build manifest")
        fb(serial, "stage", str(payload), timeout=60)
    operation = ["oem", args.action]
    if args.action == "run":
        operation.append(str(args.checkpoint))
    r = fb(serial, *operation, timeout=30)
    print(r.stdout + r.stderr)
    if args.action in ("selftest", "run"):
        # The single-core kernel temporarily stops servicing UEFI USB. Holding
        # a macOS fastboot read open across a long run can leave a stale pipe,
        # even after XNU has returned. Open the query after its timed return.
        if args.action == "run" and args.checkpoint == 0:
            remaining = args.seconds + 8
            print(f"Waiting {remaining}s for XNU to return before reopening the USB result query.", flush=True)
            while remaining:
                delay = min(10, remaining)
                time.sleep(delay)
                remaining -= delay
                if args.metrics and args.boot_cpu == 7 and remaining > 8:
                    # The loader remains on core 0. Preserve the rolling kernel
                    # trace before a busy animation can overwrite its 1 MiB ring.
                    live_path = ROOT / "local/lemonade" / f"{int(time.time())}-perf-live.log"
                    fb(serial, "oem", "log", timeout=5)
                    fb(serial, "get_staged", str(live_path), timeout=10)
                    record["live_logs"].append({"path": str(live_path.relative_to(ROOT)),
                                               "sha256": hashlib.sha256(live_path.read_bytes()).hexdigest()})
        for attempt in range(2):
            try:
                r = fb(serial, "getvar", "result", timeout=20)
                break
            except subprocess.TimeoutExpired:
                if attempt:
                    raise
                time.sleep(2)
        result = r.stdout + r.stderr
        print(result)
    if args.action == "memmap":
        fb(serial, "oem", "log", timeout=30)
    if args.action in ("log", "memmap", "selftest", "run"):
        label = args.action if args.action != "run" else f"run-cp{args.checkpoint}"
        dest = ROOT / "local/lemonade" / f"{int(time.time())}-{label}.log"
        fb(serial, "get_staged", str(dest), timeout=30)
        print(f"Saved private device evidence: {dest}")
        if args.action == "run":
            record.update({"result": result, "log_sha256": hashlib.sha256(dest.read_bytes()).hexdigest()})
            dest.with_suffix(".manifest.json").write_text(json.dumps(record, indent=2) + "\n")
            if args.gpu_step and f"adreno: step {args.gpu_step}: done" not in dest.read_text(errors="replace"):
                raise SystemExit(f"Adreno step {args.gpu_step} did not pass; inspect the saved device log")
            if args.gpu and "adreno: THE GPU STAYS ON" not in dest.read_text(errors="replace"):
                raise SystemExit("Adreno did not start for rendering; inspect the saved device log")
    if args.action == "selftest" and result is not None and "result: selftest-ok " not in result:
        raise SystemExit("Return-path selftest did not pass; inspect the saved device log")
    if args.action == "run" and args.checkpoint and f"result: checkpoint {args.checkpoint} " not in result:
        raise SystemExit("XNU did not reach the requested checkpoint; inspect the saved device log")


if __name__ == "__main__":
    try:
        main()
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"USB command timed out after {exc.timeout}s; check status and collect the log before inferring a kernel hang")
