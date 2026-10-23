#!/usr/bin/env python3
"""Host-only safety scenarios; no adb/fastboot process is ever started."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("lemonade_usb", Path(__file__).with_name("lemonade-usb.py"))
usb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usb)


class DeviceSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "local/lemonade").mkdir(parents=True)
        (self.root / "out").mkdir()
        (self.root / "prebuilt").mkdir()
        kernel = b"fixture kernel"
        (self.root / "prebuilt/mach.development.bcm2837").write_bytes(kernel)
        (self.root / "prebuilt/SHA256SUMS").write_text(hashlib.sha256(kernel).hexdigest() + "  prebuilt/mach.development.bcm2837\n")
        self.serial = "test-oneplus-device"
        self.identity = hashlib.sha256(self.serial.encode()).hexdigest()
        self.loader = "lemonade-" + self.identity[:16]
        (self.root / "local/lemonade/baseline.json").write_text(json.dumps({
            "hardware_serial_sha256": self.identity}))
        data = bytearray(4096)
        data[:8] = b"ANDROID!"
        struct.pack_into("<I", data, 40, 3)
        (self.root / "out/lemonade-boot.img").write_bytes(data)
        self.boot_manifest = {"device": "lemonade", "stage": "loader-only",
                              "loader_usb_serial": self.loader, "sha256": hashlib.sha256(data).hexdigest()}
        (self.root / "out/lemonade-boot.manifest.json").write_text(json.dumps(self.boot_manifest))
        payload = b"test return-path payload"
        (self.root / "out/lemonade-selftest.bin").write_bytes(payload)
        (self.root / "out/lemonade-selftest.manifest.json").write_text(json.dumps({
            "device": "lemonade", "stage": "return-selftest", "checkpoint": 999,
            "loader_usb_serial": self.loader, "sha256": hashlib.sha256(payload).hexdigest()}))
        self.adb = "List of devices attached\nother-phone device usb:1 product:sagit\n"
        self.fastboot = ""
        self.product = "lemonade"
        self.result = "selftest-ok 0 0"
        self.log = b"fixture kernel log"
        self.props = {"ro.product.model": "LE2110", "ro.soc.model": "SM8350", "ro.boot.flash.locked": "0"}
        self.calls = []

    def fake_command(self, argv, timeout=15, check=True):
        self.calls.append(argv)
        out, err = "", ""
        if argv == ["adb", "devices", "-l"]:
            out = self.adb
        elif argv == ["fastboot", "devices"]:
            out = self.fastboot
        elif argv[0] == "bash":
            out = "-v cpus=1 ufs=0 ufs_write=0"
        elif argv[0] == "python3" and Path(argv[1]).name == "mkpayload.py":
            Path(argv[argv.index("-o") + 1]).write_bytes(b"fixture payload")
        else:
            if argv[:2] not in (["adb", "-s"], ["fastboot", "-s"]):
                self.fail(f"Unscoped device command: {argv}")
            self.assertIn(argv[2], (self.serial, self.loader))
            parts = argv[3:]
            self.assertFalse(set(parts) & {"flash", "erase", "format", "set_active", "flashing"})
            if parts[:2] == ["shell", "getprop"]:
                out = self.props[parts[2]]
            elif parts == ["reboot", "bootloader"]:
                self.fastboot = self.serial + "\tfastboot\n"
            elif parts == ["getvar", "product"]:
                err = "(bootloader) product: " + self.product + "\n"
            elif parts == ["getvar", "result"]:
                err = "(bootloader) result: " + self.result + "\n"
            elif parts[0] == "get_staged":
                Path(parts[1]).write_bytes(self.log)
        return subprocess.CompletedProcess(argv, 0, out, err)

    def run_action(self, action, *extra):
        with patch.object(usb, "ROOT", self.root), patch.object(usb, "command", self.fake_command), \
                patch.object(usb.time, "sleep") as sleep, patch("sys.argv", ["lemonade-usb", action, *extra]), \
                contextlib.redirect_stdout(io.StringIO()):
            usb.main()
            return sleep

    def test_unrelated_usb_and_wifi_do_not_allow_boot(self):
        self.adb += self.serial + " device product:lemonade\n"
        with self.assertRaisesRegex(SystemExit, "not connected by USB"):
            self.run_action("boot")
        self.assertFalse(any("-s" in c for c in self.calls))

    def test_firmware_requires_real_dma_guard(self):
        with patch.object(usb, 'ROOT', self.root), self.assertRaisesRegex(SystemExit, 'no verified endpoint'):
            usb.verified_firmware_overlay('current-kernel')
        folder = self.root / 'local/lemonade/wifi'
        folder.mkdir()
        (folder / 'dma-guard-verified.json').write_text(json.dumps({'kernel_sha256': 'old-kernel'}))
        with patch.object(usb, 'ROOT', self.root), self.assertRaisesRegex(SystemExit, 'different kernel'):
            usb.verified_firmware_overlay('current-kernel')

    def test_firmware_rejects_foreign_evidence_path(self):
        folder = self.root / 'local/lemonade/wifi'
        folder.mkdir()
        (folder / 'dma-guard-verified.json').write_text(json.dumps({
            'kernel_sha256': 'current-kernel', 'log': 'outside-device.log'}))
        with patch.object(usb, 'ROOT', self.root), self.assertRaisesRegex(SystemExit, 'outside the private'):
            usb.verified_firmware_overlay('current-kernel')

    def test_corrupt_image_is_rejected_before_reboot(self):
        self.adb += self.serial + " device usb:2 product:lemonade\n"
        with (self.root / "out/lemonade-boot.img").open("ab") as f:
            f.write(b"corrupt")
        with self.assertRaisesRegex(SystemExit, "checksum"):
            self.run_action("boot")
        self.assertFalse(any("reboot" in c for c in self.calls))

    def test_wrong_manifest_identity_is_rejected(self):
        self.boot_manifest["loader_usb_serial"] = "another-device"
        (self.root / "out/lemonade-boot.manifest.json").write_text(json.dumps(self.boot_manifest))
        with self.assertRaisesRegex(SystemExit, "different device identity"):
            self.run_action("boot")

    def test_locked_phone_is_rejected_before_reboot(self):
        self.adb += self.serial + " device usb:2 product:lemonade\n"
        self.props["ro.boot.flash.locked"] = "1"
        with self.assertRaisesRegex(SystemExit, "ro.boot.flash.locked"):
            self.run_action("boot")
        self.assertFalse(any("reboot" in c for c in self.calls))

    def test_wrong_fastboot_product_is_rejected(self):
        self.fastboot = self.serial + "\tfastboot\n"
        self.product = "sagit"
        with self.assertRaisesRegex(SystemExit, "unverified fastboot product"):
            self.run_action("boot")
        self.assertFalse(any("boot" in c for c in self.calls))

    def test_verified_phone_uses_only_targeted_temporary_boot(self):
        self.adb += self.serial + " device usb:2 product:lemonade\n"
        self.run_action("boot")
        self.assertIn(["adb", "-s", self.serial, "reboot", "bootloader"], self.calls)
        self.assertIn(["fastboot", "-s", self.serial, "boot", str(self.root / "out/lemonade-boot.img")], self.calls)

    def test_libusb_topology_without_usb_prefix(self):
        self.adb += self.serial + " device 2-1.1 product:lemonade\n"
        self.run_action("boot")
        self.assertIn(["fastboot", "-s", self.serial, "boot", str(self.root / "out/lemonade-boot.img")], self.calls)

    def test_stock_lahaina_product_with_matching_identity(self):
        self.fastboot = self.serial + "\tfastboot\n"
        self.product = "lahaina"
        self.run_action("boot")
        self.assertIn(["fastboot", "-s", self.serial, "boot", str(self.root / "out/lemonade-boot.img")], self.calls)

    def test_memmap_composes_log_before_fetch(self):
        self.fastboot = self.loader + "\tfastboot\n"
        self.product = "xnuloader"
        self.run_action("memmap")
        actions = [c[3:] for c in self.calls if "-s" in c]
        self.assertLess(actions.index(["oem", "memmap"]), actions.index(["oem", "log"]))
        self.assertEqual(actions[-1][0], "get_staged")

    def test_selftest_checks_result_and_collects_failed_log(self):
        self.fastboot = self.loader + "\tfastboot\n"
        self.product = "xnuloader"
        self.result = "not-started 0 0"
        with self.assertRaisesRegex(SystemExit, "did not pass"):
            self.run_action("selftest")
        actions = [c[3:] for c in self.calls if "-s" in c]
        self.assertLess(actions.index(["oem", "selftest"]), actions.index(["getvar", "result"]))
        self.assertEqual(actions[-1][0], "get_staged")

    def test_timed_run_waits_before_query_and_keeps_staged_kernel_log(self):
        self.fastboot = self.loader + "\tfastboot\n"
        self.product = "xnuloader"
        self.result = "returned 19 0"
        sleep = self.run_action("run", "--seconds", "1")
        sleep.assert_called_once_with(9)
        actions = [c[3:] for c in self.calls if "-s" in c]
        self.assertLess(actions.index(["oem", "run", "0"]), actions.index(["getvar", "result"]))
        self.assertNotIn(["oem", "log"], actions)
        self.assertEqual(actions[-1][0], "get_staged")

    def test_gpu_refuses_the_unadapted_prebuilt_kernel(self):
        with self.assertRaises(SystemExit):
            self.run_action("run", "--gpu")
        self.assertFalse(self.calls)

    def test_gpu_step_range_is_checked_before_device_access(self):
        with self.assertRaises(SystemExit):
            self.run_action("run", "--kernel", "built", "--gpu-step", "23")
        self.assertFalse(self.calls)

    def test_changed_built_kernel_is_rejected_before_upload(self):
        self.fastboot = self.loader + "\tfastboot\n"
        self.product = "xnuloader"
        (self.root / "out/lemonade-kernel").write_bytes(b"different kernel")
        (self.root / "out/lemonade-kernel.manifest.json").write_text(json.dumps({
            "device": "lemonade", "stage": "xnu-kernel", "sha256": hashlib.sha256(b"kernel").hexdigest()}))
        with self.assertRaisesRegex(SystemExit, "kernel checksum"):
            self.run_action("run", "--kernel", "built", "--gpu-step", "1")
        self.assertFalse(any("stage" in c or "run" in c for c in self.calls))

    def test_cpu_policy_refuses_prebuilt_before_device_access(self):
        with self.assertRaises(SystemExit):
            self.run_action("run", "--cpu-policy", "balanced")
        self.assertFalse(self.calls)

    def test_touch_refuses_prebuilt_before_device_access(self):
        with self.assertRaises(SystemExit):
            self.run_action("run", "--touch", "on")
        self.assertFalse(self.calls)

    def test_wifi_refuses_prebuilt_before_device_access(self):
        with self.assertRaises(SystemExit):
            self.run_action("run", "--wifi", "inspect")
        self.assertFalse(self.calls)

    def test_wifi_refuses_old_kernel_before_upload(self):
        self.fastboot = self.loader + "\tfastboot\n"
        self.product = "xnuloader"
        data = b"kernel without PCIe survey"
        (self.root / "out/lemonade-kernel").write_bytes(data)
        (self.root / "out/lemonade-kernel.manifest.json").write_text(json.dumps({
            "device": "lemonade", "stage": "xnu-kernel", "sha256": hashlib.sha256(data).hexdigest()}))
        with self.assertRaisesRegex(SystemExit, "Wi-Fi"):
            self.run_action("run", "--kernel", "built", "--wifi", "inspect")
        self.assertFalse(any("stage" in c or "run" in c for c in self.calls))

    def test_wifi_write_stages_refuse_concurrent_power_users(self):
        for extra in (("--touch", "on"), ("--gpu",), ("--cpu-policy", "balanced")):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                self.run_action("run", "--kernel", "built", "--wifi", "rails", *extra)
        self.assertFalse(self.calls)

    def test_old_monolithic_wifi_power_mode_is_removed(self):
        with self.assertRaises(SystemExit):
            self.run_action("run", "--kernel", "built", "--wifi", "power")
        self.assertFalse(self.calls)

    def test_inspection_kernel_cannot_run_root_power_probe(self):
        self.fastboot = self.loader + "\tfastboot\n"
        self.product = "xnuloader"
        data = b"read-only Wi-Fi survey kernel"
        (self.root / "out/lemonade-kernel").write_bytes(data)
        (self.root / "out/lemonade-kernel.manifest.json").write_text(json.dumps({
            "device": "lemonade", "stage": "xnu-kernel", "sha256": hashlib.sha256(data).hexdigest(),
            "wifi_modes_supported": ["inspect"]}))
        with self.assertRaisesRegex(SystemExit, "Wi-Fi"):
            self.run_action("run", "--kernel", "built", "--wifi", "root")
        self.assertFalse(any("stage" in c or "run" in c for c in self.calls))

    def test_wifi_inspection_disables_redwood_hardware_path(self):
        self.fastboot = self.loader + "\tfastboot\n"
        self.product = "xnuloader"
        self.result = "returned 19 0"
        data = b"PCIe survey kernel"
        (self.root / "out/lemonade-kernel").write_bytes(data)
        (self.root / "out/lemonade-kernel.manifest.json").write_text(json.dumps({
            "device": "lemonade", "stage": "xnu-kernel", "sha256": hashlib.sha256(data).hexdigest(),
            "wifi_modes_supported": ["inspect"]}))
        self.run_action("run", "--kernel", "built", "--wifi", "inspect", "--seconds", "1")
        payload = next(c for c in self.calls if c[0] == "python3")
        args = payload[payload.index("--cmdline") + 1].split()
        self.assertEqual([x for x in args if x.startswith("wifi=")], ["wifi=0"])
        self.assertEqual([x for x in args if x.startswith("lemonade_wifi=")], ["lemonade_wifi=1"])

    def test_long_session_requires_performance_and_physical_touch(self):
        with self.assertRaises(SystemExit):
            self.run_action("run", "--profile", "performance", "--seconds", "900")
        self.assertFalse(self.calls)

    def test_touch_session_still_has_a_fifteen_minute_limit(self):
        with self.assertRaises(SystemExit):
            self.run_action("run", "--profile", "performance", "--touch", "on", "--seconds", "901")
        self.assertFalse(self.calls)

    def test_touch_refuses_old_kernel_before_upload(self):
        self.fastboot = self.loader + "\tfastboot\n"
        self.product = "xnuloader"
        data = b"previous CPU/GPU kernel"
        (self.root / "out/lemonade-kernel").write_bytes(data)
        (self.root / "out/lemonade-kernel.manifest.json").write_text(json.dumps({
            "device": "lemonade", "stage": "xnu-kernel", "sha256": hashlib.sha256(data).hexdigest()}))
        with self.assertRaisesRegex(SystemExit, "S6SY792 touch support"):
            self.run_action("run", "--kernel", "built", "--touch", "on")
        self.assertFalse(any("stage" in c or "run" in c for c in self.calls))

    def test_touch_inspection_emits_only_inspection_mode(self):
        self.fastboot = self.loader + "\tfastboot\n"
        self.product = "xnuloader"
        self.result = "returned 19 0"
        data = b"touch inspection kernel"
        (self.root / "out/lemonade-kernel").write_bytes(data)
        (self.root / "out/lemonade-kernel.manifest.json").write_text(json.dumps({
            "device": "lemonade", "stage": "xnu-kernel", "sha256": hashlib.sha256(data).hexdigest(),
            "touch_driver_supported": "lemonade-s6sy792-v1"}))
        self.run_action("run", "--kernel", "built", "--touch", "inspect", "--seconds", "1")
        payload = next(c for c in self.calls if c[0] == "python3")
        args = payload[payload.index("--cmdline") + 1].split()
        self.assertEqual([x for x in args if x.startswith("touchscreen=")], ["touchscreen=2"])

    def test_firmware_modes_require_verified_ram_overlay_before_device_access(self):
        for mode in ('bhi', 'mhi', 'bhie', 'ipcr', 'qmi'):
            with self.subTest(mode=mode), self.assertRaises(SystemExit):
                self.run_action('run', '--kernel', 'built', '--wifi', mode)
            self.assertFalse(self.calls)

    def test_cpu_policy_refuses_old_kernel_before_upload(self):
        self.fastboot = self.loader + "\tfastboot\n"
        self.product = "xnuloader"
        data = b"previous GPU-only kernel"
        (self.root / "out/lemonade-kernel").write_bytes(data)
        (self.root / "out/lemonade-kernel.manifest.json").write_text(json.dumps({
            "device": "lemonade", "stage": "xnu-kernel", "sha256": hashlib.sha256(data).hexdigest()}))
        with self.assertRaisesRegex(SystemExit, "CPU policy support"):
            self.run_action("run", "--kernel", "built", "--cpu-policy", "balanced")
        self.assertFalse(any("stage" in c or "run" in c for c in self.calls))

    def test_performance_profile_rejects_conflicts_before_device_access(self):
        with self.assertRaises(SystemExit):
            self.run_action("run", "--profile", "performance", "--kernel", "prebuilt")
        self.assertFalse(self.calls)

    def test_performance_profile_keeps_scoped_ram_only_run(self):
        self.fastboot = self.loader + "\tfastboot\n"
        self.product = "xnuloader"
        self.result = "returned 19 0"
        self.log = b"adreno: THE GPU STAYS ON\n"
        data = b"verified CPU/GPU kernel"
        (self.root / "out/lemonade-kernel").write_bytes(data)
        (self.root / "out/lemonade-kernel.manifest.json").write_text(json.dumps({
            "device": "lemonade", "stage": "xnu-kernel", "sha256": hashlib.sha256(data).hexdigest(),
            "cpu_policy_supported": "sm8350-capped-epss-v1"}))
        (self.root / "userland").mkdir()
        images = {}
        for name in ("root", "system"):
            relative = f"userland/{name}.apfs"
            (self.root / relative).write_bytes(b"test disk")
            images[relative] = {"sha256": hashlib.sha256(b"test disk").hexdigest(), "bytes": 9}
        (self.root / "local/lemonade/images.json").write_text(json.dumps(images))
        self.run_action("run", "--profile", "performance", "--seconds", "1")
        payload_call = next(c for c in self.calls if c[0] == "python3")
        args = payload_call[payload_call.index("--cmdline") + 1].split()
        for arg in ("bootcpu=7", "cpus=4", "cpufreq=1", "gpu=1", "ufs=0", "ufs_write=0"):
            self.assertIn(arg, args)
        self.assertEqual(sum(arg.startswith("cpus=") for arg in args), 1)
        self.assertNotIn("-v", args)


if __name__ == "__main__":
    unittest.main()
