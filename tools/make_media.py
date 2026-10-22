"""Build side-by-side comparison clips and README GIFs from rendered pairs.

Usage: python tools/make_media.py <folder with NAME.mp4 and NAME-original.mp4> <output folder>

Each pair becomes NAME-compare.mp4 (high quality, for video editing) and
NAME.gif (small, for the README). The 4:3 picture area of 720p DVR recordings
is cropped so the comparison uses the space for picture, not letterbox.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

FONT_DIR = Path(__file__).resolve().parents[1] / "src" / "flugbild" / "assets"


def label(text: str, x: str) -> str:
    return (f"drawtext=fontfile=Inter.ttf:text='{text}':fontsize=22:fontcolor=white:"
            f"box=1:boxcolor=0x000000@0.6:boxborderw=8:x={x}:y=h-th-18")


def compare(original: Path, cleaned: Path, target: Path, panel_height: int, crop: str) -> None:
    graph = (f"[0:v]{crop},scale=-2:{panel_height}:flags=lanczos,{label('ORIGINAL', '18')}[a];"
             f"[1:v]{crop},scale=-2:{panel_height}:flags=lanczos,{label('CLEANED', 'w-tw-18')}[b];"
             "[a][b]hstack=inputs=2,format=yuv420p[v]")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(original), "-i", str(cleaned),
                    "-filter_complex", graph, "-map", "[v]", "-c:v", "libx264", "-crf", "16", "-preset", "slow",
                    str(target)], check=True, cwd=FONT_DIR)


def gif(source: Path, target: Path, width: int, fps: int, seconds: float) -> None:
    graph = (f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];"
             "[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "1", "-t", str(seconds), "-i", str(source),
                    "-filter_complex", graph, "-loop", "0", str(target)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--gif-width", type=int, default=820)
    parser.add_argument("--gif-fps", type=int, default=10)
    parser.add_argument("--gif-seconds", type=float, default=4.0)
    parser.add_argument("--gif-only", action="store_true", help="Reuse existing comparison clips")
    args = parser.parse_args()
    args.folder, args.output = args.folder.resolve(), args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    for cleaned in sorted(args.folder.glob("*.mp4")):
        if cleaned.stem.endswith("-original") or cleaned.stem.endswith("-compare"):
            continue
        original = cleaned.with_name(f"{cleaned.stem}-original.mp4")
        if not original.exists():
            continue
        # Crop the same 4:3 picture area from both, whatever their output size.
        crop = "crop=iw*944/1280:ih:iw*168/1280:0"
        if cleaned.stem.startswith("d_"):
            # Upscaling: a detail region, enlarged, so the gain is visible.
            crop = "crop=iw*0.3:ih*0.42:iw*0.36:ih*0.4,scale=1080:-2:flags=neighbor"
            panel = 540
        else:
            panel = 720
        combined = args.output / f"{cleaned.stem}-compare.mp4"
        if not (args.gif_only and combined.exists()):
            compare(original, cleaned, combined, panel, crop)
        gif(combined, args.output / f"{cleaned.stem}.gif", args.gif_width, args.gif_fps, args.gif_seconds)
        print(combined, (args.output / f"{cleaned.stem}.gif").stat().st_size // 1024, "KiB")


if __name__ == "__main__":
    main()
