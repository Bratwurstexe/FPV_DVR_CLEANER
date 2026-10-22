"""Multi-frame fusion: noise reduction, streak repair and upscaling in one pass.

Each frame's luma is enlarged with Lanczos (or kept at its size). Two earlier
and two later frames are treated the same way and warped onto the current
frame's grid with optical flow. The per-pixel median of these five values is
the reference: a bright or dark streak in one frame is an outlier against it
and gets replaced, and random analog noise averages out. Every value is then
weighted by its distance to the reference. Where the aligned frames disagree
among themselves (occlusion, flow errors) the current frame is kept, so fast
motion does not ghost. A limited detail lift follows; its overshoot is clipped
to avoid halos. Chroma comes from the current frame only, it is repaired
earlier by the analog colour stage.
"""

from __future__ import annotations

from collections import deque
from typing import Iterable, Iterator

import cv2
import numpy as np


RADIUS = 2
FLOW_WIDTH = 480
LIFT_LIMIT = 12.0


def target_size(width: int, height: int, percent: int) -> tuple[int, int]:
    """Output size for a percentage, rounded to even numbers for H.264."""
    return (round(width * percent / 200) * 2, round(height * percent / 200) * 2)


class _Layer:
    def __init__(self, frame: np.ndarray, size: tuple[int, int]) -> None:
        ycc = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
        same = size == (frame.shape[1], frame.shape[0])
        self.luma = (ycc[:, :, 0] if same else
                     cv2.resize(ycc[:, :, 0], size, interpolation=cv2.INTER_LANCZOS4)).astype(np.float32)
        self.ycc = ycc
        height, width = frame.shape[:2]
        factor = min(1.0, FLOW_WIDTH / width)
        self.small = cv2.resize(ycc[:, :, 0], (max(8, round(width * factor)), max(8, round(height * factor))),
                                interpolation=cv2.INTER_AREA)


class _Fuser:
    def __init__(self, size: tuple[int, int], calm: float, lift: float) -> None:
        self.size = size
        self.flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        width, height = size
        self.grid = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
        # Stronger calming accepts larger differences as noise and trusts the
        # current frame less.
        self.tolerance = 6.0 + 14.0 * calm
        self.own_weight = 1.0 + 3.0 * (1.0 - calm)
        self.lift = lift

    def frame(self, window: list[_Layer], center: int) -> np.ndarray:
        present = window[center]
        width, height = self.size
        layers = [present.luma]
        for index, other in enumerate(window):
            if index == center:
                continue
            flow = cv2.resize(self.flow.calc(present.small, other.small, None), self.size,
                              interpolation=cv2.INTER_LINEAR)
            map_x = self.grid[0] + flow[:, :, 0] * (width / present.small.shape[1])
            map_y = self.grid[1] + flow[:, :, 1] * (height / present.small.shape[0])
            layers.append(cv2.remap(other.luma, map_x, map_y, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE))
        stack = np.stack(layers)
        fused = present.luma
        if len(layers) > 2:
            reference = np.median(stack, axis=0)
            closeness = np.exp(-np.square((stack - reference) / self.tolerance))
            closeness[0] *= self.own_weight
            blended = (stack * closeness).sum(axis=0) / closeness.sum(axis=0)
            # How well the aligned frames agree with each other; poor agreement
            # means the motion was not followed, so keep the frame as it is.
            spread = np.median(np.abs(stack[1:] - reference), axis=0)
            trust = np.exp(-np.square(spread / (1.5 * self.tolerance)))
            fused = trust * blended + (1 - trust) * present.luma
        if self.lift:
            detail = fused - cv2.GaussianBlur(fused, (0, 0), 1.2)
            fused = fused + np.clip(detail * self.lift, -LIFT_LIMIT, LIFT_LIMIT)
        chroma = (present.ycc[:, :, 1:] if self.size == present.ycc.shape[1::-1] else
                  cv2.resize(present.ycc[:, :, 1:], self.size, interpolation=cv2.INTER_LANCZOS4))
        result = np.dstack([np.clip(fused, 0, 255).astype(np.uint8), chroma])
        return cv2.cvtColor(result, cv2.COLOR_YCrCb2BGR)


def fuse_stream(frames: Iterable[np.ndarray], percent: int = 100, calm: float = 0.5, lift: float = 0.0,
                radius: int = RADIUS) -> Iterator[np.ndarray]:
    """Yield fused (and optionally enlarged) frames, each delayed by ``radius`` frames."""
    if percent == 100 and calm <= 0 and lift <= 0:
        yield from frames
        return
    window: deque[_Layer] = deque()
    fuser = None
    first = emitted = 0
    for count, frame in enumerate(frames, start=1):
        if fuser is None:
            fuser = _Fuser(target_size(frame.shape[1], frame.shape[0], percent), calm, lift)
        window.append(_Layer(frame, fuser.size))
        while emitted + radius < count:
            yield fuser.frame(list(window), emitted - first)
            emitted += 1
            while emitted - first > radius:
                window.popleft()
                first += 1
    while emitted < first + len(window):
        yield fuser.frame(list(window), emitted - first)
        emitted += 1
        while emitted - first > radius and len(window) > 1:
            window.popleft()
            first += 1
