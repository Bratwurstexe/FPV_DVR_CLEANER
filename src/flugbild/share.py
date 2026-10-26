"""The cleaned clip as a ready video for YouTube or for Shorts, Reels and TikTok.

Only the cleaned file is used, from its first frame to its last. 16:9 places
the DVR picture in the middle of a black 16:9 frame; 9:16 crops the picture to
the phone screen and pans smoothly across it, following the motion.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import subprocess
import tempfile
import threading
from typing import Callable, Iterator

import cv2
import numpy as np

from .media import PROCESS_FLAGS, VideoKenndaten, inspect_video
from .processor import Laufergebnis, diagnostic_tail, encoding_options, temporary_output
from .recipe import Arbeitsrezept
from .restoration import _encoder, picture_box

FORMATS = ("landscape", "vertical")
ANALYSIS_WIDTH = 160


@dataclass(frozen=True)
class Teilauftrag:
    """Which format the cleaned clip is brought into."""

    format: str = "landscape"
    sound: bool = True
    effort: str = "best"       # encoder speed, as on the Repair page

    def validate(self) -> None:
        if self.format not in FORMATS:
            raise ValueError("Choose 16:9 or 9:16")
        if not isinstance(self.sound, bool):
            raise ValueError("sound must be on or off")
        if self.effort not in {"fast", "balanced", "best"}:
            raise ValueError("Unknown processing effort")

    @classmethod
    def of(cls, settings: dict) -> Teilauftrag:
        """The job from a request; fields of older requests (layout, length, ...) are ignored."""
        known = {key: settings[key] for key in cls.__dataclass_fields__ if key in settings}
        # Without a choice the sound stays for 16:9 and is left out for 9:16.
        known.setdefault("sound", known.get("format", "landscape") == "landscape")
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


def frame_indices(count: int, source_fps: float, fps: float) -> list[int]:
    """The cleaned frame each output frame shows: all of them, or evenly dropped for faster sources."""
    step = source_fps / fps if fps > 0 else 1.0
    total = max(1, round(count / step))
    return [min(count - 1, int(round(number * step))) for number in range(total)]


def _even(value: float) -> int:
    return int(round(value / 2) * 2)


def inset(box: tuple[int, int, int, int], fraction: float = 0.01) -> tuple[int, int, int, int]:
    """The box a hair smaller on every side: edges of a DVR picture often carry a dark or bright line."""
    x, y, width, height = box
    dx = max(2, _even(width * fraction)) if width > 64 else 0
    dy = max(2, _even(height * fraction)) if height > 64 else 0
    return x + dx, y + dy, width - 2 * dx, height - 2 * dy


# ---------------------------------------------------------------- reading the cleaned clip

def _decode(source: Path, count: int, fps: float, box: tuple[int, int, int, int],
            size: tuple[int, int] | None = None) -> Iterator[np.ndarray]:
    """Exactly ``count`` frames of ``source`` from the start, cropped to ``box``, optionally scaled.

    A short decode repeats its last frame, so the length never depends on the FFmpeg build.
    """
    x, y, width, height = box
    out_width, out_height = size or (width, height)
    frame_bytes = out_width * out_height * 3
    chain = f"crop={width}:{height}:{x}:{y}"
    if size:
        chain += f",scale={out_width}:{out_height}:flags=area"
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(source),
            "-map", "0:v:0", "-an", "-sn", "-vf", chain, "-r", f"{fps:.6f}",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    decoder = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               creationflags=PROCESS_FLAGS)
    last = None
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


# ---------------------------------------------------------------- horizontal pan for 9:16

def motion_centres(frames: Iterator[np.ndarray], count: int,
                   report: Callable[[float], None] = lambda fraction: None,
                   cancel: threading.Event | None = None) -> np.ndarray:
    """Per frame, the horizontal centre of motion, 0..1 of the picture width (nan: no motion)."""
    centre = []
    previous = None
    for index, frame in enumerate(frames):
        if cancel is not None and cancel.is_set():
            break
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
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
    if len(centre) > 1:
        # The first frame has no predecessor; it moves as the second does.
        centre[0] = centre[1]
    return np.array(centre, np.float32)


def _running_median(values: np.ndarray, radius: int) -> np.ndarray:
    padded = np.pad(values, radius, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * radius + 1)
    return np.median(windows, axis=1)


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


# ---------------------------------------------------------------- one output frame

class _Framer:
    """Puts the cleaned picture into the output frame: centred on black, or cropped for 9:16."""

    def __init__(self, size: tuple[int, int], aspect: float, vertical: bool):
        self.width, self.height = size
        # 9:16 shows a part of the picture as wide as the phone screen; 16:9 shows all of it.
        self.window = min(1.0, (self.width / self.height) / aspect) if vertical else 1.0
        shown = aspect * self.window
        picture_width = min(self.width, self.height * shown)
        self.picture = (_even(picture_width), _even(picture_width / shown))
        self.place = ((self.width - self.picture[0]) // 2, (self.height - self.picture[1]) // 2)
        self.ground = np.zeros((self.height, self.width, 3), np.uint8)

    def frame(self, picture: np.ndarray, centre: float) -> np.ndarray:
        if self.window < 0.999:
            crop = max(2, round(picture.shape[1] * self.window))
            left = crop_span(picture.shape[1], crop, centre)
            picture = picture[:, left:left + crop]
        width, height = self.picture
        if picture.shape[1] != width or picture.shape[0] != height:
            shrink = picture.shape[1] > width
            picture = cv2.resize(picture, (width, height), interpolation=cv2.INTER_AREA if shrink else cv2.INTER_CUBIC)
        if (width, height) == (self.width, self.height):
            return np.ascontiguousarray(picture)
        out = self.ground.copy()
        x, y = self.place
        out[y:y + height, x:x + width] = picture
        return out


# ---------------------------------------------------------------- the whole video

@dataclass
class Teilergebnis:
    result: Laufergebnis
    duration: float = 0.0
    size: tuple[int, int] = (0, 0)
    fps: float = 0.0
    sound: bool = False


def make_share(cleaned: Path, target: Path, job: Teilauftrag,
               progress: Callable[[float], None], cancel: threading.Event) -> Teilergebnis:
    """Bring the whole cleaned clip into the chosen format and encode it as H.264 MP4."""
    job.validate()
    cleaned, target = cleaned.resolve(), target.resolve()
    if target == cleaned:
        raise ValueError("The share video must not replace the cleaned file")
    if target.suffix.lower() != ".mp4":
        raise ValueError("The share video is written as .mp4")
    made = inspect_video(cleaned)
    fps = made.fps
    if fps <= 0:
        raise ValueError("Could not read the frame rate of the cleaned file")
    count = max(1, round(made.duration * fps))
    found = picture_box(cleaned, 0.0, made.duration, made.width, made.height)
    size = output_size(job, found[3])
    box = inset(found)
    aspect = box[2] / box[3]
    rate = output_rate(fps)
    vertical = job.format == "vertical"
    framer = _Framer(size, aspect, vertical)
    weight = 0.3 if vertical else 0.0

    centre = np.full(count, 0.5, np.float32)
    if vertical and framer.window < 0.999:
        small = (ANALYSIS_WIDTH, max(2, _even(ANALYSIS_WIDTH / aspect)))
        measured = motion_centres(_decode(cleaned, count, fps, box, small), count,
                                  lambda fraction: progress(weight * fraction), cancel)
        if cancel.is_set():
            return Teilergebnis(Laufergebnis(cleaned, target, False, "Canceled"))
        centre[:len(measured)] = smooth_pan(measured, fps, framer.window)[:count]

    indices = frame_indices(count, fps, rate)
    duration = len(indices) / rate
    with_sound = job.sound and made.has_audio

    pending = temporary_output(target)
    log = tempfile.TemporaryFile(mode="w+b")
    encoder = None
    reader: _Reader | None = None
    try:
        args = _encoder(size, rate)
        if with_sound:
            args.extend(["-i", str(cleaned), "-map", "0:v:0", "-map", "1:a:0"])
        else:
            args.extend(["-map", "0:v:0"])
        recipe = Arbeitsrezept(kompressionswert=18, aufwand=job.effort)
        # BT.709 conversion, and tagged as such on the frames: newer FFmpeg takes the tags from there.
        args.extend(["-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
                            "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"])
        args.extend(encoding_options(recipe, VideoKenndaten(duration, size[0], size[1], with_sound, rate), duration))
        args.extend(["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                     "-t", f"{duration:.6f}", str(pending)])
        encoder = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=log, creationflags=PROCESS_FLAGS)
        assert encoder.stdin is not None
        reader = _Reader(_decode(cleaned, count, fps, box))
        total = len(indices)
        for number, index in enumerate(indices):
            if cancel.is_set():
                break
            frame = framer.frame(reader.get(index), float(centre[index]))
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
        return Teilergebnis(Laufergebnis(cleaned, target, True, "Done"), duration, size, rate, with_sound)
    except Exception:
        if encoder is not None and encoder.poll() is None:
            encoder.kill()
            encoder.wait()
        raise
    finally:
        if reader is not None:
            reader.close()
        if encoder is not None and encoder.stdin and not encoder.stdin.closed:
            try:
                encoder.stdin.close()
            except OSError:
                pass
        if encoder is not None and encoder.poll() is None:
            encoder.kill()
            encoder.wait()
        log.close()
        pending.unlink(missing_ok=True)
