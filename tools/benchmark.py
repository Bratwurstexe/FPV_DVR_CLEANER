"""Measure export speed on this computer, for the Speed chart in the README.

Usage:
    python tools/benchmark.py <recording> <timings.json> [--start 30] [--seconds 20]

Runs the three repair jobs of the chart at Fast, Balanced and Best (with the
GPU, if one passes the self-test) and Balanced once more with ``FLUGBILD_GPU=0``.
Every run is a separate process, so the GPU setting and caches never leak from
one run into the next. The result feeds ``tools/make_speed_chart.py``:

    python tools/make_speed_chart.py timings.json docs/media/speed.svg --machine "..."

Close other heavy programs first; a laptop should run on mains power.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]

JOBS = {
    "colour": {"farbberuhigung": 60, "rauschminderung": 0, "osd_entfernen": False, "vergroesserung_prozent": 100},
    "colour+osd+denoise": {"farbberuhigung": 60, "rauschminderung": 50, "osd_entfernen": True, "vergroesserung_prozent": 100},
    "all+1.5x": {"farbberuhigung": 60, "rauschminderung": 50, "osd_entfernen": True, "vergroesserung_prozent": 150},
}
RUNS = [("fast", True), ("balanced", True), ("best", True), ("balanced", False)]

# One run, in its own interpreter: returns wall-clock seconds and whether the GPU was used.
WORKER = r"""
import json, sys, threading, time
from pathlib import Path
from flugbild import gpu
from flugbild.media import inspect_video
from flugbild.recipe import Arbeitsrezept
from flugbild.restoration import process_temporal
source, target, start, seconds, settings = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), json.loads(sys.argv[5])
info = inspect_video(source)
recipe = Arbeitsrezept(signalart="analog", kantendetail=15, schnittbeginn=start, schnittende=start + seconds, **settings)
began = time.perf_counter()
result = process_temporal(source, target, recipe, info, None, lambda fraction: None, threading.Event())
elapsed = time.perf_counter() - began
if not result.success:
    raise SystemExit(result.message)
print(json.dumps({"seconds": elapsed, "gpu": gpu.enabled(), "gpu_name": gpu.name()}))
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("recording", type=Path)
    parser.add_argument("timings", type=Path)
    parser.add_argument("--start", type=float, default=30.0)
    parser.add_argument("--seconds", type=float, default=20.0)
    args = parser.parse_args()

    timings = json.loads(args.timings.read_text(encoding="utf-8")) if args.timings.exists() else {}
    with tempfile.TemporaryDirectory(prefix="flugbild-bench-") as scratch:
        for job, settings in JOBS.items():
            for effort, use_gpu in RUNS:
                key = f"{effort}|{job}" if use_gpu else f"cpu-{effort}|{job}"
                if key in timings:
                    print(f"{key}: kept {timings[key]['per_video_second']:.2f} s per video second", flush=True)
                    continue
                environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
                if not use_gpu:
                    environment["FLUGBILD_GPU"] = "0"
                target = Path(scratch) / f"{effort}-{job.replace('+', '-')}-{int(use_gpu)}.mp4"
                outcome = subprocess.run(
                    [sys.executable, "-c", WORKER, str(args.recording), str(target), str(args.start),
                     str(args.seconds), json.dumps({**settings, "aufwand": effort})],
                    capture_output=True, text=True, env=environment)
                if outcome.returncode:
                    raise SystemExit(f"{key} failed:\n{outcome.stderr[-2000:]}")
                run = json.loads(outcome.stdout.strip().splitlines()[-1])
                per_second = run["seconds"] / args.seconds
                timings[key] = {"per_video_second": round(per_second, 3), "wall_seconds": round(run["seconds"], 1),
                                "gpu": run["gpu"], "gpu_name": run["gpu_name"]}
                args.timings.write_text(json.dumps(timings, indent=2), encoding="utf-8")
                print(f"{key}: {per_second:.2f} s per video second ({run['seconds']:.0f} s, "
                      f"GPU {'on: ' + run['gpu_name'] if run['gpu'] else 'off'})", flush=True)
                time.sleep(5)  # Let the machine settle between runs.


if __name__ == "__main__":
    main()
