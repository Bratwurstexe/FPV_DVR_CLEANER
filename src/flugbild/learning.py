"""Local calibration from user footage; no inherited weights or reference assets."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import statistics
import threading
import time
from typing import Callable

import cv2
import numpy as np


EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".ts"}


@dataclass(frozen=True)
class Signalsignatur:
    formatstand: int
    signalart: str
    stichproben: int
    farbrauschen: float
    blocknaht: float
    quelldateien: int

    def save(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, destination: Path) -> Signalsignatur:
        raw = json.loads(destination.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != set(cls.__dataclass_fields__):
            raise ValueError("Unknown model format")
        result = cls(**raw)
        if (type(result.formatstand) is not int or result.formatstand != 1
                or result.signalart not in {"analog", "digital"}
                or type(result.stichproben) is not int or result.stichproben < 1
                or type(result.quelldateien) is not int or result.quelldateien < 1
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       or value < 0 or value > 255
                       for value in (result.farbrauschen, result.blocknaht))):
            raise ValueError("Invalid signal model")
        return result


def candidates(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise ValueError("Choose a folder with videos")
    return sorted(item for item in folder.rglob("*")
                  if item.is_file() and item.suffix.lower() in EXTENSIONS)


def _measure(frame: np.ndarray) -> tuple[float, float]:
    small = cv2.resize(frame, (min(frame.shape[1], 640), min(frame.shape[0], 360)),
                       interpolation=cv2.INTER_AREA)
    ycc = cv2.cvtColor(small, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    chroma = ycc[:, :, 1:]
    smooth = cv2.GaussianBlur(chroma, (5, 5), 0)
    farbrauschen = float(np.percentile(np.abs(chroma - smooth), 85))
    gray = ycc[:, :, 0]
    height, width = gray.shape
    if height < 24 or width < 24:
        return farbrauschen, 0.0
    vertical = np.abs(gray[:, 8:width - 1:8] - gray[:, 7:width - 2:8])
    horizontal = np.abs(gray[8:height - 1:8, :] - gray[7:height - 2:8, :])
    ordinary_v = np.abs(gray[:, 5:width - 1:8] - gray[:, 4:width - 2:8])
    ordinary_h = np.abs(gray[5:height - 1:8, :] - gray[4:height - 2:8, :])
    blocknaht = max(0.0, (float(np.percentile(vertical, 85)) + float(np.percentile(horizontal, 85))
                           - float(np.percentile(ordinary_v, 85)) - float(np.percentile(ordinary_h, 85))) / 2)
    return farbrauschen, blocknaht


def calibrate(folder: Path, signalart: str, seconds: int,
              announce: Callable[[int, int], None], stop: threading.Event) -> Signalsignatur:
    if signalart not in {"analog", "digital"}:
        raise ValueError("Signal family must be Analog or HDZero")
    if not 1 <= seconds <= 3600:
        raise ValueError("Learning time must be between 1 and 3600 seconds")
    footage = candidates(folder)
    if not footage:
        raise ValueError("No supported videos found in the folder")
    chroma_scores: list[float] = []
    seam_scores: list[float] = []
    touched: set[Path] = set()
    deadline = time.monotonic() + seconds
    index = 0
    while time.monotonic() < deadline and not stop.is_set():
        clip = footage[index % len(footage)]
        index += 1
        stream = cv2.VideoCapture(str(clip))
        if not stream.isOpened():
            continue
        touched.add(clip)
        try:
            total = int(stream.get(cv2.CAP_PROP_FRAME_COUNT))
            for position in range(0, max(total, 1), max(1, total // 40)):
                if time.monotonic() >= deadline or stop.is_set():
                    break
                stream.set(cv2.CAP_PROP_POS_FRAMES, position)
                ok, frame = stream.read()
                if ok:
                    chroma, seam = _measure(frame)
                    chroma_scores.append(chroma)
                    seam_scores.append(seam)
                    announce(len(chroma_scores), seconds)
        finally:
            stream.release()
    if not chroma_scores:
        raise ValueError("Could not read frames from these videos")
    return Signalsignatur(1, signalart, len(chroma_scores),
                             round(statistics.median(chroma_scores), 4),
                             round(statistics.median(seam_scores), 4), len(touched))
