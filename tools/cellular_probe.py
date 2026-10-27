#!/usr/bin/env python3
"""Read-only modem inventory from an FDT and, optionally, Android over ADB.

This is a discovery tool. It does not start MPSS or implement a cellular driver.
The output intentionally omits IMEI, IMSI, phone numbers and SIM identifiers.
"""

import argparse
import json
import pathlib
import struct
import subprocess

FDT_MAGIC = 0xD00DFEED
BEGIN_NODE, END_NODE, PROP, NOP, END = 1, 2, 3, 4, 9
KEYWORDS = ("modem", "mpss", "mss", "ipa", "qrtr", "glink", "smem", "smp2p", "remoteproc")
PROPERTY_NAMES = {"compatible", "status", "memory-region", "qcom,pas-id", "qcom,smem", "qcom,smem-id", "reg", "interrupts", "phandle", "linux,phandle"}
ANDROID_PROPERTIES = (
    "ro.product.device", "ro.boot.hardware.sku", "ro.board.platform", "ro.boot.hardware", "ro.boot.baseband",
    "gsm.version.baseband", "init.svc.vendor.qcrild", "init.svc.vendor.qcrild2",
    "init.svc.vendor.qrtr-ns", "init.svc.vendor.ssgqmigd", "gsm.sim.state",
)


def fdt_nodes(data):
    if len(data) < 40:
        raise ValueError("DTB header is too short")
    magic, total, off_struct, off_strings, _, version, _, _, size_strings, size_struct = struct.unpack_from(">10I", data)
    if magic != FDT_MAGIC or version < 16 or total > len(data):
        raise ValueError("invalid or unsupported DTB")
    if off_struct + size_struct > total or off_strings + size_strings > total:
        raise ValueError("DTB sections exceed file size")
    strings = data[off_strings:off_strings + size_strings]
    pos, limit, stack, indexes, nodes = off_struct, off_struct + size_struct, [], [], []
    while pos + 4 <= limit:
        token = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        if token == BEGIN_NODE:
            stop = data.find(b"\0", pos, limit)
            if stop < 0:
                raise ValueError("unterminated DTB node")
            name = data[pos:stop].decode("ascii", "replace")
            stack.append(name)
            nodes.append({"path": "/" + "/".join(x for x in stack if x), "properties": {}})
            indexes.append(len(nodes) - 1)
            pos = (stop + 4) & ~3
        elif token == END_NODE:
            if not stack:
                raise ValueError("unbalanced DTB nodes")
            stack.pop()
            indexes.pop()
        elif token == PROP:
            if pos + 8 > limit or not nodes or not stack:
                raise ValueError("invalid DTB property")
            length, nameoff = struct.unpack_from(">II", data, pos)
            pos += 8
            if pos + length > limit or nameoff >= len(strings):
                raise ValueError("DTB property exceeds section")
            name_end = strings.find(b"\0", nameoff)
            if name_end < 0:
                raise ValueError("unterminated DTB property name")
            name = strings[nameoff:name_end].decode("ascii", "replace")
            if name in PROPERTY_NAMES:
                raw = data[pos:pos + length]
                if name in ("compatible", "status"):
                    value = [x.decode("ascii", "replace") for x in raw.rstrip(b"\0").split(b"\0")]
                else:
                    value = raw.hex()
                nodes[indexes[-1]]["properties"][name] = value
            pos = (pos + length + 3) & ~3
        elif token == NOP:
            continue
        elif token == END:
            if stack:
                raise ValueError("unclosed DTB nodes")
            return nodes
        else:
            raise ValueError(f"invalid DTB token {token}")
    raise ValueError("DTB has no end token")


def modem_nodes(data):
    nodes = fdt_nodes(data)
    return [node for node in nodes if any(
        word in (node["path"].rsplit("/", 1)[-1] + " " + " ".join(node["properties"].get("compatible", []))).lower()
        for word in KEYWORDS
    )]


def adb_call(serial, *args):
    command = ["adb"] + (["-s", serial] if serial else []) + list(args)
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"adb exited {result.returncode}")
    return result.stdout.strip()


def android_inventory(serial):
    devices = [line.split()[0] for line in adb_call(None, "devices").splitlines()[1:]
               if len(line.split()) >= 2 and line.split()[1] == "device"]
    if serial is None:
        if len(devices) != 1:
            raise RuntimeError(f"expected one authorized Android device, found {len(devices)}; use --serial")
        serial = devices[0]
    elif serial not in devices:
        raise RuntimeError(f"Android device {serial!r} is not authorized or connected")
    properties = {key: adb_call(serial, "shell", "getprop", key) for key in ANDROID_PROPERTIES}
    subsystem_lines = adb_call(serial, "shell", "for d in /sys/bus/msm_subsys/devices/subsys*; do "
        "[ -r \"$d/name\" ] && [ -r \"$d/state\" ] && "
        "printf '%s:%s\\n' \"$(cat \"$d/name\")\" \"$(cat \"$d/state\")\"; done")
    subsystems = dict(line.split(":", 1) for line in subsystem_lines.splitlines() if ":" in line)
    interfaces = adb_call(serial, "shell", "ls", "/sys/class/net").split()
    return {"properties": properties, "subsystems": subsystems,
            "cellular_interfaces": [name for name in interfaces if name.startswith(("rmnet", "wwan", "ipa"))]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dtb", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1] / "prebuilt/uefi/redwood.dtb")
    parser.add_argument("--adb", action="store_true", help="also query a connected Android phone without changing it")
    parser.add_argument("--serial", help="ADB serial when more than one phone is connected")
    args = parser.parse_args()
    try:
        report = {"dtb": str(args.dtb), "modem_related_nodes": modem_nodes(args.dtb.read_bytes())}
        if args.adb:
            report["android"] = android_inventory(args.serial)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"cellular_probe: {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
