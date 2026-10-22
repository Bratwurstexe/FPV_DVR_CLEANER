"""Full-resolution before/after stills for the README.

Usage: python tools/make_stills.py <folder with NAME.mp4 and NAME-original.mp4> <output folder>

For every pair the frame where the repair changed the picture most is chosen.
The still shows original and result side by side at full size, and below them
a 2x enlarged detail of the area with the largest change. A split "hero"
image (left half original, right half cleaned) is written for the first pair.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

import cv2
import numpy as np

ORANGE = (0, 70, 166)
INK = (43, 37, 32)


def frames(path: Path, size: tuple[int, int] | None = None) -> list[np.ndarray]:
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, check=True).stdout.strip().split(",")
    width, height = int(probe[0]), int(probe[1])
    raw = subprocess.run(["ffmpeg", "-loglevel", "quiet", "-i", str(path), "-f", "rawvideo",
                          "-pix_fmt", "bgr24", "pipe:1"], capture_output=True, check=True).stdout
    count = len(raw) // (width * height * 3)
    result = list(np.frombuffer(raw[:count * width * height * 3], np.uint8).reshape(count, height, width, 3))
    if size and (width, height) != size:
        result = [cv2.resize(frame, size, interpolation=cv2.INTER_AREA) for frame in result]
    return result


def picture_area(frame: np.ndarray) -> np.ndarray:
    """The 4:3 picture of a 16:9 DVR recording."""
    height, width = frame.shape[:2]
    inner = round(height * 4 / 3)
    left = (width - inner) // 2
    return frame[:, left:left + inner]


def change(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    a = cv2.cvtColor(before, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    b = cv2.cvtColor(after, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    # Colour changes count double: they are what the analog repair is about.
    return np.abs(a[:, :, 0] - b[:, :, 0]) + 2 * np.abs(a[:, :, 1:] - b[:, :, 1:]).sum(axis=2)


def tag(image: np.ndarray, text: str, right: bool = False, accent: bool = False) -> None:
    scale, thick = image.shape[0] / 900, max(1, round(image.shape[0] / 450))
    (w, h), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, thick)
    x = image.shape[1] - w - 28 if right else 18
    y = image.shape[0] - 22
    cv2.rectangle(image, (x - 10, y - h - 12), (x + w + 10, y + base + 6), ORANGE if accent else INK, -1)
    cv2.putText(image, text, (x, y), cv2.FONT_HERSHEY_DUPLEX, scale, (255, 255, 255), thick, cv2.LINE_AA)


def showcase(before: list[np.ndarray], after: list[np.ndarray], target: Path, title: str,
             pick: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    if pick is None:
        scores = [float(change(b, a).mean()) for b, a in zip(before, after)]
        pick = int(np.argmax(scores[2:-2])) + 2 if len(scores) > 4 else int(np.argmax(scores))
    left, right = picture_area(before[pick]).copy(), picture_area(after[pick]).copy()
    height, width = left.shape[:2]
    # Detail: the window with the largest summed change, away from the frame edge.
    heat = cv2.blur(change(left, right), (121, 81))
    heat[:60], heat[-60:], heat[:, :80], heat[:, -80:] = 0, 0, 0, 0
    cy, cx = np.unravel_index(int(np.argmax(heat)), heat.shape)
    box_w, box_h = width // 3, height // 3
    x0 = int(np.clip(cx - box_w // 2, 0, width - box_w))
    y0 = int(np.clip(cy - box_h // 2, 0, height - box_h))
    zoom = [cv2.resize(img[y0:y0 + box_h, x0:x0 + box_w], (width, round(box_h * width / box_w)),
                       interpolation=cv2.INTER_CUBIC) for img in (left, right)]
    for img in (left, right):
        cv2.rectangle(img, (x0, y0), (x0 + box_w, y0 + box_h), (60, 160, 255), 2)
    tag(left, "ORIGINAL"); tag(right, "CLEANED", right=True, accent=True)
    tag(zoom[0], "ORIGINAL  2x"); tag(zoom[1], "CLEANED  2x", right=True, accent=True)
    gap = np.full((height, 8, 3), 245, np.uint8)
    top = np.hstack([left, gap, right])
    bottom = np.hstack([zoom[0], np.full((zoom[0].shape[0], 8, 3), 245, np.uint8), zoom[1]])
    header = np.full((70, top.shape[1], 3), (242, 246, 247), np.uint8)
    cv2.putText(header, title, (18, 48), cv2.FONT_HERSHEY_DUPLEX, 1.1, INK, 2, cv2.LINE_AA)
    sheet = np.vstack([header, top, np.full((8, top.shape[1], 3), 245, np.uint8), bottom])
    cv2.imwrite(str(target), sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return picture_area(before[pick]), picture_area(after[pick])


def hero(before: np.ndarray, after: np.ndarray, target: Path) -> None:
    """One frame, split down the middle: original left, cleaned right."""
    width = before.shape[1]
    mix = before.copy()
    mix[:, width // 2:] = after[:, width // 2:]
    mix[:, width // 2 - 2:width // 2 + 2] = (27, 119, 239)
    tag(mix, "ORIGINAL"); tag(mix, "CLEANED", right=True, accent=True)
    cv2.imwrite(str(target), mix, [cv2.IMWRITE_JPEG_QUALITY, 90])


# Clips where the frame with the largest change is not representative.
MOMENT = {"c_osd_removed": 3.3}

TITLES = {
    "a_harburg_colour": "Colour noise, rainbow streaks and grain",
    "b_bremerhaven_rainbow": "Rainbow stripes from a disturbed colour carrier",
    "c_osd_removed": "OSD removal: text, icons and bars",
    "d_upscaled": "Multi-frame upscaling to 1080p with noise reduction",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    made_hero = False
    for cleaned in sorted(args.folder.glob("*.mp4")):
        original = cleaned.with_name(f"{cleaned.stem}-original.mp4")
        if cleaned.stem.endswith(("-original", "-compare")) or not original.exists():
            continue
        after = frames(cleaned)
        before = frames(original, (after[0].shape[1], after[0].shape[0]))
        count = min(len(before), len(after))
        target = args.output / f"{cleaned.stem.split('_', 1)[1].replace('_', '-')}-before-after.jpg"
        rate = (subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                         "stream=r_frame_rate", "-of", "csv=p=0", str(cleaned)],
                                        capture_output=True, text=True).stdout.strip() or "0/1").split("/")
        fps = float(rate[0]) / float(rate[1]) if float(rate[1]) else 0.0
        moment = MOMENT.get(cleaned.stem)
        pick = min(count - 1, round(moment * fps)) if moment is not None and fps else None
        pair = showcase(before[:count], after[:count], target, TITLES.get(cleaned.stem, cleaned.stem), pick)
        print(target, target.stat().st_size // 1024, "KiB")
        if not made_hero:
            hero(*pair, args.output / "hero-split.jpg")
            made_hero = True


if __name__ == "__main__":
    main()
