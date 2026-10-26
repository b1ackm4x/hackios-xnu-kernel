# OnePlus 9 Bluetooth

Status: 2026-10-27. **Native Bluetooth discovery is integrated into the complete
`2026.10.27-base.2` runtime and the paired kernel/userland `main` sources.** The
original iOS Settings page displays a real nearby Mac while the Wi-Fi station,
graphics and battery services are enabled. Pairing, audio, peripheral data, sleep,
repeated on/off behavior and long-term stability remain unverified.

## Complete-base acceptance

Kernel SHA-256: `22206252397d69c037632b7ef695ead42c4e64a790210f0b624a30cd1e96dd6a`.
Runtime root SHA-256: `b8b686fbf49b51167826a36b4856e4d07bc6aa5d0d9b10811b4390c4df4e0929`.

The exact kernel passed its own hardware DMA guard. A 180-second combined run
passed graphics/touch, Wi-Fi association/DHCP/DNS/HTTP/reconnect and battery
verification. All 14 native Bluetooth samples were powered and available;
10 distinct native battery updates agreed with the kernel. Bluetooth transport
and the shared hardware stopped cleanly.

A separate 180-second run from Android used a temporary UI observer and bounded
client. The original `BTSDevicesController` displayed one device row, accompanied
by a genuine discovery notification. An IOSurface recording confirms the Mac.
The native daemon did not restart, and transport/UART/clocks returned cleanly.
The unchanged production root then ran for 90 seconds without those diagnostic
components, until an explicit host stop; all 28 runtime checks passed.

Reusing a returned RAM loader once entered Qualcomm `05c6:900e` during Wi-Fi
initialization, before Bluetooth startup was observed. The underlying cause is
not isolated. The supported launcher now returns through Android before another
cold start. A Sahara reset recovered the exact phone without flashing; Android
settings and all five checked partition hashes were verified afterward. This
failed warm run is retained separately and is not accepted as a successful test.

Private evidence: `local/lemonade/release-2026.10.27-base.2/` and
`local/lemonade/base-bluetooth-20261027/`. Device names, recordings, original
firmware and controller addresses remain private. The launcher temporarily turns
Android Bluetooth off for cold initialization, then restores its original setting
when returning. These captures are not optical panel photographs.

## Earlier isolated device evidence

The accepted 180-second graphical run used kernel SHA-256
`ed5fdda89da5899f4b408216423d47a703d56bf355ac3c1dbaad6fa84b715469`
and private candidate `out/lemonade-bt-native-23`.

- Native Bluetooth remained powered and available for all 14 sampled checks.
  The daemon did not restart.
- The actual controller returned successful Inquiry Command Status and Inquiry
  Complete events, plus 17 extended inquiry reports. Those are repeated reports,
  not 17 distinct devices.
- At least 2,680 LE advertising reports also reached the original native daemon.
  BLE advertisements are not equivalent to devices supported by the Settings UI.
- One discovery notification arrived. The original `BTSDevicesController` table
  changed from zero to one device row and retained it in five subsequent checks.
  A held IOSurface recording shows the same nearby Mac in Settings.
- Transport shutdown completed cleanly after 86 transmitted / 2,974 received HCI
  packets. Android subsequently booted on its original slot, with all five checked
  boot-partition hashes matching the backup.
- The Mac's temporary discoverability fixture restored its original setting.
  Its preference API readback did not establish radio visibility; the phone's
  genuine inquiry reports and visible device row provide that evidence.

A second 180-second run (`lemonade-bt-native-24`) removed both diagnostic libraries
and used only a one-shot Settings URL launcher, which exited without making any
BluetoothManager power or scan request. The native Settings page again discovered
the Mac. The daemon spawned once and did not restart; transport shutdown was clean
after 85 transmitted / 2,487 received packets. Full APFS entry comparison confirms
that the clean image differs from this validation image only by removal of the
launcher executable and its launchd job. The exact clean image was not separately
booted; its remaining files, modes and ownership match the tested image.

Earlier isolated hardware tests also verified:

- Cold controller Product `0x13`, ROM `0x100`, patch `0x1017`, SoC `0x400c0110`.
- This phone's own 178,532-byte `hpbtfw10.tlv` loaded in 735 segments. The 5,753-byte
  `hpnv10.bin` received 24 acknowledgements. Board ID zero selects that original
  vendor fallback; other board profiles are not assumed compatible.
- After reset, patch version was `0x2f3e`, HCI version 11 and manufacturer 29.
  Address readback matched this phone's privately collected controller address.
- A five-second passive LE scan returned 410 advertising reports and stopped
  successfully, followed by UART clock/pin restoration and Android verification.

Private evidence is indexed by
`local/lemonade/bluetooth-20261026/device-validation.json`. Each entry binds its
log, actual image/kernel and Android roundtrip. Recordings contain local device
names and stay private. IOSurface capture is not an optical panel photograph.

## Implementation

### Real controller and H4 transport

`lemonade_bluetooth.c` owns SE18 UART, preserves its register journal, performs
bounded FIFO I/O and restores pins/clocks on normal exit. It uses hardware
CTS/RFR at 115200 baud; no Bluetooth DMA path is implemented. Qualcomm IBS is
recognized only at H4 packet boundaries.

`lemonade_bt_firmware.h` checks TLV lengths, nested bounds and required NVM tags.
Only a RAM copy of the NVM is adjusted for the verified device address, 115200 baud
and awake operation. Other calibration/coexistence bytes remain unchanged.
Firmware and address inputs are private and are never committed or published.

The combined Wi-Fi run exposed a real Qualcomm diagnostic ACL packet on reserved
header `0x2edc` while firmware initialization was still active. The receiver now
separates only that complete, length-checked packet on the identified Product
`0x13` / ROM `0x100` / SoC `0x400c0110`. Ordinary ACL traffic is not discarded.
The initializer also disables SoC logging with the real `0xfc17` / `{0x14,0x00}`
command after NVM upload, requiring the controller's successful status. Diagnostic
payloads are never logged. These protocol facts match Linux's
[`hci_qca.c`](https://github.com/torvalds/linux/blob/master/drivers/bluetooth/hci_qca.c)
and [`btqca.c`](https://github.com/torvalds/linux/blob/master/drivers/bluetooth/btqca.c).

`lemonade_radio.c` serializes shared RPMh transactions. Voltage votes are combined
by physical resource address. A timed-out submitted transaction is quarantined
instead of restoring commands while hardware still owns it.

The native bridge exposes `com.apple.uart.bluetooth` as a stream kernel-control
socket with the observed H4/POSIX termios contract. A bounded transmit queue and
one worker serialize UART access. Unsupported options, framing errors and delivery
failures are explicit. After a native-client disconnect, the owner drains that
session, performs a real HCI reset and verifies firmware/address again before
another connection is permitted. Repeated daemon reconnects passed on the device;
this does not establish normal Settings toggle/reconnect acceptance.

`LemonadeBluetooth.cpp` publishes the genuine address, Qualcomm identity and H4
transport encoding. Readiness becomes true only after firmware, reset and address
readback pass. `lemonade_bt_bootstrap.c` implements the board's observed BlueTool
XPC bootstrap contract under its own launchd label. Only `boot` and `init` are
accepted, and success requires that verified readiness flag. It does not execute
arbitrary scripts or fabricate HCI responses.

### Native host compatibility

The original iOS stack assumes an Apple/Broadcom controller. Narrow compatibility
rules select its existing standard HCI paths for this actual Qualcomm board:

- Write LE Host Support is normalized from `{1,1}` to `{1,0}`. The second field is
  obsolete and must be zero under Core 5.2 Vol 4 Part E 7.3.79 / 6.35; the genuine
  controller response is preserved. The [Android common kernel](https://android.googlesource.com/kernel/common/+/1f3214aae9f49faf495f3836216afbc6c5400b2e/net/bluetooth/hci_sync.c)
  also uses zero for this field.
- An optional Write Default Erroneous Data Reporting step is skipped only when
  the real Supported Commands bitmap says it is unsupported. The native
  initializer's own sequencer advances with that option left disabled.
- The Broadcom callback backend stays unbound. Native null-callback guards return
  vendor-command-unsupported promptly; 230 direct callback loads were audited for
  their guards. Standard HCI dispatch and genuine feature bits remain intact.
- Existing process-local configuration switches select legacy scanning and
  disable proprietary scan-core, extended/periodic advertising and scanning
  paths. Unrelated preferences are preserved and no preference file is written.
- Under that legacy policy the unused Read Periodic Advertiser List Size query
  is skipped. Sending even this query first selects the extended command family,
  causing subsequent legacy scanning commands to fail with status `0x0c` under
  the [Core HCI command-family rule](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Core-54/out/en/host-controller-interface/host-controller-interface-functional-specification.html).

`lemonade_bt_compat.c` checks the real board/readiness, exact native Mach-O UUID,
function prefixes, descriptors and table pointers before changing process-local
data entries. Build-time native binary hashing adds another binding. Executable
pages are refused and original data-page protection is restored. No identity,
feature bitmap, HCI event or successful response is synthesized.

### Original Settings UI

The Bluetooth page previously blocked synchronously in NanoRegistry while asking
for the paired-watch registry. Six main-thread samples found the same wait, and
launchd reported the missing service. Including the original `nanoregistryd` and
`nanoregistrylaunchd` services resolves it; their binary contents are checked
against the unchanged System image. The real Settings controller now displays the
switch, search indicator and discovered device. No substitute list or registry
response is used.

## Builds and checks

The actual production H4 framing/version parser, FIFO bounds and shutdown, RPMh
transactions, firmware/NVM parsing, transmit queue, reconnect and legacy LE-host
parameter rules pass relevant ASan/UBSan host tests. Reconnect tests include
partial packets, timeout, identity mismatch and framing errors.

Compatibility tests run against real Mach VM protections on Apple Silicon and
check preference preservation, read-only data mutation/restoration, and refusal
of executable or unmapped memory. XNU and the userland components build. The USB
suite passes 63 tests, including exact-device/image provenance and keeping the
Bluetooth root while enabling the verified base renderer. These are local checks. Linux and macOS CI also run the production Bluetooth
tests; the generic CI build compiles the bootstrap and compatibility library
without reading an Apple system image or device inputs. Remote build results are
reported by the workflow for the exact commit; they do not replace device tests.

## Maintainer use

Acquire the exact device lease, verify its Android baseline and preserve its
original Bluetooth setting. Cold hardware tests require Android Bluetooth off.
Use the matching Linux build container/toolchain and a fresh output directory:

```sh
bash tools/lemonade-build-kernel.sh
bash tools/lemonade-build-bt-bootstrap.sh
bash tools/lemonade-build-bt-compat.sh
python3 tools/lemonade-build-bt-firmware.py \
  --output out/lemonade-bt-native-clean-N \
  --native --board-bootstrap --board-compat
```

The default native image contains no tracing libraries, acceptance client or
automatic Settings launcher. The local clean artifact is
`out/lemonade-bt-native-clean-1/root.apfs`. It remains private and device-bound.

After the normal verified RAM loader boot and System upload, a bounded graphical
session can use:

```sh
python3 tools/lemonade-usb.py run --kernel built --userland \
  --bluetooth native --bluetooth-image out/lemonade-bt-native-clean-1 \
  --bluetooth-ui --gpu --touch on --display direct --display-fences \
  --display-hz 120 --seconds 180 --boot-cpu 7 --cpus 4
```

Open Settings → Bluetooth normally. `--bluetooth-ui` enables the base renderer;
it does not require or add a test client. Return to Android, verify all five
partition hashes, restore the original Android Bluetooth setting and release the
lease after testing.

Development tools are opt-in. Build `lemonade-build-bt-client.sh` before selecting
`--native-client` for bounded power/scan requests, or `--settings-launch` for a
one-shot URL launch that makes no BluetoothManager request. `--native-trace` adds
the HCI observer; `--ui-trace` adds the bounded Settings observer and requires the
acceptance client. Build each observer first. The observers log status/counts,
static API names and controller classes, not device names or radio payloads.

For the integrated base, use the complete candidate flow after building the
production Bluetooth helpers and the diagnostic client/UI observer:

```sh
python3 tools/lemonade-build-base.py --version VERSION
python3 tools/lemonade-validate-base.py --version VERSION
python3 out/releases/lemonade-VERSION/launch.py android
python3 tools/lemonade-validate-base-bluetooth.py --version VERSION
python3 tools/lemonade-usb.py recording  # private inspection before the next boot
python3 tools/lemonade-validate-base-runtime.py --version VERSION
python3 out/releases/lemonade-VERSION/launch.py android
python3 tools/lemonade-verify-android.py
```

Each step must pass before continuing. Settings validation starts from Android;
the untouched runtime validator uses the shipped launcher, including its Android
roundtrip when the previous session has returned. Recovery/status operations
remain available for failed candidates, with exact-device and file-integrity
checks still enforced. Acceptance flags are bound to the tested kernel/root;
copying another version's receipts does not authorize a new package.
