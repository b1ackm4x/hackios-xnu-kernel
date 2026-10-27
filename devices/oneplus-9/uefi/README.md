# OnePlus 9 UEFI inputs

`BASE` pins Mu-Silicium. The patch and `XnuLoader.c` / `XnuLoader.h` are the original common loader-hook baseline imported with the port. This directory owns a copy so the OnePlus build does not consume another board's UEFI port or binaries.

From the repository root, `python3 devices/oneplus-9/tools/lemonade-prepare.py` applies these hooks to the local pinned `Mu-Silicium/` checkout, then adds OnePlus RAM reservations, disables persistent firmware logs and sanitizes the locally collected DTB. The script requires the existing private device baseline; see `docs/LEMONADE.md`.

No prebuilt OnePlus firmware or device identity is stored here. The source import does not rebuild or replace a previously accepted sealed runtime package.
