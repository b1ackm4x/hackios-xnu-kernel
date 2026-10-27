#!/usr/bin/env python3
"""Check a saved real-device GPU run; startup alone is not rendering acceptance."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def inspect(log):
    manifest = json.loads(log.with_suffix(".manifest.json").read_text())
    paths = [log]
    live_checksums = True
    for info in manifest.get("live_logs", []):
        path = Path(__file__).resolve().parents[3] / info["path"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != info["sha256"]:
            live_checksums = False
            continue
        paths.append(path)
    text = "\n".join(path.read_text(errors="replace") for path in paths)
    contexts = sorted(set(line for line in text.splitlines()
                          if "context for CoreAnimation" in line and "redwood-gl:" in line))
    passes = sorted(set(line for line in text.splitlines()
                        if "gl_test: PASS: hardware renderer FD660; shader triangle and pixel readback verified" in line))
    completed = re.findall(r"tl: q (\d+) took (\d+)", text)
    checks = {
        "recorded_lemonade_gpu_run": manifest.get("device") == "lemonade" and
            manifest.get("kernel_source") == "built" and manifest.get("gpu_enabled") is True and
            manifest.get("userland") is True,
        "log_checksum": hashlib.sha256(log.read_bytes()).hexdigest() == manifest.get("log_sha256"),
        "live_log_checksums": live_checksums,
        "returned_to_loader": "result: returned " in manifest.get("result", ""),
        "init_checks": "=== 36 checks, 0 failed ===" in text,
        "gpu_started": "adreno: THE GPU STAYS ON" in text,
        "coreanimation_fd660": bool(contexts) and all("FD660" in line for line in contexts),
        "shader_pixel_readback": bool(passes),
        "gpu_fences_completed": bool(completed),
        "no_render_failure": not any(marker in text for marker in (
            "gl_test: FAIL:", "adreno: gpu: no answer:", "adreno: gpu: ring full:",
            "THE 3D CORE IS RECOVERED", "THE GPU IS NOT STARTED")),
    }
    return {"passed": all(checks.values()), "checks": checks,
            "kernel_sha256": manifest.get("kernel_sha256"),
            "log": str(log), "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
            "seconds": manifest.get("seconds"), "coreanimation_contexts": contexts,
            "checked_log_files": len(paths),
            "triangle_results": passes,
            "last_completed_gpu_fence": max((int(n) for n, _ in completed), default=0)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = inspect(args.log)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
    raise SystemExit(0 if report["passed"] else 1)
