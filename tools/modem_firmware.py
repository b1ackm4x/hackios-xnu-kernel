#!/usr/bin/env python3
"""Validate a Qualcomm MDT firmware set against a device tree modem region.

This is an offline check. It does not authenticate, start, or contact the modem.
"""

import argparse
import json
import pathlib
import struct

from cellular_probe import fdt_nodes


def cell(prop):
    raw = bytes.fromhex(prop)
    if len(raw) != 4:
        raise ValueError("expected one 32-bit device tree cell")
    return struct.unpack(">I", raw)[0]


def inspect(dtb_path, firmware_dir, node_path):
    nodes = fdt_nodes(dtb_path.read_bytes())
    by_path = {node["path"]: node for node in nodes}
    if node_path not in by_path:
        raise ValueError(f"modem node {node_path} is absent")
    props = by_path[node_path]["properties"]
    if "memory-region" not in props or "qcom,pas-id" not in props:
        raise ValueError("modem node lacks memory-region or qcom,pas-id")
    memory_phandle = cell(props["memory-region"])
    matches = [node for node in nodes if any(
        cell(node["properties"][name]) == memory_phandle
        for name in ("phandle", "linux,phandle") if name in node["properties"])]
    if len(matches) != 1:
        raise ValueError(f"memory-region phandle {memory_phandle:#x} is not unique")
    region = matches[0]
    raw_reg = bytes.fromhex(region["properties"].get("reg", ""))
    if len(raw_reg) != 16:
        raise ValueError("expected a two-cell base and size for modem reserved memory")
    base, size = struct.unpack(">QQ", raw_reg)
    if size == 0:
        raise ValueError("empty modem reserved memory")

    header = (firmware_dir / "modem.mdt").read_bytes()
    if len(header) < 52 or header[:5] != b"\x7fELF\x01" or header[5] != 1:
        raise ValueError("modem.mdt is not little-endian ELF32")
    program_offset = struct.unpack_from("<I", header, 28)[0]
    entry_size, count = struct.unpack_from("<HH", header, 42)
    if entry_size != 32 or not 1 <= count <= 128 or program_offset + count * entry_size > len(header):
        raise ValueError("invalid program header table")
    segments = []
    total = 0
    for index in range(count):
        kind, _, _, address, file_size, memory_size, flags, _ = struct.unpack_from(
            "<8I", header, program_offset + index * entry_size)
        if kind != 1 or (flags >> 24) & 7 == 2 or memory_size == 0:
            continue
        if file_size > memory_size or address < base or address + memory_size > base + size:
            raise ValueError(f"segment {index} exceeds the modem reserved memory")
        name = f"modem.b{index:02d}"
        if file_size:
            actual = (firmware_dir / name).stat().st_size
            if actual != file_size:
                raise ValueError(f"{name}: {actual} bytes, expected {file_size}")
            total += actual
        segments.append({"index": index, "address": address, "file_bytes": file_size,
                         "memory_bytes": memory_size})
    if not segments or min(item["address"] for item in segments) != base:
        raise ValueError("firmware has no segments starting at the modem memory base")
    return {"modem_node": node_path, "memory_node": region["path"], "memory_base": base,
            "memory_size": size, "pas_id": cell(props["qcom,pas-id"]), "program_headers": count,
            "loadable_segments": len(segments), "file_bytes": total, "segments": segments}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dtb", type=pathlib.Path, required=True)
    parser.add_argument("--firmware-dir", type=pathlib.Path, required=True)
    parser.add_argument("--node", required=True, help="modem remoteproc node in this device's DTB")
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.dtb, args.firmware_dir, args.node), indent=2))
    except (OSError, ValueError) as error:
        parser.exit(1, f"modem_firmware: {error}\n")


if __name__ == "__main__":
    main()
