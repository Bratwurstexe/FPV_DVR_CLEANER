"""Share-ready before/after videos of a finished export.

The original recording and the cleaned file are read side by side, frame by
frame, and composed into a new video: both pictures next to each other, under
a split line, or in alternating cuts; 16:9 for YouTube or 9:16 for Shorts,
Reels and TikTok.

Pairing follows the export: the cleaned file is the clock, and its frame ``k``
shows the source frame that the kept parts of the trim (in order, each
``round(length * fps)`` frames long) put there. The original is decoded part
by part with the same seek the export used, so both pictures stay on the same
moment, including across removed parts.

Where the repair changed one frame far more than the frames around it, a
breakup or a tear was rebuilt. Those frames can be shown slowed down and
frozen for a moment. "Best N seconds" picks the stretch of the clip that holds
the strongest of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from typing import Callable, Iterator
import wave

import cv2
import numpy as np

from .media import PROCESS_FLAGS, VideoKenndaten, inspect_video
from .processor import Laufergebnis, diagnostic_tail, encoding_options, find_asset, temporary_output
from .recipe import Arbeitsrezept
from .restoration import _encoder, _placement, picture_box
from .segments import Segment, normalize_segments

# Layouts per format: next to each other, under a split line, alternating cuts; stacked for 9:16.
LAYOUTS = {"landscape": ("side", "wipe", "flip"), "vertical": ("stacked", "wipe")}
LENGTHS = (0, 15, 30, 60)
SUFFIXES = {"landscape": "-share", "vertical": "-short"}

SLOW_FACTOR = 0.25
SLOW_BEFORE = 0.3      # source seconds played slowly before a repaired frame ...
SLOW_AFTER = 0.3       # ... and after it
FREEZE_SECONDS = 1.0   # how long the repaired frame holds still
FLIP_SECONDS = 2.0     # alternating cuts: seconds per side
WIPE_PERIOD = 8.0      # seconds for the moving split line to swing there and back
PEAK_SEPARATION = 2.5  # seconds between two highlighted repairs
MIN_PEAK = 6.0         # grey levels a repaired frame must stand out from its neighbours
ANALYSIS_WIDTH = 160
AUDIO_RATE = 48000

# Colours (BGR): the orange split line of the result view, the brand orange and the charcoal ground.
LINE = (58, 138, 240)
BRAND = (0, 70, 166)
INK = (74, 66, 58)
GROUND = (29, 25, 21)
WHITE = (255, 255, 255)

WATERMARK = "Cleaned with FPV DVR Cleaner"


@dataclass(frozen=True)
class Teilauftrag:
    """What the share video should look like."""

    format: str = "landscape"
    layout: str = "side"
    length: int = 30           # 0: the whole clip, otherwise the best N seconds
    highlights: bool = True    # slow motion and a frozen frame on the strongest repairs
    sound: bool = True
    watermark: bool = True
    wipe: str = "moving"       # the split line sweeps, or stays in the middle ("fixed")
    effort: str = "best"       # encoder speed, as on the Repair page

    def validate(self) -> None:
        if self.format not in LAYOUTS:
            raise ValueError("Choose 16:9 or 9:16")
        if self.layout not in LAYOUTS[self.format]:
            raise ValueError(f"The layout {self.layout!r} is not available for this format")
        if self.length not in LENGTHS:
            raise ValueError("Length must be the whole clip or 15, 30 or 60 seconds")
        for name in ("highlights", "sound", "watermark"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be on or off")
        if self.wipe not in {"moving", "fixed"}:
            raise ValueError("The split line is moving or fixed")
        if self.effort not in {"fast", "balanced", "best"}:
            raise ValueError("Unknown processing effort")

    @classmethod
    def of(cls, settings: dict) -> Teilauftrag:
        known = {key: settings[key] for key in cls.__dataclass_fields__ if key in settings}
        if "length" in known:
            known["length"] = int(known["length"])
        job = cls(**known)
        job.validate()
        return job


def output_size(job: Teilauftrag, picture_height: int) -> tuple[int, int]:
    if job.format == "vertical":
        return 1080, 1920
    # Small sources (SD) are not blown up to 1080p; 720p is plenty for them.
    return (1920, 1080) if picture_height >= 600 else (1280, 720)


def output_rate(fps: float) -> float:
    """30 or 60 fps style: the source rate up to 60; faster recordings are halved or thirded
    when that lands on 48..60 fps, otherwise shown at 60 fps."""
    if fps <= 0:
        return 30.0
    if fps <= 60.5:
        return 30.0 if fps < 23.5 else fps
    for divisor in range(2, 9):
        rate = fps / divisor
        if rate <= 60.5:
            return rate if rate >= 47.5 else 60.0
    return 60.0


# ---------------------------------------------------------------- pairing original and cleaned

def segment_frames(segments: list[Segment], fps: float) -> list[int]:
    """Frames each kept part became in the export (see process_temporal)."""
    return [max(1, round((end - start) * fps)) for start, end in segments]


def source_pieces(segments: list[Segment], fps: float, first: int, last: int) -> list[tuple[float, float, int]]:
    """Parts of the original (start, seconds, frames) that make up cleaned frames ``first..last-1``."""
    pieces = []
    offset = 0
    for (start, _), count in zip(segments, segment_frames(segments, fps)):
        low, high = max(first, offset), min(last, offset + count)
        if high > low:
            pieces.append((start + (low - offset) / fps, (high - low) / fps, high - low))
        offset += count
    if last > offset and pieces:
        # The cleaned file can run a frame or two longer; the last part covers it.
        start, seconds, count = pieces[-1]
        pieces[-1] = (start, seconds + (last - offset) / fps, count + last - offset)
    return pieces


def source_time(segments: list[Segment], fps: float, index: int) -> float:
    """The moment of the original that cleaned frame ``index`` shows."""
    pieces = source_pieces(segments, fps, index, index + 1)
    return pieces[0][0] if pieces else segments[-1][1]


def _decode(source: Path, pieces: list[tuple[float, float, int]], fps: float,
            box: tuple[int, int, int, int], size: tuple[int, int] | None = None) -> Iterator[np.ndarray]:
    """Frames of ``source`` piece by piece, cropped to ``box`` and optionally scaled to ``size``.

    Every piece delivers exactly its frame count: a short decode repeats its last
    frame, a long one is cut, so the pairing never drifts.
    """
    x, y, width, height = box
    out_width, out_height = size or (width, height)
    frame_bytes = out_width * out_height * 3
    last = None
    for start, seconds, count in pieces:
        args = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error"]
        if start > 0:
            args.extend(["-ss", f"{start:.6f}"])
        chain = f"crop={width}:{height}:{x}:{y}"
        if size:
            chain += f",scale={out_width}:{out_height}:flags=area"
        args.extend(["-i", str(source), "-t", f"{seconds + 0.5 / fps:.6f}", "-map", "0:v:0", "-an", "-sn",
                     "-vf", chain, "-r", f"{fps:.6f}", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"])
        decoder = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   creationflags=PROCESS_FLAGS)
        try:
            assert decoder.stdout is not None
            for _ in range(count):
                raw = decoder.stdout.read(frame_bytes)
                if len(raw) == frame_bytes:
                    last = np.frombuffer(raw, np.uint8).reshape(out_height, out_width, 3)
                elif last is None:
                    last = np.zeros((out_height, out_width, 3), np.uint8)
                yield last
        finally:
            decoder.stdout.close()
            if decoder.poll() is None:
                decoder.kill()
            decoder.wait()


class _Reader:
    """Random access for a non-decreasing frame index over a sequential decoder."""

    def __init__(self, frames: Iterator[np.ndarray]):
        self.frames = frames
        self.position = -1
        self.frame: np.ndarray | None = None

    def get(self, index: int) -> np.ndarray:
        while self.position < index:
            following = next(self.frames, None)
            if following is None:
                break
            self.frame = following
            self.position += 1
        assert self.frame is not None
        return self.frame

    def close(self) -> None:
        close = getattr(self.frames, "close", None)
        if close:
            close()


def inset(box: tuple[int, int, int, int], fraction: float = 0.01) -> tuple[int, int, int, int]:
    """The box a hair smaller on every side: edges of a DVR picture often carry a dark or bright line."""
    x, y, width, height = box
    dx = max(2, _even(width * fraction)) if width > 64 else 0
    dy = max(2, _even(height * fraction)) if height > 64 else 0
    return x + dx, y + dy, width - 2 * dx, height - 2 * dy


def picture_boxes(original: Path, info: VideoKenndaten, cleaned: Path, made: VideoKenndaten,
                  segments: list[Segment]) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """The picture inside black bars in the original and in the cleaned file.

    The export places the picture exactly (see restoration._placement), so the
    cleaned box follows from the original one when the size is a known factor.
    """
    span = segments[-1][1] - segments[0][0]
    box = picture_box(original, segments[0][0], max(0.2, span), info.width, info.height)
    for percent in (100, 150, 200):
        inner, full, offset = _placement(box, info.width, info.height, percent)
        if full == (made.width, made.height):
            return inset(box), inset((offset[0], offset[1], inner[0], inner[1]))
    return inset(box), inset(picture_box(cleaned, 0.0, made.duration, made.width, made.height))


# ---------------------------------------------------------------- what the repair changed

@dataclass
class Messung:
    """Per cleaned frame: how much the repair changed it, and where the motion is."""

    difference: np.ndarray       # mean absolute difference original vs cleaned, grey levels
    centre: np.ndarray           # horizontal centre of motion, 0..1 of the picture width (nan: none)
    fps: float

    @property
    def count(self) -> int:
        return len(self.difference)


def measure(original: Iterator[np.ndarray], cleaned: Iterator[np.ndarray], count: int, fps: float,
            report: Callable[[float], None] = lambda fraction: None,
            cancel: threading.Event | None = None) -> Messung:
    """Compare small copies of both pictures frame by frame."""
    difference = []
    centre = []
    previous = None
    for index, (before, after) in enumerate(zip(original, cleaned)):
        if cancel is not None and cancel.is_set():
            break
        difference.append(float(np.abs(before.astype(np.int16) - after.astype(np.int16)).mean()))
        grey = cv2.cvtColor(after, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if previous is None:
            centre.append(math.nan)
        else:
            motion = np.abs(grey - previous)
            # Sensor noise and compression flicker are everywhere; only what stands out counts.
            motion = np.maximum(motion - float(np.median(motion)) - 2.0, 0.0)
            columns = motion.sum(axis=0)
            total = float(columns.sum())
            centre.append(float((columns * (np.arange(len(columns)) + 0.5)).sum() / total / len(columns))
                          if total > motion.shape[0] * 0.5 else math.nan)
        previous = grey
        if index % 30 == 0:
            report(min(1.0, (index + 1) / max(1, count)))
    return Messung(np.array(difference, np.float32), np.array(centre, np.float32), fps)


def _running_median(values: np.ndarray, radius: int) -> np.ndarray:
    padded = np.pad(values, radius, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * radius + 1)
    return np.median(windows, axis=1)


def repair_peaks(difference: np.ndarray, fps: float, separation: float = PEAK_SEPARATION
                 ) -> list[tuple[int, float]]:
    """Frames the repair changed far more than their neighbours: (index, strength), strongest first.

    A breakup or a tear is a single-frame event; steady changes such as noise
    reduction or upscaling move the running median along and do not count.
    """
    if len(difference) < 5:
        return []
    radius = max(4, round(fps * 0.25))
    spike = difference - _running_median(difference, radius)
    spread = 1.4826 * float(np.median(np.abs(spike - np.median(spike))))
    threshold = max(MIN_PEAK, 6 * spread)
    gap = max(1, round(separation * fps))
    chosen: list[tuple[int, float]] = []
    for index in np.argsort(-spike, kind="stable"):
        strength = float(spike[index])
        if strength < threshold:
            break
        if all(abs(int(index) - other) >= gap for other, _ in chosen):
            chosen.append((int(index), strength))
    return chosen


def extra_seconds() -> float:
    """Time one highlight adds: the slowed stretch and the frozen frame."""
    return (SLOW_BEFORE + SLOW_AFTER) * (1 / SLOW_FACTOR - 1) + FREEZE_SECONDS


def highlight_limit(seconds: float) -> int:
    return max(1, min(6, round(seconds / 12)))


def best_window(difference: np.ndarray, peaks: list[tuple[int, float]], fps: float, seconds: float,
                highlights: bool) -> tuple[int, int]:
    """Cleaned frames ``first..last-1`` of the best ``seconds``: around the strongest repairs.

    With highlights on, the window is shortened by the time their slow motion
    adds, so the finished video stays close to ``seconds``.
    """
    count = len(difference)
    target = max(1, round(seconds * fps))
    if count <= target:
        return 0, count
    cumulative = np.concatenate([[0.0], np.cumsum(difference, dtype=np.float64)])
    step = max(1, round(fps / 10))

    def pick(length: int) -> tuple[int, list[tuple[int, float]]]:
        margin = min(round(fps), length // 4)
        best, best_score, best_inside = 0, -math.inf, []
        for first in range(0, count - length + 1, step):
            inside = [(index, strength) for index, strength in peaks
                      if first + margin <= index < first + length - margin]
            inside.sort(key=lambda item: -item[1])
            inside = inside[:highlight_limit(seconds)]
            # Repairs count most; the overall change breaks ties and decides without any.
            steady = (cumulative[first + length] - cumulative[first]) / length
            score = sum(strength for _, strength in inside) * 1000 + steady
            if inside:
                # Prefer the repairs near the middle, away from the cuts.
                middle = first + length / 2
                score -= 10 * sum(abs(index - middle) for index, _ in inside) / length
            if score > best_score:
                best, best_score, best_inside = first, score, inside
        return best, best_inside

    length = target
    first, inside = pick(length)
    if highlights and inside:
        shorter = max(target // 2, target - round(len(inside) * extra_seconds() * fps))
        first, _ = pick(shorter)
        length = shorter
    return first, first + length


def find_repairs(original: Path, cleaned: Path, segments: object = None) -> list[float]:
    """Cleaned-file seconds of the frames the repair changed most, strongest first."""
    info, made = inspect_video(original), inspect_video(cleaned)
    fps = made.fps or info.fps
    parts = normalize_segments(segments, info.duration)
    count = min(max(1, round(made.duration * fps)), sum(segment_frames(parts, fps)) + round(fps * 0.1))
    box, made_box = picture_boxes(original, info, cleaned, made, parts)
    measured = _measure_pair(original, cleaned, parts, fps, count, box, made_box)
    return [index / fps for index, _ in repair_peaks(measured.difference, fps)]


def _measure_pair(original: Path, cleaned: Path, parts: list[Segment], fps: float, count: int,
                  box: tuple[int, int, int, int], made_box: tuple[int, int, int, int],
                  report: Callable[[float], None] = lambda fraction: None,
                  cancel: threading.Event | None = None) -> Messung:
    aspect = made_box[2] / made_box[3]
    small = (ANALYSIS_WIDTH, max(2, _even(ANALYSIS_WIDTH / aspect)))
    return measure(_decode(original, source_pieces(parts, fps, 0, count), fps, box, small),
                   _decode(cleaned, [(0.0, count / fps, count)], fps, made_box, small),
                   count, fps, report, cancel)


# ---------------------------------------------------------------- horizontal pan for 9:16

def smooth_pan(centre: np.ndarray, fps: float, window: float) -> np.ndarray:
    """Centre of a crop ``window`` wide (fraction of the picture) per frame, following the motion.

    The raw centre of motion jitters from frame to frame. It is pulled towards
    the middle, median filtered, smoothed in both directions, limited in speed
    and kept so that the crop never leaves the picture.
    """
    count = len(centre)
    low, high = window / 2, 1 - window / 2
    if count == 0:
        return np.zeros(0, np.float32)
    if high <= low:
        return np.full(count, 0.5, np.float32)
    values = np.where(np.isfinite(centre), centre, 0.5).astype(np.float64)
    values = 0.5 + 0.7 * (values - 0.5)
    values = _running_median(values, max(1, round(fps * 0.5)))
    # Kept inside before smoothing: every later step only averages or slows, so it stays inside
    # and never stops hard at the edge.
    values = np.clip(values, low, high)

    def both_ways(series: np.ndarray, seconds: float) -> np.ndarray:
        weight = 1 - math.exp(-1 / max(1.0, seconds * fps))
        out = series.copy()
        for direction in (1, -1):
            out = out[::direction]
            for index in range(1, count):
                out[index] = out[index - 1] + weight * (out[index] - out[index - 1])
            out = out[::direction]
        return out

    values = both_ways(values, 0.8)
    speed = 0.15 / fps  # at most 15 % of the picture width per second
    for order in (range(1, count), range(count - 2, -1, -1)):
        for index in order:
            before = values[index - 1] if order.step == 1 else values[index + 1]
            values[index] = before + np.clip(values[index] - before, -speed, speed)
    values = both_ways(values, 0.5)
    return np.clip(values, low, high).astype(np.float32)


def crop_span(width: int, crop: int, centre: float) -> int:
    """Left edge of a ``crop`` wide window around ``centre`` (0..1), inside ``width``."""
    return int(np.clip(round(centre * width - crop / 2), 0, max(0, width - crop)))


# ---------------------------------------------------------------- the frame plan

@dataclass
class Zeitplan:
    """Every output frame: the cleaned frame it shows and how (play, slow, freeze)."""

    fps: float
    indices: list[int] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)
    # Runs of output frames for the sound: (cleaned frame where it starts or None for silence, frames).
    runs: list[tuple[float | None, int]] = field(default_factory=list)

    def add(self, index: int, mode: str, position: float) -> None:
        self.indices.append(index)
        self.modes.append(mode)
        sounding = position if mode == "play" else None
        if self.runs and (self.runs[-1][0] is None) == (sounding is None):
            self.runs[-1] = (self.runs[-1][0], self.runs[-1][1] + 1)
        else:
            self.runs.append((sounding, 1))

    @property
    def duration(self) -> float:
        return len(self.indices) / self.fps


def frame_plan(first: int, last: int, source_fps: float, fps: float, highlights: list[int]) -> Zeitplan:
    """Normal speed, slowed around each highlight, frozen on the highlighted frame itself."""
    plan = Zeitplan(fps)
    step = source_fps / fps
    spans = [(peak - SLOW_BEFORE * source_fps, peak + SLOW_AFTER * source_fps, peak)
             for peak in sorted(highlights) if first <= peak < last]
    frozen: set[int] = set()
    position = float(first)
    while position < last - 1e-6:
        span = next((item for item in spans if item[0] <= position < item[1]), None)
        if span is not None and span[2] not in frozen and position >= span[2] - 0.5:
            frozen.add(span[2])
            for _ in range(max(1, round(FREEZE_SECONDS * fps))):
                plan.add(span[2], "freeze", position)
            continue
        plan.add(min(last - 1, int(round(position))), "slow" if span else "play", position)
        position += step * (SLOW_FACTOR if span else 1.0)
    return plan


def tag_opacity(modes: list[str], fps: float) -> np.ndarray:
    """Slow-motion tags fade in and out over a few frames next to normal playback."""
    count = len(modes)
    distance = np.full(count, 1e9)
    last = -1e9
    for index, mode in enumerate(modes):
        if mode == "play":
            last = index
        distance[index] = index - last
    last = 1e9
    for index in range(count - 1, -1, -1):
        if modes[index] == "play":
            last = index
        distance[index] = min(distance[index], last - index)
    return np.clip(distance / max(1.0, 0.15 * fps), 0, 1)


# ---------------------------------------------------------------- text and drawing

class _Lettering:
    """Text masks in the bundled Inter typeface, drawn once by FFmpeg's drawtext.

    Without drawtext (an FFmpeg build without FreeType) OpenCV's own font stands in.
    """

    def __init__(self) -> None:
        self.workspace = tempfile.TemporaryDirectory(prefix="fpv-share-")
        self.folder = Path(self.workspace.name)
        try:
            shutil.copyfile(find_asset("Inter.ttf"), self.folder / "Inter.ttf")
            self.font = True
        except (FileNotFoundError, OSError):
            self.font = False

    def close(self) -> None:
        self.workspace.cleanup()

    def masks(self, lines: list[tuple[str, int, int]]) -> list[np.ndarray]:
        """Alpha 0..1 of each (text, size in px, stroke weight), cropped to the ink.

        All lines are drawn in one FFmpeg run, one band each, since starting
        FFmpeg costs more than drawing.
        """
        drawn = self._drawtext(lines) if self.font else None
        result = []
        for index, (text, size, _) in enumerate(lines):
            mask = drawn[index] if drawn is not None else None
            if mask is None or mask.max() < 0.5:
                mask = self._opencv(text, size)
            rows = np.flatnonzero(mask.max(axis=1) > 0.02)
            columns = np.flatnonzero(mask.max(axis=0) > 0.02)
            if not len(rows) or not len(columns):
                result.append(np.zeros((max(1, size // 2), max(1, size // 2)), np.float32))
                continue
            result.append(np.ascontiguousarray(mask[rows[0]:rows[-1] + 1, columns[0]:columns[-1] + 1]))
        return result

    def mask(self, text: str, size: int, weight: int = 1) -> np.ndarray:
        return self.masks([(text, size, weight)])[0]

    def _drawtext(self, lines: list[tuple[str, int, int]]) -> list[np.ndarray] | None:
        width = max(int(len(text) * size * 0.8) + 4 * size for text, size, _ in lines)
        bands = [int(size * 2) for _, size, _ in lines]
        width += width % 2
        height = sum(bands) + sum(bands) % 2
        chain = []
        top = 0
        for index, ((text, size, weight), band) in enumerate(zip(lines, bands)):
            (self.folder / f"label{index}.txt").write_text(text, encoding="utf-8")
            chain.append(f"drawtext=fontfile=Inter.ttf:textfile=label{index}.txt:expansion=none:fontsize={size}"
                         f":fontcolor=white:borderw={weight}:bordercolor=white:x={size}:y={top + size // 3}")
            top += band
        args = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "lavfi",
                "-i", f"color=c=black:s={width}x{height}", "-frames:v", "1", "-vf", ",".join(chain),
                "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
        try:
            raw = subprocess.run(args, capture_output=True, timeout=60, cwd=self.folder,
                                 creationflags=PROCESS_FLAGS).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        if len(raw) != width * height:
            return None
        grey = np.frombuffer(raw, np.uint8).reshape(height, width).astype(np.float32)
        low, high = float(grey.min()), float(grey.max())
        if high - low < 64:
            return None
        grey = (grey - low) / (high - low)
        starts = np.cumsum([0] + bands)
        return [grey[start:end] for start, end in zip(starts[:-1], starts[1:])]

    @staticmethod
    def _opencv(text: str, size: int) -> np.ndarray:
        text = text.replace("×", "x")
        font = cv2.FONT_HERSHEY_DUPLEX
        scale = size / 30
        thickness = max(1, round(size / 18))
        (width, height), base = cv2.getTextSize(text, font, scale, thickness)
        canvas = np.zeros((height + base + 8, width + 8), np.uint8)
        cv2.putText(canvas, text, (4, height + 4), font, scale, 255, thickness, cv2.LINE_AA)
        return canvas.astype(np.float32) / 255


@dataclass
class _Sprite:
    """Premultiplied colour and coverage of a small overlay."""

    colour: np.ndarray   # float32 (h, w, 3), already multiplied by alpha
    alpha: np.ndarray    # float32 (h, w)

    @property
    def size(self) -> tuple[int, int]:
        return self.alpha.shape[1], self.alpha.shape[0]

    def draw(self, frame: np.ndarray, x: int, y: int, opacity: float = 1.0) -> None:
        if opacity <= 0.01:
            return
        width, height = self.size
        left, top = max(0, x), max(0, y)
        right, bottom = min(frame.shape[1], x + width), min(frame.shape[0], y + height)
        if right <= left or bottom <= top:
            return
        part = frame[top:bottom, left:right].astype(np.float32)
        alpha = self.alpha[top - y:bottom - y, left - x:right - x, None] * opacity
        colour = self.colour[top - y:bottom - y, left - x:right - x] * opacity
        frame[top:bottom, left:right] = np.clip(part * (1 - alpha) + colour + 0.5, 0, 255).astype(np.uint8)


def _rounded(width: int, height: int, radius: float) -> np.ndarray:
    """Anti-aliased coverage of a rounded rectangle."""
    y, x = np.mgrid[0:height, 0:width].astype(np.float32) + 0.5
    dx = np.maximum(np.abs(x - width / 2) - (width / 2 - radius), 0)
    dy = np.maximum(np.abs(y - height / 2) - (height / 2 - radius), 0)
    return np.clip(radius - np.hypot(dx, dy) + 0.5, 0, 1).astype(np.float32)


def pill(text: np.ndarray, background: tuple[int, int, int], opacity: float) -> _Sprite:
    """A label: white text on a rounded, slightly transparent badge."""
    text_height, text_width = text.shape
    pad_y = max(4, round(text_height * 0.62))
    pad_x = max(6, round(text_height * 0.95))
    height, width = text_height + 2 * pad_y, text_width + 2 * pad_x
    badge = _rounded(width, height, height * 0.3) * opacity
    ink = np.zeros((height, width), np.float32)
    ink[pad_y:pad_y + text_height, pad_x:pad_x + text_width] = text
    alpha = ink + badge * (1 - ink)
    colour = (ink[..., None] * np.array(WHITE, np.float32)
              + (badge * (1 - ink))[..., None] * np.array(background, np.float32))
    return _Sprite(colour, alpha)


def shadowed(text: np.ndarray, strength: float = 0.82) -> _Sprite:
    """Plain white text with a soft shadow, for the watermark."""
    pad = max(3, text.shape[0] // 3)
    ink = np.pad(text, pad)
    shadow = cv2.GaussianBlur(np.roll(ink, (1, 1), axis=(0, 1)), (0, 0), max(1.0, pad / 2.5)) * 0.55
    ink = ink * strength
    alpha = ink + shadow * (1 - ink)
    colour = ink[..., None] * np.array(WHITE, np.float32)
    return _Sprite(colour, alpha)


def split_line(frame: np.ndarray, x: float, top: int, bottom: int, scale: float) -> None:
    """The split line in orange with a soft glow, at sub-pixel position ``x``."""
    reach = max(8, round(40 * scale))
    left, right = max(0, int(x) - reach), min(frame.shape[1], int(x) + reach + 1)
    if right <= left:
        return
    offset = np.arange(left, right, dtype=np.float32) + 0.5 - x
    core = np.clip(1.6 * scale + 0.5 - np.abs(offset), 0, 1)
    glow = 0.5 * np.exp(-(offset / (11 * scale)) ** 2)
    band = frame[top:bottom, left:right].astype(np.float32)
    orange = np.array(LINE, np.float32)
    # Screen blend for the glow brightens like light; the core line is drawn over it.
    band = 255 - (255 - band) * (1 - glow[None, :, None] * orange / 255)
    band = band * (1 - core[None, :, None]) + orange * core[None, :, None]
    frame[top:bottom, left:right] = np.clip(band + 0.5, 0, 255).astype(np.uint8)


def fit(picture: np.ndarray, width: int, height: int) -> np.ndarray:
    if picture.shape[1] == width and picture.shape[0] == height:
        return picture
    shrink = picture.shape[1] > width
    return cv2.resize(picture, (width, height), interpolation=cv2.INTER_AREA if shrink else cv2.INTER_CUBIC)


def _even(value: float) -> int:
    return int(round(value / 2) * 2)


# ---------------------------------------------------------------- layouts

class _Composer:
    """Draws one output frame from the original and the cleaned picture."""

    def __init__(self, job: Teilauftrag, size: tuple[int, int], aspect: float, letters: _Lettering):
        self.job = job
        self.width, self.height = size
        self.aspect = aspect
        self.unit = min(size) / 1080
        unit = self.unit
        self.margin = round(28 * unit)
        label, tag, mark = round(26 * unit), round(24 * unit), round(21 * unit)
        original, cleaned, slow, frozen, whole, upper, lower = letters.masks([
            ("ORIGINAL", label, 1), ("CLEANED", label, 1), (f"SLOW MOTION ×{SLOW_FACTOR:g}", tag, 1),
            ("FROZEN FRAME", tag, 1), (WATERMARK, mark, 0), ("Cleaned with", mark, 0), ("FPV DVR Cleaner", mark, 0)])
        self.original = pill(original, INK, 0.78)
        self.cleaned = pill(cleaned, BRAND, 0.86)
        self.tags = {"slow": pill(slow, INK, 0.8), "freeze": pill(frozen, BRAND, 0.86)}
        self.mark = shadowed(whole) if job.watermark else None
        self.mark_lines = None
        if job.watermark:
            # Stacked in two lines, right-aligned, for the side bars next to a 4:3 picture.
            gap = round(mark * 0.45)
            both = np.zeros((upper.shape[0] + gap + lower.shape[0], max(upper.shape[1], lower.shape[1])), np.float32)
            both[:upper.shape[0], both.shape[1] - upper.shape[1]:] = upper
            both[upper.shape[0] + gap:, both.shape[1] - lower.shape[1]:] = lower
            self.mark_lines = shadowed(both)
        self.ground = np.empty((self.height, self.width, 3), np.uint8)
        self.ground[:] = GROUND
        self.in_bars = False
        self._layout()

    def _layout(self) -> None:
        width, height, unit, aspect = self.width, self.height, self.unit, self.aspect
        job = self.job
        if job.format == "landscape" and job.layout == "side":
            gap, outer = round(16 * unit), round(24 * unit)
            picture_width = min((width - 2 * outer - gap) / 2, (height - 2 * round(150 * unit)) * aspect)
            self.picture = (_even(picture_width), _even(picture_width / aspect))
            total = 2 * self.picture[0] + gap
            left = (width - total) // 2
            top = _even((height - self.picture[1]) / 2)
            self.places = [(left, top), (left + self.picture[0] + gap, top)]
            self.crop = 1.0
        elif job.format == "landscape":
            picture_width = min(width, height * aspect)
            self.picture = (_even(picture_width), _even(picture_width / aspect))
            self.places = [((width - self.picture[0]) // 2, (height - self.picture[1]) // 2)]
            self.crop = 1.0
            # A 4:3 picture leaves bars at the sides: labels and watermark go there, off the footage.
            bar = self.places[0][0]
            self.in_bars = bar >= max(self.original.size[0], self.cleaned.size[0]) + 2 * self.margin
        elif job.layout == "stacked":
            # Each picture as wide as the frame; wider than 4:3 is cropped to 4:3, following the motion.
            shown = min(aspect, 4 / 3)
            gap = round(14 * unit)
            picture_height = min(width / shown, (height - gap - 2 * round(110 * unit)) / 2)
            self.picture = (_even(picture_height * shown), _even(picture_height))
            top = (height - 2 * self.picture[1] - gap) // 2
            left = (width - self.picture[0]) // 2
            self.places = [(left, top), (left, top + self.picture[1] + gap)]
            self.crop = shown / aspect
        else:
            # One full-height picture, cropped to 9:16 around the motion.
            self.picture = (width, height)
            self.places = [(0, 0)]
            self.crop = min(1.0, (width / height) / aspect)

    def _cut(self, picture: np.ndarray, centre: float) -> np.ndarray:
        if self.crop >= 0.999:
            return picture
        crop = max(2, round(picture.shape[1] * self.crop))
        left = crop_span(picture.shape[1], crop, centre)
        return picture[:, left:left + crop]

    def _wipe_position(self, clock: float) -> float:
        if self.job.wipe == "fixed":
            return 0.5
        return 0.5 + 0.3 * math.sin(2 * math.pi * clock / WIPE_PERIOD)

    def compose(self, before: np.ndarray, after: np.ndarray, clock: float, mode: str, local: float,
                centre: float, tag_alpha: float) -> np.ndarray:
        job, unit, margin = self.job, self.unit, self.margin
        frame = self.ground.copy()
        width, height = self.picture
        before = fit(self._cut(before, centre), width, height)
        after = fit(self._cut(after, centre), width, height)
        tag_anchor: tuple[int, int]
        if job.layout in {"side", "stacked"}:
            (x1, y1), (x2, y2) = self.places
            frame[y1:y1 + height, x1:x1 + width] = before
            frame[y2:y2 + height, x2:x2 + width] = after
            if job.layout == "side":
                for sprite, x in ((self.original, x1), (self.cleaned, x2)):
                    label_width, label_height = sprite.size
                    sprite.draw(frame, x + (width - label_width) // 2,
                                max(margin // 2, y1 - label_height - round(20 * unit)))
                tag_anchor = (self.width // 2, y1 + height + round(26 * unit))
            else:
                self.original.draw(frame, x1 + margin, y1 + margin)
                self.cleaned.draw(frame, x2 + margin, y2 + margin)
                tag_anchor = (self.width // 2, y2 - (y2 - y1 - height) // 2)
        elif job.layout == "wipe":
            x0, y0 = self.places[0]
            edge = x0 + width * self._wipe_position(clock)
            cut = int(round(edge)) - x0
            region = frame[y0:y0 + height, x0:x0 + width]
            region[:, :cut] = before[:, :cut]
            region[:, cut:] = after[:, cut:]
            split_line(frame, edge, y0, y0 + height, unit)
            label_y = y0 + (round(0.105 * self.height) if job.format == "vertical" else margin)
            if self.in_bars:
                self._bar_labels(frame, self.original, self.cleaned)
            else:
                for sprite, side_width, x in ((self.original, edge - x0, x0 + margin),
                                              (self.cleaned, x0 + width - edge,
                                               x0 + width - margin - self.cleaned.size[0])):
                    # A badge fades out when its side of the split line gets too narrow for it.
                    room = (side_width - sprite.size[0] - 2 * margin) / (60 * unit) + 1
                    sprite.draw(frame, x, label_y, float(np.clip(room, 0, 1)))
            tag_anchor = ((self.width // 2, label_y + self.original.size[1] + round(54 * unit))
                          if job.format == "vertical" else (self.width // 2, y0 + height - margin - round(56 * unit)))
        else:  # flip: alternating cuts, faster on a frozen frame
            x0, y0 = self.places[0]
            period = FREEZE_SECONDS / 2 if mode == "freeze" else FLIP_SECONDS
            moment = local if mode == "freeze" else clock
            showing_original = int(moment // period) % 2 == 0
            frame[y0:y0 + height, x0:x0 + width] = before if showing_original else after
            shown = self.original if showing_original else self.cleaned
            if self.in_bars:
                self._bar_labels(frame, shown, None)
            else:
                shown.draw(frame, x0 + margin, y0 + margin)
            tag_anchor = (self.width // 2, y0 + height - margin - round(56 * unit))
        if mode in self.tags and tag_alpha > 0:
            sprite = self.tags[mode]
            sprite.draw(frame, tag_anchor[0] - sprite.size[0] // 2, tag_anchor[1] - sprite.size[1] // 2, tag_alpha)
        if self.mark is not None:
            mark_width, mark_height = self.mark.size
            if job.format == "vertical":
                # Platform buttons cover the right edge and the bottom fifth of a Short.
                if job.layout == "stacked":
                    bottom = self.places[1][1] + height
                    y = bottom + (self.height - bottom - mark_height) // 2
                else:
                    y = round(self.height * 0.79)
                self.mark.draw(frame, (self.width - mark_width) // 2, y)
            elif self.in_bars and self.mark_lines is not None:
                lines_width, lines_height = self.mark_lines.size
                self.mark_lines.draw(frame, self.width - lines_width - margin + mark_height // 3,
                                     self.height - lines_height - margin + mark_height // 3)
            else:
                self.mark.draw(frame, self.width - mark_width - margin + mark_height // 3,
                               self.height - mark_height - margin + mark_height // 3)
        return frame

    def _bar_labels(self, frame: np.ndarray, left: _Sprite, right: _Sprite | None) -> None:
        """Badges centred in the side bars, level with the top of the picture."""
        bar = self.places[0][0]
        top = self.places[0][1] + self.margin
        left.draw(frame, (bar - left.size[0]) // 2, top)
        if right is not None:
            right.draw(frame, self.width - bar + (bar - right.size[0]) // 2, top)


# ---------------------------------------------------------------- sound

def _read_audio(source: Path, start: float, seconds: float) -> np.ndarray:
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error"]
    if start > 0:
        args.extend(["-ss", f"{start:.6f}"])
    args.extend(["-i", str(source), "-t", f"{seconds:.6f}", "-map", "0:a:0", "-vn", "-ac", "2",
                 "-ar", str(AUDIO_RATE), "-f", "f32le", "pipe:1"])
    raw = subprocess.run(args, capture_output=True, timeout=600, creationflags=PROCESS_FLAGS).stdout
    samples = np.frombuffer(raw[:len(raw) // 8 * 8], np.float32).reshape(-1, 2)
    wanted = round(seconds * AUDIO_RATE)
    if len(samples) < wanted:
        samples = np.concatenate([samples, np.zeros((wanted - len(samples), 2), np.float32)])
    return samples[:wanted]


def sound_track(plan: Zeitplan, source_fps: float, first: int, pieces: list[tuple[Path, float, float]]
                ) -> np.ndarray:
    """The sound under the plan: as recorded while playing, silent in slow motion and freezes.

    ``pieces`` (file, start, seconds) are read one after the other and give the
    sound of cleaned frames from ``first`` on.
    """
    sound = np.concatenate([_read_audio(path, start, seconds) for path, start, seconds in pieces]) \
        if pieces else np.zeros((0, 2), np.float32)
    fade = round(0.012 * AUDIO_RATE)
    parts = []
    for position, frames in plan.runs:
        length = round(frames / plan.fps * AUDIO_RATE)
        if position is None:
            parts.append(np.zeros((length, 2), np.float32))
            continue
        begin = round((position - first) / source_fps * AUDIO_RATE)
        part = sound[max(0, begin):max(0, begin) + length]
        if len(part) < length:
            part = np.concatenate([part, np.zeros((length - len(part), 2), np.float32)])
        part = part.copy()
        ramp = np.linspace(0, 1, min(fade, length // 2), dtype=np.float32)[:, None]
        if len(ramp):
            part[:len(ramp)] *= ramp
            part[len(part) - len(ramp):] *= ramp[::-1]
        parts.append(part)
    return np.concatenate(parts) if parts else np.zeros((0, 2), np.float32)


def write_wave(path: Path, samples: np.ndarray) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(AUDIO_RATE)
        handle.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- the whole video

@dataclass
class Teilergebnis:
    result: Laufergebnis
    duration: float = 0.0
    size: tuple[int, int] = (0, 0)
    fps: float = 0.0
    window: tuple[float, float] = (0.0, 0.0)      # cleaned seconds shown
    highlights: list[float] = field(default_factory=list)  # cleaned seconds slowed down and frozen
    sound: bool = False


def share_name(cleaned: Path, job: Teilauftrag) -> str:
    return f"{cleaned.stem}{SUFFIXES[job.format]}.mp4"


def make_share(original: Path, cleaned: Path, target: Path, segments: object, job: Teilauftrag,
               progress: Callable[[float], None], cancel: threading.Event) -> Teilergebnis:
    """Compose the before/after video of one export and encode it as H.264 MP4."""
    job.validate()
    original, cleaned, target = original.resolve(), cleaned.resolve(), target.resolve()
    if target in {original, cleaned}:
        raise ValueError("The share video must not replace the original or the cleaned file")
    if target.suffix.lower() != ".mp4":
        raise ValueError("The share video is written as .mp4")
    info = inspect_video(original)
    made = inspect_video(cleaned)
    fps = made.fps or info.fps
    if fps <= 0:
        raise ValueError("Could not read the frame rate of the cleaned file")
    parts = normalize_segments(segments, info.duration)
    count = min(max(1, round(made.duration * fps)), sum(segment_frames(parts, fps)) + round(fps * 0.1))
    box, made_box = picture_boxes(original, info, cleaned, made, parts)
    aspect = made_box[2] / made_box[3]
    size = output_size(job, made_box[3])
    rate = output_rate(fps)
    vertical_pan = job.format == "vertical"
    analyse = job.highlights or job.length or vertical_pan
    weight = 0.3 if analyse else 0.0

    centre = np.full(count, 0.5, np.float32)
    peaks: list[tuple[int, float]] = []
    difference = np.zeros(count, np.float32)
    if analyse:
        measured = _measure_pair(original, cleaned, parts, fps, count, box, made_box,
                                 lambda fraction: progress(weight * fraction), cancel)
        if cancel.is_set():
            return Teilergebnis(Laufergebnis(cleaned, target, False, "Canceled"))
        count = min(count, measured.count) or count
        difference = measured.difference[:count]
        peaks = repair_peaks(difference, fps)
        if vertical_pan:
            window = 1.0
            if job.layout == "wipe":
                window = min(1.0, (size[0] / size[1]) / aspect)
            elif aspect > 4 / 3:
                window = (4 / 3) / aspect
            centre = smooth_pan(measured.centre[:count], fps, window)

    if job.length:
        first, last = best_window(difference, peaks, fps, job.length, job.highlights)
    else:
        first, last = 0, count
    chosen: list[int] = []
    if job.highlights:
        margin = round(SLOW_BEFORE * fps)
        inside = [(index, strength) for index, strength in peaks if first + margin <= index < last - margin]
        limit = highlight_limit((last - first) / fps if not job.length else job.length)
        chosen = sorted(index for index, _ in inside[:limit])
    plan = frame_plan(first, last, fps, rate, chosen)
    fades = tag_opacity(plan.modes, rate)

    # Sound from the cleaned file (the export kept the original's); otherwise from the original itself.
    sound_pieces: list[tuple[Path, float, float]] = []
    if job.sound:
        if made.has_audio:
            sound_pieces = [(cleaned, first / fps, (last - first) / fps)]
        elif info.has_audio:
            sound_pieces = [(original, start, seconds)
                            for start, seconds, _ in source_pieces(parts, fps, first, last)]
    with_sound = bool(sound_pieces)

    letters = _Lettering()
    pending = temporary_output(target)
    log = tempfile.TemporaryFile(mode="w+b")
    encoder = None
    readers: list[_Reader] = []
    try:
        composer = _Composer(job, size, aspect, letters)
        args = _encoder(size, rate)
        if with_sound:
            track = letters.folder / "sound.wav"
            write_wave(track, sound_track(plan, fps, first, sound_pieces))
            args.extend(["-i", str(track)])
        args.extend(["-map", "0:v:0"])
        if with_sound:
            args.extend(["-map", "1:a:0"])
        recipe = Arbeitsrezept(kompressionswert=18, aufwand=job.effort)
        # BT.709 conversion, and tagged as such on the frames: newer FFmpeg takes the tags from there.
        args.extend(["-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
                            "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"])
        args.extend(encoding_options(recipe, VideoKenndaten(plan.duration, size[0], size[1], with_sound, rate),
                                     plan.duration))
        args.extend(["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                     "-t", f"{plan.duration:.6f}", str(pending)])
        encoder = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=log, creationflags=PROCESS_FLAGS)
        assert encoder.stdin is not None
        before = _Reader(_decode(original, source_pieces(parts, fps, first, last), fps, box))
        after = _Reader(_decode(cleaned, [(first / fps, (last - first) / fps, last - first)], fps, made_box))
        readers = [before, after]
        total = len(plan.indices)
        run_start = 0
        for number, (index, mode) in enumerate(zip(plan.indices, plan.modes)):
            if cancel.is_set():
                break
            if number and mode != plan.modes[number - 1]:
                run_start = number
            frame = composer.compose(before.get(index - first), after.get(index - first), number / rate, mode,
                                     (number - run_start) / rate, float(centre[min(index, len(centre) - 1)]),
                                     float(fades[number]))
            try:
                encoder.stdin.write(frame.tobytes())
            except (BrokenPipeError, OSError):
                break
            if number % 10 == 0:
                progress(min(1.0, weight + (1 - weight) * (number + 1) / total))
        encoder.stdin.close()
        encoder.wait(timeout=300)
        if cancel.is_set():
            return Teilergebnis(Laufergebnis(cleaned, target, False, "Canceled"))
        if encoder.returncode:
            return Teilergebnis(Laufergebnis(cleaned, target, False,
                                             diagnostic_tail(log) or "Encoding the share video failed"))
        pending.replace(target)
        progress(1.0)
        return Teilergebnis(Laufergebnis(cleaned, target, True, "Done"), plan.duration, size, rate,
                            (first / fps, last / fps), [index / fps for index in chosen], with_sound)
    except Exception:
        if encoder is not None and encoder.poll() is None:
            encoder.kill()
            encoder.wait()
        raise
    finally:
        for reader in readers:
            reader.close()
        if encoder is not None and encoder.stdin and not encoder.stdin.closed:
            try:
                encoder.stdin.close()
            except OSError:
                pass
        log.close()
        pending.unlink(missing_ok=True)
        letters.close()
