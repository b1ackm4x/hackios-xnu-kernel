#!/usr/bin/env python3
"""Exact-device, temporary loader bring-up. No flash/erase/slot-changing commands.

The device is selected by the hardware serial hash in the private baseline,
never by the first Android device in a list. Acquire its Device Hub lease first.
"""
import argparse
import atexit
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]


def command(argv, timeout=15, check=True):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=check)


def verified_firmware_overlay(kernel_hash):
    """Require a real guard fault from this kernel before a firmware download."""
    guard_path = ROOT / 'local/lemonade/wifi/dma-guard-verified.json'
    if not guard_path.exists():
        raise SystemExit('Refusing: no verified endpoint DMA guard test')
    guard = json.loads(guard_path.read_text())
    if guard.get('kernel_sha256') != kernel_hash:
        raise SystemExit('Refusing: DMA guard test used a different kernel')
    log = (ROOT / guard['log']).resolve()
    if not log.is_relative_to((ROOT / 'local/lemonade').resolve()):
        raise SystemExit('Refusing: DMA evidence is outside the private device directory')
    record = json.loads(log.with_suffix('.manifest.json').read_text())
    spec = importlib.util.spec_from_file_location('lemonade_wifi_check', Path(__file__).with_name('lemonade-check-wifi.py'))
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    evidence = checker.check(record, log.read_bytes())
    if (record.get('kernel_sha256') != kernel_hash or record.get('log_sha256') != guard.get('log_sha256') or
            not evidence['passed'] or evidence['mode'] != 'dma-fault'):
        raise SystemExit('Refusing: endpoint DMA guard evidence failed revalidation')
    image = ROOT / 'out/lemonade-wifi/root.apfs'
    manifest = json.loads(image.with_name('manifest.json').read_text())
    normal = json.loads((ROOT / 'local/lemonade/images.json').read_text())['userland/root.apfs']['sha256']
    if (manifest.get('device') != 'lemonade' or manifest.get('base_root_sha256') != normal or
            not manifest.get('other_file_contents_and_modes_verified') or
            manifest.get('sha256') != hashlib.sha256(image.read_bytes()).hexdigest() or
            manifest.get('firmware', {}).get('amss.bin', {}).get('sha256') !=
            '71e701a8cd8b5d37da939e5eccc4f7786c1f031eb92084b6a616bc0f6c1ed521'):
        raise SystemExit('Refusing: private firmware overlay differs from its verified manifest')
    return image, manifest, guard


def verified_graphics_overlay(system=False):
    folder = ROOT / ('out/lemonade-metal-system' if system else 'out/lemonade-metal')
    profile = 'metal-system-v1' if system else 'metal-baseline-v1'
    jobs = 'common/devices/lemonade-metal-system.rc.jobs' if system else 'common/devices/lemonade-metal.rc.jobs'
    image = folder / 'root.apfs'
    record = json.loads((folder / 'manifest.json').read_text())
    normal = json.loads((ROOT / 'local/lemonade/images.json').read_text())['userland/root.apfs']['sha256']
    def digest(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    if (record.get('device') != 'lemonade' or record.get('profile') != profile or
            record.get('base_root_sha256') != normal or record.get('network_private') is not False or
            record.get('sha256') != digest(image) or not record.get('normal_root_unchanged') or
            not record.get('other_file_contents_and_modes_verified') or
            record.get('build_receipt_sha256') != digest(folder / 'build.json') or
            record.get('jobs_sha256') != digest(ROOT / jobs)):
        raise SystemExit('Refusing: graphics overlay differs from its verified build')
    build = json.loads((folder / 'build.json').read_text())
    for relative, expected in build['sources'].items():
        if digest(ROOT / relative) != expected:
            raise SystemExit('Refusing: Metal sources changed after image build: ' + relative)
    return image, record


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "boot", "log", "memmap", "selftest", "run", "ramdisk", "screenshot", "reboot"))
    parser.add_argument("--checkpoint", type=int, default=0, help="XNU bring-up checkpoint; 0 runs normally")
    parser.add_argument("--seconds", type=int, help="XNU timed return, 1..300 seconds; up to 900 for performance + physical touch; default 8 or 300 with --profile")
    parser.add_argument("--userland", action="store_true", help="include root.apfs; upload system.apfs with ramdisk first")
    parser.add_argument("--kernel", choices=("prebuilt", "built"),
                        help="use the upstream kernel or the locally built, manifest-verified lemonade kernel")
    parser.add_argument("--gpu", action="store_true", help="enable the adapted Adreno 660 for rendering (requires --kernel built)")
    parser.add_argument("--gpu-step", type=int, default=0, help="run Adreno bring-up through step 1..22, then return")
    parser.add_argument('--gpu-clock', type=int, choices=(315, 379, 443, 540), help='explicit SM8350 stock GPU OPP in MHz; default remains 315')
    parser.add_argument("--boot-cpu", type=int, choices=range(8), help="physical boot core, 0..7; 7 is the SM8350 prime core")
    parser.add_argument("--cpus", type=int, choices=range(1, 9), help="maximum XNU core count; a resident loader core is excluded")
    parser.add_argument("--metrics", action="store_true", help="capture per-frame timing, input and CPU load for performance analysis")
    parser.add_argument("--gpu-trace", action="store_true", help="log every GPU submission; diagnostic only, can exhaust the device log")
    parser.add_argument('--network-test', action='store_true', help='explicit RAM-only association/DHCP/traffic acceptance with a private current-network overlay')
    parser.add_argument('--metal-test', action='store_true', help='verified RAM-only Metal pixel and IOSurface diagnostic overlay')
    parser.add_argument('--metal-system', choices=('apps', 'desktop'), help='verified RAM-only system Metal discovery; desktop also selects Metal compositing')
    parser.add_argument('--metal-profile', type=int, choices=(0, 1), help='experimental system capability table: 0 Apple1, 1 Apple2 (tested system default); does not imply complete family compliance')
    parser.add_argument('--display', choices=('inspect', 'clocks', 'irq', 'sync', 'smmu', 'map', 'flip'), help='SM8350 display; smmu reads mappings; map identity-maps GOP; flip tests two owned buffers; both restore firmware state')
    parser.add_argument('--display-flips', type=int, choices=(64, 1000), help='bounded dual-buffer test count, only with --display flip; default 64')
    parser.add_argument("--touch", choices=("inspect", "on"), help="Samsung S6SY792 touch inspection or input (adapted kernel only)")
    parser.add_argument("--wifi", choices=("inspect", "rails", "domain", "clocks", "resets", "controller", "phy", "root", "radio", "link", "endpoint", "mmio", "smmu", "sid", "dma", "dma-fault", "bhi", "mhi", "bhie", "ipcr", "qmi", "driver", "scan", "station"), help="bounded Wi-Fi stages; station exposes the iOS interface without choosing a network")
    parser.add_argument("--cpu-policy", choices=("firmware", "inspect", "balanced"),
                        help="SM8350 EPSS policy: inspect only, or capped stock OPPs with restore on return")
    parser.add_argument("--profile", choices=("performance",),
                        help="bounded desktop session: adapted kernel, four big cores, capped CPU policy and GPU")
    args = parser.parse_args()
    if args.network_test and (args.action != 'run' or args.wifi != 'station' or not args.userland):
        parser.error('--network-test requires run --wifi station --userland')
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
        args.seconds = 300 if args.profile else (60 if args.display_flips == 1000 else 30 if args.display in ('map', 'flip') else 8)
    if (args.boot_cpu is not None or args.cpus is not None) and (args.action != "run" or args.kernel != "built"):
        parser.error("CPU topology tests require run --kernel built")
    if args.gpu_trace and (args.action != "run" or not args.gpu):
        parser.error("GPU submission tracing requires a GPU run")
    if args.gpu_clock is not None and (args.action != 'run' or args.kernel != 'built' or
            args.checkpoint or not (args.gpu or args.gpu_step >= 17)):
        parser.error('GPU clock selection requires a built GPU run or GPU step 17..22')
    if args.metrics and (args.action != "run" or args.kernel != "built"):
        parser.error("Performance metrics require run --kernel built")
    if args.touch and (args.action != "run" or args.kernel != "built" or args.checkpoint or args.gpu_step):
        parser.error("Touch requires run --kernel built without a checkpoint or GPU step")
    if args.wifi and (args.action != "run" or args.kernel != "built" or args.checkpoint or args.gpu_step):
        parser.error("Wi-Fi requires run --kernel built without a checkpoint or GPU step")
    if args.display and (args.action != 'run' or args.kernel != 'built' or args.checkpoint or args.gpu_step or args.wifi):
        parser.error('Display survey requires run --kernel built without checkpoints, GPU steps or Wi-Fi tests')
    if args.display in ('map', 'flip') and (not args.userland or args.gpu or args.touch or args.cpu_policy != 'firmware' or
                                          args.cpus not in (None, 1) or args.boot_cpu not in (None, 0)):
        parser.error('Display mapping probe requires --userland on boot CPU 0 only, without GPU, touch or CPU-policy changes')
    if args.display_flips is not None and args.display != 'flip':
        parser.error('--display-flips requires --display flip')
    if args.display_flips == 1000 and args.seconds < 50:
        parser.error('1000 display flips require at least 50 seconds')
    if args.metal_test and args.metal_system:
        parser.error('Choose process-local or system Metal testing')
    if args.metal_profile is not None and not args.metal_system:
        parser.error('--metal-profile requires --metal-system')
    if args.metal_system and args.metal_profile is None:
        args.metal_profile = 1  # SpringBoard/MPS require the tested Apple2 table.
    if (args.metal_test or args.metal_system) and (args.action != 'run' or args.kernel != 'built' or not args.gpu or
            not args.userland or args.checkpoint or args.gpu_step or args.wifi):
        parser.error('Metal test requires run --kernel built --gpu --userland without a Wi-Fi test')
    if args.wifi and args.wifi != "inspect" and (args.touch or args.gpu or args.cpu_policy != "firmware"):
        parser.error("Wi-Fi root-power probe must run without touch, GPU or CPU-policy changes")
    if args.wifi in ('bhi', 'mhi', 'bhie', 'ipcr', 'qmi', 'driver', 'scan', 'station') and not args.userland:
        parser.error('BHI firmware test requires --userland and the verified private firmware overlay')
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
            if args.display and args.display not in kernel_manifest.get('display_modes_supported', []):
                raise SystemExit('Refusing: kernel has no requested lemonade display mode')
            if args.gpu_clock is not None and args.gpu_clock not in kernel_manifest.get('gpu_clock_mhz_supported', []):
                raise SystemExit('Refusing: kernel has no requested stock GPU clock')
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
        if args.metal_system:
            cmdline = ' '.join(arg for arg in cmdline.split() if not arg.startswith(('metal=', 'camtl=', 'metal_profile=')))
            cmdline += ' metal=1 metal_profile=' + str(args.metal_profile or 0)
            cmdline += ' camtl=' + ('1' if args.metal_system == 'desktop' else '0')
        if args.display:
            cmdline = ' '.join(arg for arg in cmdline.split() if not arg.startswith('lemonade_display='))
            cmdline += ' lemonade_display=' + str({'inspect': 1, 'clocks': 2, 'irq': 3, 'sync': 4, 'smmu': 5, 'map': 6, 'flip': 7}[args.display])
            if args.display == 'flip':
                cmdline += ' lemonade_display_flips=' + str(args.display_flips or 64)
            if args.display == 'sync':
                cmdline = ' '.join(arg for arg in cmdline.split() if not arg.startswith('ca_vbl='))
                cmdline += ' ca_vbl=1'
        if args.touch:
            cmdline = " ".join(arg for arg in cmdline.split() if not arg.startswith("touchscreen="))
            cmdline += " touchscreen=" + ("2" if args.touch == "inspect" else "1")
        if args.wifi:
            cmdline = " ".join(arg for arg in cmdline.split() if not arg.startswith(("wifi=", "wifi_step=", "lemonade_wifi=")))
            cmdline += " wifi=0 lemonade_wifi=" + str({"inspect": 1, "rails": 2, "domain": 3, "clocks": 4, "resets": 5, "controller": 6, "phy": 7, "root": 8, "radio": 9, "link": 10, "endpoint": 11, "mmio": 12, "smmu": 13, "sid": 14, "dma": 15, "dma-fault": 16, "bhi": 17, "mhi": 18, "bhie": 19, "ipcr": 20, "qmi": 21, "driver": 22, "scan": 23, "station": 24}[args.wifi])
        firmware_record = guard_record = None
        network_record = network_helper = None
        firmware_overlay = None
        if args.wifi in ('bhi', 'mhi', 'bhie', 'ipcr', 'qmi', 'driver', 'scan', 'station'):
            firmware_overlay, firmware_record, guard_record = verified_firmware_overlay(expected_kernel)
            if bool(firmware_record.get('network_private')) != args.network_test:
                raise SystemExit('Private network overlay and --network-test must be selected together')
            if args.network_test:
                sys.path.insert(0, str(ROOT / 'tools/apfs'))
                import apfs
                container = apfs.Container(str(firmware_overlay))
                try:
                    volume = apfs.pick_volume(container, None)
                    found = volume.lookup('fw/lemonade-network')
                    if found is None:
                        raise SystemExit('Private network file is missing')
                    inode = volume.inode(found[0])
                    if inode.mode != 0o100600 or inode.owner != 0 or inode.group != 0:
                        raise SystemExit('Invalid private network file permissions')
                    network_record = inode.contents()
                finally:
                    container.close()
                spec = importlib.util.spec_from_file_location('private_network', ROOT / 'tools/lemonade-network.py')
                network_helper = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(network_helper)
                network_helper.unpack(network_record)
                # These are temporary secret-bearing artifacts, including on failure.
                atexit.register(firmware_overlay.unlink, missing_ok=True)
                atexit.register((ROOT / 'out/lemonade-xnu.bin').unlink, missing_ok=True)
            if args.wifi in ('driver', 'scan', 'station'):
                if kernel_manifest.get('wifi_driver_profile') != 'qca6490-hw10-v1':
                    raise SystemExit('Refusing: kernel does not include the dedicated QCA6490 major-1 driver')
                for name in ('m3.bin', 'board.bin', 'regdb.bin'):
                    if not firmware_record.get('firmware', {}).get(name, {}).get('sha256'):
                        raise SystemExit('Refusing: original driver firmware is missing: ' + name)
            cmdline += ' lemonade_dma_verified=1'
        if args.cpu_policy != "firmware":
            cmdline = " ".join(arg for arg in cmdline.split() if not arg.startswith("cpufreq="))
            cmdline += " cpufreq=" + ("2" if args.cpu_policy == "inspect" else "1") + " ddr=0"
        if args.gpu or args.gpu_step:
            cmdline = " ".join(arg for arg in cmdline.split() if not arg.startswith("gpu="))
            if args.gpu:
                cmdline += " gpu=1 gpu_log=" + ("1" if args.gpu_trace else "0")
            else:
                cmdline += f" gpu=0 adreno={args.gpu_step} adreno_pause=0 adreno_watch=1 adreno_return=1"
        if args.gpu_clock is not None:
            cmdline = ' '.join(arg for arg in cmdline.split() if not arg.startswith('adreno_speed='))
            cmdline += ' adreno_speed=' + str({315: 1, 379: 2, 443: 3, 540: 4}[args.gpu_clock])
        cmdline = f"loader_return_after={args.seconds} epoch={int(time.time())} sb_delay=8 " + cmdline
        payload = ROOT / "out/lemonade-xnu.bin"
        extra_images = [f"ramdisk={verified_image('userland/root.apfs')}"] if args.userland or args.gpu or args.gpu_step else []
        if firmware_overlay:
            extra_images = [f'ramdisk={firmware_overlay}']
        graphics_record = None
        if args.metal_test or args.metal_system:
            graphics_overlay, graphics_record = verified_graphics_overlay(bool(args.metal_system))
            extra_images = [f'ramdisk={graphics_overlay}']
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
        record["gpu_submission_trace"] = args.gpu_trace
        record['gpu_clock_mhz_requested'] = args.gpu_clock
        record["metrics_mode"] = "frame-input-present-v2" if args.metrics else None
        record["cpu_policy"] = args.cpu_policy
        record["profile"] = args.profile
        record["touch"] = args.touch
        record["wifi"] = args.wifi
        record['network_test'] = args.network_test
        record['metal_test'] = args.metal_test
        record['metal_system'] = args.metal_system
        record['metal_profile'] = args.metal_profile
        record['display'] = args.display
        if graphics_record:
            record['graphics_image'] = graphics_record
        if firmware_record:
            record['wifi_firmware'] = firmware_record
            record['dma_guard'] = guard_record
        record["live_logs"] = []
        if args.userland or args.gpu or args.gpu_step:
            image_manifest = json.loads((ROOT / "local/lemonade/images.json").read_text())
            record["images"] = {name: image_manifest[name] for name in
                                ("userland/root.apfs", "userland/system.apfs")}
            if firmware_record:
                record['images'].pop('userland/root.apfs')
                record['images']['out/lemonade-wifi/root.apfs'] = firmware_record
            if graphics_record:
                record['images'].pop('userland/root.apfs')
                record['images'][str(graphics_overlay.relative_to(ROOT))] = graphics_record
        (ROOT / "out/lemonade-xnu.manifest.json").write_text(json.dumps(record, indent=2) + "\n")
        try:
            fb(serial, "stage", str(payload), timeout=60)
        finally:
            if args.network_test:
                payload.unlink(missing_ok=True)
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
                if args.network_test and args.boot_cpu == 7 and not record['live_logs']:
                    # Preserve early driver trace from this same run while the
                    # loader is alive on core 0. The separate printf message
                    # buffer can still rotate before the final collection.
                    live_path = ROOT / 'local/lemonade' / f'{int(time.time())}-network-early.log'
                    try:
                        fb(serial, 'oem', 'log', timeout=5)
                        fb(serial, 'get_staged', str(live_path), timeout=10)
                    finally:
                        if live_path.exists():
                            live_path.write_bytes(network_helper.redact(live_path.read_bytes(), network_record))
                    record['live_logs'].append({'path': str(live_path.relative_to(ROOT)),
                                               'sha256': hashlib.sha256(live_path.read_bytes()).hexdigest()})
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
        try:
            fb(serial, "get_staged", str(dest), timeout=30)
        finally:
            if args.action == 'run' and network_record is not None:
                if dest.exists():
                    dest.write_bytes(network_helper.redact(dest.read_bytes(), network_record))
                    record['network_identity_redacted'] = True
                firmware_overlay.unlink(missing_ok=True)
        print(f"Saved private device evidence: {dest}")
        if args.action == "run":
            if network_record is not None and record['live_logs']:
                pieces = []
                for item in record['live_logs']:
                    part = (ROOT / item['path']).read_bytes()
                    if hashlib.sha256(part).hexdigest() != item['sha256']:
                        raise SystemExit('Same-run early evidence changed before completion')
                    pieces.append(part)
                pieces.append(dest.read_bytes())
                dest.write_bytes(b'\n=== same-run network evidence segment ===\n'.join(pieces))
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
