# POCO X5 Pro (redwood)

Upstream product layout: `device.conf` is the port profile; `soc.args` contains the public SoC defaults; `prebuilt/` contains the original public UEFI/BootShim and device-tree fixture; `uefi/` contains the Mu-Silicium port; `tools/` contains the cellular and modem inspection programs.

Use `common/build.sh poco-x5-pro` (the existing `redwood` alias also works). These hardware inputs belong to the POCO port and are not fallbacks for OnePlus 9. See `docs/PORTING.md` for new targets.
