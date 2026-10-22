"""Multi-frame upscaling: neighbouring frames contribute detail to each frame.

Each frame is enlarged with Lanczos. Two earlier and two later frames are
enlarged the same way, warped onto the current frame's grid with optical flow
and fused with weights that drop where they disagree (occlusion, wrong flow).
Analog noise and sub-pixel camera shifts differ from frame to frame, so the
fused luma is calmer and resolves more fine structure than one enlarged frame.
A limited detail lift follows; its overshoot is clipped to avoid halos.
Chroma is enlarged from the current frame only.
"""

from __future__ import annotations

from collections import deque
from typing import Iterable, Iterator

import cv2
import numpy as np


RADIUS = 2
FLOW_WIDTH = 480
AGREEMENT = 10.0
LIFT_LIMIT = 12.0


def target_size(width: int, height: int, percent: int) -> tuple[int, int]:
    """Output size for a percentage, rounded to even numbers for H.264."""
    return (round(width * percent / 200) * 2, round(height * percent / 200) * 2)


class _Enlarged:
    def __init__(self, frame: np.ndarray, size: tuple[int, int]) -> None:
        ycc = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
        self.luma = cv2.resize(ycc[:, :, 0], size, interpolation=cv2.INTER_LANCZOS4).astype(np.float32)
        self.ycc = ycc
        height, width = frame.shape[:2]
        factor = min(1.0, FLOW_WIDTH / width)
        self.small = cv2.resize(ycc[:, :, 0], (max(8, round(width * factor)), max(8, round(height * factor))),
                                interpolation=cv2.INTER_AREA)


class _Fuser:
    def __init__(self, size: tuple[int, int]) -> None:
        self.size = size
        self.flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        width, height = size
        self.grid = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))

    def frame(self, window: list[_Enlarged], center: int, lift: float) -> np.ndarray:
        present = window[center]
        width, height = self.size
        total = present.luma.copy()
        weight = np.ones_like(total)
        for index, other in enumerate(window):
            if index == center:
                continue
            flow = cv2.resize(self.flow.calc(present.small, other.small, None), self.size,
                              interpolation=cv2.INTER_LINEAR)
            map_x = self.grid[0] + flow[:, :, 0] * (width / present.small.shape[1])
            map_y = self.grid[1] + flow[:, :, 1] * (height / present.small.shape[0])
            moved = cv2.remap(other.luma, map_x, map_y, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
            agree = np.exp(-np.square((moved - present.luma) / AGREEMENT))
            total += moved * agree
            weight += agree
        fused = total / weight
        if lift:
            detail = fused - cv2.GaussianBlur(fused, (0, 0), 1.2)
            fused += np.clip(detail * lift, -LIFT_LIMIT, LIFT_LIMIT)
        chroma = cv2.resize(present.ycc[:, :, 1:], self.size, interpolation=cv2.INTER_LANCZOS4)
        result = np.dstack([np.clip(fused, 0, 255).astype(np.uint8), chroma])
        return cv2.cvtColor(result, cv2.COLOR_YCrCb2BGR)


def upscale_stream(frames: Iterable[np.ndarray], percent: int, lift: float = 0.6,
                   radius: int = RADIUS) -> Iterator[np.ndarray]:
    """Yield enlarged frames in order, each delayed by ``radius`` input frames."""
    if percent == 100:
        yield from frames
        return
    window: deque[_Enlarged] = deque()
    fuser = None
    first = emitted = 0
    for count, frame in enumerate(frames, start=1):
        if fuser is None:
            fuser = _Fuser(target_size(frame.shape[1], frame.shape[0], percent))
        window.append(_Enlarged(frame, fuser.size))
        while emitted + radius < count:
            yield fuser.frame(list(window), emitted - first, lift)
            emitted += 1
            while emitted - first > radius:
                window.popleft()
                first += 1
    while emitted < first + len(window):
        yield fuser.frame(list(window), emitted - first, lift)
        emitted += 1
        while emitted - first > radius and len(window) > 1:
            window.popleft()
            first += 1
