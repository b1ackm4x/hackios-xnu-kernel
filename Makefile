# HackiOS: one source checkout, upstream directory layout, device-specific entry points.
# make fetch        fetch the iOS 16.5 IPSW into sysimage/ on this machine
# make images       build system images using DEVICE's profile
# make kernel       build the kernel (OnePlus 9 by default)
# make loader       build the loader using this machine's identity inputs
# make loader-open  build a generic loader without changing identity.h
# make components   compile and package public OnePlus components (Linux, THEOS required)
# make qemu         run the prepared QEMU images
# make qemu-open    run the open kernel with the hello RAM disk
# make phone BASE=/absolute/path/to/sealed-base  launch an accepted OnePlus RAM package
# make prebuilt     refresh the public prebuilts from local builds
# make clean        remove generated kernel, loader and userland build outputs
# DEVICE: oneplus-9 (alias lemonade), poco-x5-pro (alias redwood), qemu-virt

DEVICE ?= oneplus-9
JOBS ?= 8
SECS ?= 600
ARGS_OPEN ?= cpus=1
IMAGE_OPTS ?=
BASE ?=

.PHONY: help all fetch images kernel loader loader-open hello components qemu qemu-open phone prebuilt clean
help:
	@sed -n '2,13p' Makefile | sed 's/^# //'
all: kernel loader images
fetch:
	bash sysimage/fetch.sh
images:
	SKIP="kernel loader boot" bash common/build.sh $(DEVICE) $(IMAGE_OPTS)
kernel:
	@if [ "$(DEVICE)" = oneplus-9 ] || [ "$(DEVICE)" = lemonade ]; then \
	  JOBS=$(JOBS) bash devices/oneplus-9/tools/lemonade-build-kernel.sh; \
	else \
	  SKIP="loader images boot" JOBS=$(JOBS) bash common/build.sh $(DEVICE); \
	fi
loader:
	bash xnuloader/build.sh
loader-open:
	LOADER_PLACEHOLDER=1 LOADER_USB_SERIAL=lemonade-ci bash xnuloader/build.sh
hello:
	bash -c '. ./xnu-env.sh && xnu-clang -arch arm64 -Os -static -nostdlib -ffreestanding -fno-stack-protector -fno-builtin -Wl,-e,_start -Wl,-pagezero_size,0x100000000 -o userland/hello userland/src/hello.c'
components:
	JOBS=$(JOBS) bash tools/ci/build-lemonade.sh
qemu:
	bash common/run.sh qemu-virt $(SECS)
qemu-open: kernel loader-open hello
	mkdir -p logs
	python3 xnuloader/mkpayload.py -o qemu/payload.bin kernel=xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837 ramdisk=userland/hello --cmdline "$(ARGS_OPEN)"
	bash -o pipefail -c 'qemu/run.sh $(SECS) | tee logs/qemu-open.log'
phone:
	@if [ "$(DEVICE)" = oneplus-9 ] || [ "$(DEVICE)" = lemonade ]; then \
	  test -n "$(BASE)" && test -f "$(BASE)/launch.py" || { echo 'Set BASE to a sealed OnePlus base package; see docs/LEMONADE-BASE.md' >&2; exit 1; }; \
	  python3 "$(BASE)/launch.py" start; \
	else bash common/run.sh $(DEVICE) $(SECS); fi
prebuilt:
	bash tools/update-prebuilt.sh
clean:
	rm -rf xnu-src/xnu-8796.121.2/BUILD xnuloader/loader.obj xnuloader/loader.efi userland/root.apfs userland/system.apfs userland/obj
