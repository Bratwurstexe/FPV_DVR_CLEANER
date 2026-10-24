"""Rate a cleaned clip against its original: what was gained, what is left.

The numbers are estimates from a handful of sampled frames, meant to guide the
decision between "export the original again with other settings" (no loss)
and "run the cleaned file through a second pass" (a little detail is lost to
re-encoding every time).
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import cv2
import numpy as np

from .media import PROCESS_FLAGS, VideoKenndaten

SAMPLES = 16
SAMPLE_WIDTH = 640


def sample_frames(source: Path, info: VideoKenndaten, start: float, end: float,
                  count: int = SAMPLES) -> np.ndarray:
    """A few frames spread over the cut, scaled to a common width."""
    duration = max(0.2, end - start)
    height = max(2, round(info.height * SAMPLE_WIDTH / max(1, info.width) / 2) * 2)
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "quiet"]
    if start:
        args.extend(["-ss", f"{start:.3f}"])
    args.extend(["-i", str(source), "-t", f"{duration:.3f}", "-map", "0:v:0",
                 "-vf", f"fps={max(count / duration, 0.01):.6f},scale={SAMPLE_WIDTH}:{height}",
                 "-frames:v", str(count), "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"])
    result = subprocess.run(args, capture_output=True, timeout=300, creationflags=PROCESS_FLAGS)
    size = SAMPLE_WIDTH * height * 3
    frames = len(result.stdout) // size
    if frames == 0:
        raise ValueError("Could not read frames for the assessment")
    return np.frombuffer(result.stdout[:frames * size], np.uint8).reshape(frames, height, SAMPLE_WIDTH, 3)


def _picture(frame: np.ndarray) -> np.ndarray:
    """Columns that are not black side bars."""
    bright = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean(axis=0) > 12
    columns = np.flatnonzero(bright)
    if len(columns) < frame.shape[1] // 4:
        return frame
    return frame[:, columns[0]:columns[-1] + 1]


def artefacts(frame: np.ndarray) -> dict[str, float]:
    """Artefact levels of one frame: grain, colour noise, streaks, block seams."""
    picture = _picture(frame)
    lab = cv2.cvtColor(picture, cv2.COLOR_BGR2LAB).astype(np.float32)
    luma = lab[:, :, 0]
    grain = float(np.abs(luma - cv2.GaussianBlur(luma, (0, 0), 1.2)).mean())
    chroma = lab[:, :, 1:]
    colour = float(np.abs(chroma - cv2.GaussianBlur(chroma, (0, 0), 2.0)).mean())
    rows = np.abs(np.diff(luma, axis=0)).mean(axis=1)
    streaks = float(np.percentile(rows, 99) / max(1.0, float(np.median(rows))))
    edge = np.abs(np.diff(luma, axis=1))
    seams = float(edge[:, 7::8].mean() / max(1.0, float(edge[:, 3::8].mean())))
    return {"grain": grain, "colour": colour, "streaks": streaks, "seams": seams}


def _floor(frame: np.ndarray) -> dict[str, float]:
    """What a stronger pass could reach at best: the frame with noise taken out."""
    calmed = cv2.medianBlur(frame, 5)
    return artefacts(calmed)


def summarise(frames: np.ndarray) -> dict[str, float]:
    values = [artefacts(frame) for frame in frames]
    return {key: float(np.median([value[key] for value in values])) for key in values[0]}


def assess(original: np.ndarray, cleaned: np.ndarray, quality: int, signal: str) -> dict:
    """Gain achieved, headroom left and the cost of another encode, all as percentages."""
    before = summarise(original)
    after = summarise(cleaned)
    floors = [_floor(frame) for frame in cleaned]
    floor = {key: float(np.median([value[key] for value in floors])) for key in before}
    keys = ["grain", "colour", "streaks"] + (["seams"] if signal == "digital" else [])
    gains, headrooms = {}, {}
    for key in keys:
        base = max(before[key], 1e-6)
        # Ratios are measured against "no artefacts", which is the floor, not zero.
        span = max(base - floor[key], 1e-6)
        gains[key] = float(np.clip((before[key] - after[key]) / span * 100, 0, 100))
        headrooms[key] = float(np.clip((after[key] - floor[key]) / span * 100, 0, 100))
    gain = float(np.mean(list(gains.values())))
    headroom = float(np.mean(list(headrooms.values())))
    # Detail lost to one more H.264 generation at this quality (CRF); a rule of thumb
    # from re-encoding tests: about 0.6 % per CRF step above 12.
    loss = float(np.clip((quality - 12) * 0.6, 0.5, 15))
    return {
        "before": before, "after": after, "floor": floor,
        "gains": gains, "headrooms": headrooms,
        "gain_percent": round(gain, 1), "headroom_percent": round(headroom, 1),
        "reencode_loss_percent": round(loss, 1),
        "second_pass_worth": bool(headroom > max(8.0, loss * 2)),
    }
