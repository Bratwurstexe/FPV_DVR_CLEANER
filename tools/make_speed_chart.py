"""Processing-time chart for the README.

Usage: python tools/make_speed_chart.py <timings.json> <output.svg> [--machine "CPU name"]

``timings.json`` maps "<effort>|<job>" to {"per_video_second": seconds}, with
effort "fast", "balanced" or "best" (measured with the GPU, if any) and job
"colour", "colour+osd+denoise" or "all+1.5x". An optional "cpu-balanced|<job>"
entry, measured with ``FLUGBILD_GPU=0``, is shown as text beside each job.
Seconds of processing per second of video equal minutes of processing per
minute of footage, which is what the chart shows.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

JOBS = [("colour", "Colour repair"),
        ("colour+osd+denoise", "Colour repair + OSD removal + noise reduction"),
        ("all+1.5x", "Everything + 1.5× upscaling to 1080p")]
EFFORTS = [("fast", "Fast"), ("balanced", "Balanced"), ("best", "Best")]

WIDTH = 760
LEFT = 24
RIGHT = 96
BAR = 16
GAP = 2
STYLE = """
  .surface { fill: #fcfcfb; }
  .ink { fill: #0b0b0b; }
  .soft { fill: #52514e; }
  .muted { fill: #898781; }
  .grid { stroke: #e1e0d9; }
  .axis { stroke: #c3c2b7; }
  .s1 { fill: #2a78d6; } .s2 { fill: #eb6834; } .s3 { fill: #1baf7a; }
  @media (prefers-color-scheme: dark) {
    .surface { fill: #1a1a19; }
    .ink { fill: #f0efec; }
    .soft { fill: #c3c2b7; }
    .grid { stroke: #2c2c2a; }
    .axis { stroke: #383835; }
    .s1 { fill: #3987e5; } .s2 { fill: #d95926; } .s3 { fill: #199e70; }
  }
  text { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
"""


def bar(x: float, y: float, length: float, css: str) -> str:
    """Bar from the baseline with a 4px rounded data end and a square base."""
    radius = min(4.0, length / 2, BAR / 2)
    return (f'<path class="{css}" d="M{x:.1f},{y:.1f} h{length - radius:.1f} '
            f'a{radius},{radius} 0 0 1 {radius},{radius} v{BAR - 2 * radius:.1f} '
            f'a{radius},{radius} 0 0 1 -{radius},{radius} h-{length - radius:.1f} z"/>')


def minutes(value: float) -> str:
    return f"{value:.1f} min" if value < 10 else f"{value:.0f} min"


def chart(timings: dict, machine: str) -> str:
    value = {key: entry["per_video_second"] for key, entry in timings.items()}
    longest = max(value.get(f"{effort}|{job}", 0) for effort, _ in EFFORTS for job, _ in JOBS)
    step = next(s for s in (0.5, 1, 2, 5, 10, 20) if longest / s <= 6)
    top_tick = step * -(-longest // step)
    plot = WIDTH - LEFT - RIGHT
    scale = plot / top_tick
    parts = []
    y = 28
    parts.append(f'<text class="ink" x="{LEFT}" y="{y}" font-size="17" font-weight="600">'
                 'Processing time for one minute of analog footage</text>')
    y += 22
    parts.append(f'<text class="soft" x="{LEFT}" y="{y}" font-size="13">'
                 f'720p at 60 fps, 4:3 picture. CPU and GPU, measured on {machine}; shorter is better.</text>')
    y += 26
    x = LEFT
    for index, (_, label) in enumerate(EFFORTS, start=1):
        parts.append(f'<rect class="s{index}" x="{x}" y="{y - 10}" width="12" height="12" rx="3"/>')
        parts.append(f'<text class="soft" x="{x + 18}" y="{y}" font-size="13">{label}</text>')
        x += 18 + len(label) * 7.5 + 22
    y += 22
    plot_top = y
    rows = []
    for job, label in JOBS:
        cpu = value.get(f"cpu-balanced|{job}")
        rows.append(f'<text class="ink" x="{LEFT}" y="{y + 12}" font-size="13" font-weight="600">{label}</text>')
        if cpu:
            rows.append(f'<text class="muted" x="{WIDTH - 12}" y="{y + 12}" font-size="12" text-anchor="end">'
                        f'Balanced without GPU: {minutes(cpu)}</text>')
        y += 22
        for index, (effort, _) in enumerate(EFFORTS, start=1):
            seconds = value.get(f"{effort}|{job}")
            if seconds is None:
                continue
            length = max(2.0, seconds * scale)
            rows.append(bar(LEFT, y, length, f"s{index}"))
            rows.append(f'<text class="ink" x="{LEFT + length + 6:.1f}" y="{y + 12.5}" font-size="12">'
                        f'{minutes(seconds)}</text>')
            y += BAR + GAP
        y += 16
    plot_bottom = y - 10
    ticks = []
    tick = 0.0
    while tick <= top_tick + 1e-9:
        tx = LEFT + tick * scale
        css = "axis" if tick == 0 else "grid"
        ticks.append(f'<line class="{css}" x1="{tx:.1f}" y1="{plot_top}" x2="{tx:.1f}" y2="{plot_bottom}" stroke-width="1"/>')
        ticks.append(f'<text class="muted" x="{tx:.1f}" y="{plot_bottom + 16}" font-size="11" text-anchor="middle">'
                     f'{tick:g}</text>')
        tick += step
    ticks.append(f'<text class="muted" x="{LEFT + plot / 2:.1f}" y="{plot_bottom + 34}" font-size="11" '
                 'text-anchor="middle">minutes of processing</text>')
    height = plot_bottom + 48
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
            f'viewBox="0 0 {WIDTH} {height}" role="img" '
            'aria-label="Processing time per minute of analog footage for the Fast, Balanced and Best settings">\n'
            f'<style>{STYLE}</style>\n'
            f'<rect class="surface" width="{WIDTH}" height="{height}" rx="10"/>\n'
            + "\n".join(ticks + parts + rows) + "\n</svg>\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("timings", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--machine", default="a laptop")
    args = parser.parse_args()
    timings = json.loads(args.timings.read_text(encoding="utf-8"))
    args.output.write_text(chart(timings, args.machine), encoding="utf-8")


if __name__ == "__main__":
    main()
