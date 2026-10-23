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

from . import gpu
from .pipeline import PerThread, window_map


RADIUS = 2
FLOW_WIDTH = 480
LIFT_LIMIT = 12.0


def target_size(width: int, height: int, percent: int) -> tuple[int, int]:
    """Output size for a percentage, rounded to even numbers for H.264."""
    return (round(width * percent / 200) * 2, round(height * percent / 200) * 2)


def _median(layers: list[np.ndarray]) -> np.ndarray:
    """Per-pixel median; up to 5 layers use min/max networks instead of a sort,
    which also works on GPU images."""
    low, high = cv2.min, cv2.max
    if len(layers) == 1:
        return layers[0]
    if len(layers) == 2:
        return cv2.addWeighted(layers[0], 0.5, layers[1], 0.5, 0)
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


# OpenCV's bicubic coefficient.
CUBIC_A = -0.75


def _cubic_taps(position):
    """Three lookup positions and weights along one axis for bicubic sampling.

    Of the four bicubic taps the middle two always have the same sign, so one
    bilinear lookup between them, placed by their weights, replaces both.
    With f the fraction of ``position`` and g = 1 - f, the outer weights are
    A f g^2 and A f^2 g and the merged middle weight is 1 - A f g.
    """
    base = cv2.add(cv2.add(position, 0, dtype=cv2.CV_32S), 0, dtype=cv2.CV_32F)
    # Rounding went up where it passed the position: step back to the floor.
    cv2.subtract(base, 1.0, dst=base, mask=cv2.compare(base, position, cv2.CMP_GT))
    f = cv2.subtract(position, base)
    g = cv2.addWeighted(f, -1, f, 0, 1)
    fg = cv2.multiply(f, g)
    outer_left = cv2.multiply(fg, g, scale=CUBIC_A)
    outer_right = cv2.multiply(fg, f, scale=CUBIC_A)
    middle = cv2.addWeighted(fg, -CUBIC_A, fg, 0, 1)
    # Weight of the right middle tap: 1 - (A+3) g^2 + (A+2) g^3.
    g2 = cv2.multiply(g, g)
    right = cv2.addWeighted(cv2.multiply(g2, g), CUBIC_A + 2, g2, -(CUBIC_A + 3), 1)
    between = cv2.add(base, cv2.divide(right, middle))
    return ((cv2.subtract(base, 1.0), outer_left), (between, middle), (cv2.add(base, 2.0), outer_right))


def _remap_cubic(image, map_x, map_y):
    """``cv2.remap`` with INTER_CUBIC from nine bilinear lookups.

    OpenCV's OpenCL path has bilinear but no bicubic remap; this keeps cubic
    warping on the GPU. Matches the CPU result to about one grey level.
    """
    across, down = _cubic_taps(map_x), _cubic_taps(map_y)
    total = None
    for row, row_weight in down:
        line = None
        for column, column_weight in across:
            part = cv2.multiply(cv2.remap(image, column, row, cv2.INTER_LINEAR,
                                          borderMode=cv2.BORDER_REPLICATE), column_weight)
            line = part if line is None else cv2.add(line, part, dst=line)
        part = cv2.multiply(line, row_weight)
        total = part if total is None else cv2.add(total, part, dst=total)
    return total


class _Layer:
    """Per-frame data: full-size luma and chroma, on the GPU if ``on_gpu``,
    and the reduced luma for optical flow, always on the CPU so the flow is
    the same either way."""

    def __init__(self, frame: np.ndarray, size: tuple[int, int], flow_width: int = FLOW_WIDTH,
                 on_gpu: bool = False) -> None:
        ycc = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
        height, width = frame.shape[:2]
        self.source_size = (width, height)
        luma = (ycc[:, :, 0] if size == self.source_size else
                cv2.resize(ycc[:, :, 0], size, interpolation=cv2.INTER_LANCZOS4)).astype(np.float32)
        chroma = np.ascontiguousarray(ycc[:, :, 1:])
        factor = min(1.0, flow_width / width)
        self.small = cv2.resize(ycc[:, :, 0], (max(8, round(width * factor)), max(8, round(height * factor))),
                                interpolation=cv2.INTER_AREA)
        self.luma = cv2.UMat(luma) if on_gpu else luma
        self.chroma = cv2.UMat(chroma) if on_gpu else chroma


class _Fuser:
    def __init__(self, size: tuple[int, int], calm: float, lift: float,
                 preset: int = cv2.DISOPTICAL_FLOW_PRESET_MEDIUM,
                 interpolation: int = cv2.INTER_CUBIC, on_gpu: bool = False) -> None:
        self.size = size
        self.flow = cv2.DISOpticalFlow_create(preset)
        self.interpolation = interpolation
        self.on_gpu = on_gpu
        width, height = size
        grid = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
        self.grid = [cv2.UMat(axis) for axis in grid] if on_gpu else grid
        # Stronger calming accepts larger differences as noise and trusts the
        # current frame less.
        self.tolerance = 6.0 + 14.0 * calm
        self.own_weight = 1.0 + 3.0 * (1.0 - calm)
        self.lift = lift

    def frame(self, window: list[_Layer], center: int) -> np.ndarray:
        present = window[center]
        # Optical flow on the reduced frames stays on this CPU thread; only
        # the full-size work below waits for a GPU slot.
        flows = [(other, self.flow.calc(present.small, other.small, None))
                 for index, other in enumerate(window) if index != center]
        if not self.on_gpu:
            return self.fuse(present, flows)
        with gpu.slot():
            return self.fuse(present, flows).get()

    def fuse(self, present: _Layer, flows: list[tuple[_Layer, np.ndarray]]):
        """Every call below takes arrays or GPU images alike."""
        width, height = self.size
        layers = [present.luma]
        for other, flow in flows:
            flow = cv2.resize(cv2.UMat(flow) if self.on_gpu else flow, self.size, interpolation=cv2.INTER_LINEAR)
            step_x, step_y = cv2.split(flow)
            map_x = cv2.scaleAdd(step_x, width / present.small.shape[1], self.grid[0])
            map_y = cv2.scaleAdd(step_y, height / present.small.shape[0], self.grid[1])
            if self.on_gpu and self.interpolation == cv2.INTER_CUBIC:
                layers.append(_remap_cubic(other.luma, map_x, map_y))
            else:
                layers.append(cv2.remap(other.luma, map_x, map_y, self.interpolation,
                                        borderMode=cv2.BORDER_REPLICATE))
        fused = present.luma
        if len(layers) > 2:
            # Full-frame float arithmetic dominates this stage; calls write
            # into existing buffers where they can.
            reference = _median(layers)
            total = weights = None
            gaps = []
            for index, layer in enumerate(layers):
                gap = cv2.absdiff(layer, reference)
                closeness = cv2.multiply(gap, gap, scale=-1 / self.tolerance ** 2)
                cv2.exp(closeness, dst=closeness)
                if index == 0:
                    cv2.multiply(closeness, self.own_weight, dst=closeness)
                else:
                    gaps.append(gap)
                part = cv2.multiply(layer, closeness)
                if total is None:
                    total, weights = part, closeness
                else:
                    cv2.add(total, part, dst=total)
                    cv2.add(weights, closeness, dst=weights)
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
            detail = cv2.subtract(fused, cv2.GaussianBlur(fused, (0, 0), 1.2))
            detail = cv2.multiply(detail, self.lift)
            fused = cv2.add(fused, cv2.max(cv2.min(detail, LIFT_LIMIT), -LIFT_LIMIT))
        chroma = (present.chroma if self.size == present.source_size else
                  cv2.resize(present.chroma, self.size, interpolation=cv2.INTER_LANCZOS4))
        luma = cv2.convertScaleAbs(cv2.max(fused, 0.0))
        return cv2.cvtColor(cv2.merge([luma, *cv2.split(chroma)]), cv2.COLOR_YCrCb2BGR)


def fuse_stream(frames: Iterable[np.ndarray], percent: int = 100, calm: float = 0.5, lift: float = 0.0,
                radius: int = RADIUS, flow_width: int = FLOW_WIDTH,
                preset: int = cv2.DISOPTICAL_FLOW_PRESET_MEDIUM,
                interpolation: int = cv2.INTER_CUBIC) -> Iterator[np.ndarray]:
    """Yield fused (and optionally enlarged) frames, each delayed by ``radius`` frames.

    Frames are fused in parallel on the shared pool, with the full-size work
    on the GPU when one is available.
    """
    if percent == 100 and calm <= 0 and lift <= 0:
        yield from frames
        return
    # The GPU path covers the min/max median networks, up to five frames.
    on_gpu = radius <= 2 and gpu.enabled()
    fusers: dict[tuple[int, int], PerThread] = {}

    def prepare(frame: np.ndarray) -> _Layer:
        return _Layer(frame, target_size(frame.shape[1], frame.shape[0], percent), flow_width, on_gpu)

    def work(window: list[_Layer], center: int) -> np.ndarray:
        size = target_size(*window[center].source_size, percent)
        if size not in fusers:
            fusers[size] = PerThread(lambda: _Fuser(size, calm, lift, preset, interpolation, on_gpu))
        return fusers[size].get().frame(window, center)

    yield from window_map(frames, radius, prepare, work)
