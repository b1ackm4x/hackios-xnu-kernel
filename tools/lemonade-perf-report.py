#!/usr/bin/env python3
"""Measured presentation intervals during fixed gesture windows, including stalls.

A window ends 600 ms after touch-up (or a button press), capped at the next
gesture. No long frame is discarded. Idle gaps outside these windows are not
included. This measures framebuffer completion, not panel scanout/photons.
"""
import argparse
import bisect
import hashlib
import json
from pathlib import Path
import re
import statistics
from frames import lines_of


def stats(values):
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    return {"count": len(values), "mean_ms": round(statistics.mean(values) / 1000, 3),
            "p50_ms": round(statistics.median(values) / 1000, 3),
            "p95_ms": round(ordered[min(len(ordered)-1, int(len(ordered)*.95))] / 1000, 3),
            "max_ms": round(max(values) / 1000, 3)}


def inspect(log, gesture_limit=None):
    paths = [log]
    manifest = json.loads(log.with_suffix('.manifest.json').read_text())
    if hashlib.sha256(log.read_bytes()).hexdigest() != manifest.get('log_sha256'):
        raise SystemExit(f'Final trace checksum mismatch: {log}')
    root = Path(__file__).resolve().parents[1]
    for info in manifest.get('live_logs', []):
        path = root / info['path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != info['sha256']:
            raise SystemExit(f'Live trace checksum mismatch: {path}')
        paths.append(path)
    samples, draws, swaps, shows = [], [], {}, {}
    for line in lines_of(paths):
        w = line.split()
        try:
            if w[1] == 'p':
                samples.append(tuple(map(int, (w[7], w[2], w[3], w[4], w[5]))))
            elif w[1] == 'g':
                draws.append((int(w[6]), int(w[4])))
            elif w[1] == 's':
                swaps[int(w[2])] = int(w[4])
            elif w[1] == 'v':
                shows[int(w[2])] = tuple(map(int, (w[4], w[6], w[8])))
        except (IndexError, ValueError):
            continue
    samples.sort()
    draws.sort()
    frames = []
    ends = [end for end, _ in draws]
    for sid, (ready, shown, done) in shows.items():
        if sid not in swaps:
            continue
        swap = swaps[sid]
        index = bisect.bisect_right(ends, swap) - 1
        draw_us = draws[index][0] - draws[index][1] if index >= 0 else None
        frames.append(dict(done=done, queue_and_gpu=ready-swap, copy=done-shown, draw=draw_us))
    frames.sort(key=lambda f: f['done'])
    starts = [s for s in samples if s[1] in (1, 4)]
    report, all_gaps, drag_gaps, active_frames = [], [], [], {}
    for i, (start, kind, finger, x, y) in enumerate(starts[:gesture_limit]):
        next_start = starts[i+1][0] if i+1 < len(starts) else start+2500000
        up = next((s[0] for s in samples if start < s[0] < next_start and s[1] == 3 and s[2] == finger), start)
        end = min(next_start, up+600000)
        chosen = [f for f in frames if start <= f['done'] < end]
        gaps = [b['done']-a['done'] for a, b in zip(chosen, chosen[1:])]
        dragging = [f for f in chosen if f['done'] <= up] if up-start >= 100000 else []
        drag_gaps += [b['done']-a['done'] for a, b in zip(dragging, dragging[1:])]
        all_gaps += gaps
        active_frames.update({f['done']: f for f in chosen})
        report.append(dict(index=i+1, kind='touch' if kind == 1 else 'button', x=x, y=y,
                           start_us=start, end_us=end, frames=len(chosen),
                           touch_duration_ms=round((up-start)/1000, 3),
                           first_frame_ms=round((chosen[0]['done']-start)/1000, 3) if chosen else None,
                           intervals=stats(gaps), fps=round(len(gaps)*1e6/sum(gaps), 2) if gaps else None))
    stages = {name: stats([f[name] for f in active_frames.values() if f[name] is not None])
              for name in ('draw', 'queue_and_gpu', 'copy')}
    text = '\n'.join(path.read_text(errors='replace') for path in paths)
    temperatures = sorted(set(tuple(map(float, m)) for m in re.findall(r"sensors read ([\d.]+) and ([\d.]+) degrees", text)))
    return dict(log=str(log), log_sha256=hashlib.sha256(log.read_bytes()).hexdigest(),
                measurement='framebuffer completion within touch-down to touch-up + 600 ms; no gaps discarded',
                gestures=report, total_gestures=len(starts), analyzed_gestures=len(report), total_frames=len(frames),
                active_intervals=stats(all_gaps),
                finger_down_intervals=stats(drag_gaps),
                finger_down_fps=round(len(drag_gaps)*1e6/sum(drag_gaps), 2) if drag_gaps else None,
                finger_down_over_18ms_percent=round(100*sum(g>18000 for g in drag_gaps)/len(drag_gaps), 2) if drag_gaps else None,
                active_fps=round(len(all_gaps)*1e6/sum(all_gaps), 2) if all_gaps else None,
                over_18ms_percent=round(100*sum(g>18000 for g in all_gaps)/len(all_gaps), 2) if all_gaps else None,
                over_34_5ms_percent=round(100*sum(g>34500 for g in all_gaps)/len(all_gaps), 2) if all_gaps else None,
                stages=stages, gpu_temperatures_c=temperatures)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--first-gestures', type=int, help='compare only this fixed workload prefix')
    args = parser.parse_args()
    if args.first_gestures is not None and args.first_gestures < 1:
        parser.error('--first-gestures must be positive')
    result = inspect(args.log, args.first_gestures)
    rendered = json.dumps(result, indent=2)+'\n'
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end='')
