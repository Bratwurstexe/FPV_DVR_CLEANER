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

from typing import Iterable, Iterator

import cv2
import numpy as np

from .pipeline import PerThread, window_map


RADIUS = 2
FLOW_WIDTH = 480
LIFT_LIMIT = 12.0


def target_size(width: int, height: int, percent: int) -> tuple[int, int]:
    """Output size for a percentage, rounded to even numbers for H.264."""
    return (round(width * percent / 200) * 2, round(height * percent / 200) * 2)


def _median(layers: list[np.ndarray]) -> np.ndarray:
    """Per-pixel median; 3, 4 and 5 layers use min/max networks instead of a sort."""
    low, high = cv2.min, cv2.max
    if len(layers) == 3:
        a, b, c = layers
        return high(low(a, b), low(high(a, b), c))
    if len(layers) in (4, 5):
        a, b, c, d = layers[:4]
        # The middle two of four values.
        second = high(low(a, b), low(c, d))
        third = low(high(a, b), high(c, d))
        second, third = low(second, third), high(second, third)
        if len(layers) == 4:
            return cv2.addWeighted(second, 0.5, third, 0.5, 0)
        return high(second, low(third, layers[4]))
    return np.median(np.stack(layers), axis=0)


class _Layer:
    def __init__(self, frame: np.ndarray, size: tuple[int, int], flow_width: int = FLOW_WIDTH) -> None:
        ycc = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
        same = size == (frame.shape[1], frame.shape[0])
        self.luma = (ycc[:, :, 0] if same else
                     cv2.resize(ycc[:, :, 0], size, interpolation=cv2.INTER_LANCZOS4)).astype(np.float32)
        self.ycc = ycc
        height, width = frame.shape[:2]
        factor = min(1.0, flow_width / width)
        self.small = cv2.resize(ycc[:, :, 0], (max(8, round(width * factor)), max(8, round(height * factor))),
                                interpolation=cv2.INTER_AREA)


class _Fuser:
    def __init__(self, size: tuple[int, int], calm: float, lift: float,
                 preset: int = cv2.DISOPTICAL_FLOW_PRESET_MEDIUM,
                 interpolation: int = cv2.INTER_CUBIC) -> None:
        self.size = size
        self.flow = cv2.DISOpticalFlow_create(preset)
        self.interpolation = interpolation
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
            step_x, step_y = cv2.split(flow)
            map_x = cv2.scaleAdd(step_x, width / present.small.shape[1], self.grid[0])
            map_y = cv2.scaleAdd(step_y, height / present.small.shape[0], self.grid[1])
            layers.append(cv2.remap(other.luma, map_x, map_y, self.interpolation,
                                    borderMode=cv2.BORDER_REPLICATE))
        fused = present.luma
        if len(layers) > 2:
            # Full-frame float arithmetic dominates this stage; OpenCV calls
            # write into existing buffers instead of making NumPy temporaries.
            reference = _median(layers)
            total = np.zeros_like(fused)
            weights = np.zeros_like(fused)
            closeness = np.empty_like(fused)
            gaps = []
            for index, layer in enumerate(layers):
                gap = cv2.absdiff(layer, reference)
                cv2.multiply(gap, gap, dst=closeness, scale=-1 / self.tolerance ** 2)
                cv2.exp(closeness, dst=closeness)
                if index == 0:
                    closeness *= self.own_weight
                else:
                    gaps.append(gap)
                cv2.accumulateProduct(layer, closeness, total)
                cv2.accumulate(closeness, weights)
            blended = cv2.divide(total, weights)
            # How well the aligned frames agree with each other; poor agreement
            # means the motion was not followed, so keep the frame as it is.
            spread = _median(gaps)
            trust = cv2.multiply(spread, spread, scale=-1 / (1.5 * self.tolerance) ** 2)
            cv2.exp(trust, dst=trust)
            fused = cv2.subtract(blended, present.luma)
            cv2.multiply(fused, trust, dst=fused)
            cv2.add(fused, present.luma, dst=fused)
        if self.lift:
            detail = fused - cv2.GaussianBlur(fused, (0, 0), 1.2)
            fused = fused + np.clip(detail * self.lift, -LIFT_LIMIT, LIFT_LIMIT)
        chroma = (present.ycc[:, :, 1:] if self.size == present.ycc.shape[1::-1] else
                  cv2.resize(present.ycc[:, :, 1:], self.size, interpolation=cv2.INTER_LANCZOS4))
        result = np.dstack([np.clip(fused, 0, 255).astype(np.uint8), chroma])
        return cv2.cvtColor(result, cv2.COLOR_YCrCb2BGR)


def fuse_stream(frames: Iterable[np.ndarray], percent: int = 100, calm: float = 0.5, lift: float = 0.0,
                radius: int = RADIUS, flow_width: int = FLOW_WIDTH,
                preset: int = cv2.DISOPTICAL_FLOW_PRESET_MEDIUM,
                interpolation: int = cv2.INTER_CUBIC) -> Iterator[np.ndarray]:
    """Yield fused (and optionally enlarged) frames, each delayed by ``radius`` frames.

    Frames are fused in parallel on the shared pool.
    """
    if percent == 100 and calm <= 0 and lift <= 0:
        yield from frames
        return
    fusers: dict[tuple[int, int], PerThread] = {}

    def prepare(frame: np.ndarray) -> _Layer:
        return _Layer(frame, target_size(frame.shape[1], frame.shape[0], percent), flow_width)

    def work(window: list[_Layer], center: int) -> np.ndarray:
        height, width = window[center].luma.shape
        if (width, height) not in fusers:
            fusers[width, height] = PerThread(lambda: _Fuser((width, height), calm, lift, preset, interpolation))
        return fusers[width, height].get().frame(window, center)

    yield from window_map(frames, radius, prepare, work)
