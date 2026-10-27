# OnePlus 9 (lemonade)

This device directory follows the upstream product-directory layout. `device.conf` is the build profile, `sources.json` records source provenance and historical accepted runtime versions, and `lemonade*.rc.jobs` are the matching service profiles.

`tools/` contains OnePlus build, collection, packaging, validation and host-test programs. Their repository root is three directories above the script. Runtime source stays in the shared `xnu/`, `xnuloader/` and `userland/` trees.

`uefi/` contains this port's pinned Mu-Silicium revision and loader-hook inputs. Private device DTBs, radio firmware, images and evidence remain under the ignored root-level `local/lemonade/` and `out/` directories.

See [device setup](../../docs/LEMONADE.md), [base runtime](../../docs/LEMONADE-BASE.md), [Bluetooth](../../docs/LEMONADE-BLUETOOTH.md) and [CI](../../docs/LEMONADE-CI.md). Existing `lemonade` commands remain valid as a device alias; new top-level Make targets default to `oneplus-9`.
