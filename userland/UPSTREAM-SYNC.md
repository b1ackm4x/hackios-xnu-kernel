# Upstream integration, 2026-10-27

The upstream project moved its userland into the kernel repository. This integration uses
`oosexgang/hackios-xnu-kernel@88513176b1e96ecf0b764cd62c6f4e6ac71345e3`, while retaining our separate
userland repository and its history.

Imported SpringBoard cellular hooks are gated by the kernel's read-only `cellular_enabled` flag.
The OnePlus 9 profile does not enable them. Signal bars use measured kernel state rather than a
forced one-bar fallback. Existing Metal, CoreAnimation, Siri, battery, Bluetooth and Sileo paths
remain. Optional modem firmware staging requires explicit target DTB and node inputs.

The original Redwood symbols, paths and libraries remain our ABI. Upstream's removal of Metal/AIR,
its tests and related image content was not imported. This source sync is host-tested and compiled;
it does not extend the hardware acceptance of the previously sealed `2026.10.27-base.2` runtime.
The paired kernel commit is recorded by that repository's `devices/oneplus-9/sources.json`.
