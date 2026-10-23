"""Build the README images and GIF for the HDZero breakup repair.

Usage: python tools/make_hdzero_media.py <clip> <output folder> [--gif FIRST-LAST]
           [--video FIRST-LAST] [--detect N ...] [--pairs N ...] [--name PREFIX] [--amount 35]

Frame numbers are those of ffmpeg's ``select`` filter. Writes

* PREFIX-detection.jpg: the frames in --detect with every block the repair
  flags outlined, the way the detector sees them;
* PREFIX-before-after.jpg: the frames in --pairs, original left, repaired right;
* PREFIX.gif: the stretch in --gif, slowed down, as original | detected | repaired;
* PREFIX-slow.mp4: the same for the stretch in --video, as a sharp video.

Only the 4:3 picture area of a 720p recording is used.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flugbild.digital import BLOCK, MIN_BLOCKS, SPAN, _Frame, broken_blocks, level, repair_stream  # noqa: E402

WIDTH, HEIGHT, LEFT = 960, 720, 160
MARGIN = 8  # frames decoded around each requested frame
YELLOW, GREY = (0, 230, 255), (150, 150, 150)


def decode(clip: Path, first: int, last: int) -> list[np.ndarray]:
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(clip), "-vf",
                          f"select='between(n,{first},{last})',crop={WIDTH}:{HEIGHT}:{LEFT}:0",
                          "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
                         capture_output=True, check=True).stdout
    size = WIDTH * HEIGHT * 3
    return [np.frombuffer(raw[i:i + size], np.uint8).reshape(HEIGHT, WIDTH, 3).copy()
            for i in range(0, len(raw) - size + 1, size)]


def caption(image: np.ndarray, text: str) -> np.ndarray:
    for colour, weight in (((0, 0, 0), 7), ((255, 255, 255), 2)):
        cv2.putText(image, text, (18, HEIGHT - 22), cv2.FONT_HERSHEY_DUPLEX, 1.1, colour, weight, cv2.LINE_AA)
    return image


def marked(frames: list[np.ndarray], index: int, threshold: float) -> tuple[np.ndarray, str, bool]:
    """Frame ``index`` with the flagged blocks outlined, a note how many, and
    whether the repair acts on them."""
    data = [_Frame(frame) for frame in frames[max(0, index - SPAN):index + SPAN + 1]]
    blocks = broken_blocks(data, min(index, SPAN), threshold)
    count = int(blocks.sum())
    note = "whole frame" if blocks.all() else f"{count} blocks"
    shown = frames[index].copy()
    # Below MIN_BLOCKS the frame is left alone; outline those in grey.
    colour = YELLOW if count >= MIN_BLOCKS else GREY
    if blocks.all():
        cv2.rectangle(shown, (2, 2), (WIDTH - 3, HEIGHT - 3), colour, 6)
    else:
        for row, column in zip(*np.nonzero(blocks)):
            cv2.rectangle(shown, (column * BLOCK, row * BLOCK),
                          (column * BLOCK + BLOCK - 1, row * BLOCK + BLOCK - 1), colour, 2)
    return shown, note, count >= MIN_BLOCKS


def stretch(clip: Path, first: int, last: int, amount: int) -> tuple[list[np.ndarray], list[np.ndarray], int]:
    """Original and repaired frames of ``first..last``, decoded with context."""
    start = max(0, first - MARGIN)
    frames = decode(clip, start, last + MARGIN)
    repaired = list(repair_stream(iter(frames), amount))
    return frames, repaired, first - start


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("clip", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--gif", help="FIRST-LAST frame range")
    parser.add_argument("--detect", type=int, nargs="*", default=[])
    parser.add_argument("--pairs", type=int, nargs="*", default=[])
    parser.add_argument("--name", default="hdzero")
    parser.add_argument("--amount", type=int, default=35, help="Block care setting, as in the app")
    parser.add_argument("--gif-width", type=int, default=900)
    parser.add_argument("--gif-fps", type=int, default=8)
    parser.add_argument("--video", help="FIRST-LAST frame range for PREFIX-slow.mp4")
    parser.add_argument("--video-fps", type=float, default=10, help="Frames per second of the slowed video")
    parser.add_argument("--video-hold", type=int, default=1, help="Show each repaired frame this many times")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    threshold = level(args.amount)

    if args.detect:
        tiles = []
        for number in args.detect:
            frames, _, offset = stretch(args.clip, number, number, args.amount)
            shown, note, _ = marked(frames, offset, threshold)
            tiles.append(caption(shown, f"#{number}: {note}"))
        while len(tiles) % 2:
            tiles.append(np.zeros_like(tiles[0]))
        grid = np.vstack([np.hstack(tiles[i:i + 2]) for i in range(0, len(tiles), 2)])
        grid = cv2.resize(grid, (grid.shape[1] * 2 // 3, grid.shape[0] * 2 // 3), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(args.output / f"{args.name}-detection.jpg"), grid, [cv2.IMWRITE_JPEG_QUALITY, 88])

    if args.pairs:
        rows = []
        for number in args.pairs:
            frames, repaired, offset = stretch(args.clip, number, number, args.amount)
            rows.append(np.hstack([caption(frames[offset].copy(), "ORIGINAL"),
                                   caption(repaired[offset].copy(), "REPAIRED")]))
        grid = np.vstack(rows)
        grid = cv2.resize(grid, (grid.shape[1] * 2 // 3, grid.shape[0] * 2 // 3), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(args.output / f"{args.name}-before-after.jpg"), grid, [cv2.IMWRITE_JPEG_QUALITY, 88])

    if args.gif:
        with tempfile.TemporaryDirectory() as folder:
            panels(args, args.gif, threshold, Path(folder), 1)
            graph = (f"scale={args.gif_width}:-1:flags=lanczos,split[a][b];"
                     "[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-framerate", str(args.gif_fps),
                            "-i", str(Path(folder) / "%04d.png"), "-filter_complex", graph, "-loop", "0",
                            str(args.output / f"{args.name}.gif")], check=True)
    if args.video:
        # The same panels as a sharp video for editing, e.g. the intro video.
        with tempfile.TemporaryDirectory() as folder:
            panels(args, args.video, threshold, Path(folder), args.video_hold)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-framerate",
                            str(args.video_fps), "-i", str(Path(folder) / "%04d.png"), "-c:v", "libx264",
                            "-crf", "14", "-preset", "medium", "-pix_fmt", "yuv420p", "-r", "30",
                            str(args.output / f"{args.name}-slow.mp4")], check=True)
    for path in sorted(args.output.glob(f"{args.name}*")):
        print(path, path.stat().st_size // 1024, "KiB")


def panels(args: argparse.Namespace, span: str, threshold: float, folder: Path, hold: int) -> None:
    """Write original | detected | repaired panels of ``span`` (FIRST-LAST) as numbered PNGs;
    repaired frames are written ``hold`` times so they can be seen."""
    first, last = (int(value) for value in span.split("-"))
    frames, repaired, offset = stretch(args.clip, first, last, args.amount)
    step = 0
    for index in range(offset, offset + last - first + 1):
        shown, _, acted = marked(frames, index, threshold)
        panel = np.hstack([caption(frames[index].copy(), "ORIGINAL"), caption(shown, "DETECTED"),
                           caption(repaired[index].copy(), "REPAIRED")])
        for _ in range(hold if acted else 1):
            cv2.imwrite(str(folder / f"{step:04d}.png"), panel)
            step += 1


if __name__ == "__main__":
    main()
