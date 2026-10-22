"""Temporal chroma repair for analog FPV recordings.

Analog receivers keep luma comparatively stable while chroma fails frame by
frame. Two faults are handled:

* Colour dropout. When the receiver loses the colour subcarrier, a frame or a
  band of rows is recorded with practically no chroma at all. This is tested
  per row against an absolute chroma level, independent of other frames; a
  genuinely colourless scene simply stays grey.
* Hue tears. A disturbed subcarrier phase paints rainbow stripes: saturated
  colour whose hue jumps from one row to the next. The vertical hue change of
  saturated chroma marks these stripes inside a single frame.

The current luma is kept. Chroma is re-estimated from three earlier and three
later frames aligned to the current one with optical flow, weighted by how
well their luma matches and by the two fault scores, with a Cauchy reweighting
against outliers. Where no aligned neighbour carries usable colour, the
estimate fades to neutral grey instead of spreading a stripe.
"""

from __future__ import annotations

from collections import deque
from typing import Iterable, Iterator

import cv2
import numpy as np


RADIUS = 3
FLOW_WIDTH = 320
LUMA_MATCH = 16.0
OUTLIER_SCALE = 10.0
DROPOUT_LEVEL = 1.5
DROPOUT_RAMP = 2.5


def _grid(width: int, height: int) -> tuple[int, int]:
    # Analog chroma carries far less horizontal than vertical detail, so the
    # repair grid keeps every second row but only every fourth column.
    return max(8, width // 4), max(8, height // 2)


def _hue_tears(chroma: np.ndarray) -> np.ndarray:
    """Score 0..1 for rainbow stripes: saturated colour whose hue jumps between rows."""
    height = chroma.shape[0]
    level = cv2.blur(np.abs(chroma), (15, 1))
    turn = np.abs(np.angle(chroma[1:] * np.conj(chroma[:-1])))
    turn = np.vstack([turn, turn[-1:]]) * (np.abs(chroma) > 10)
    jumps = cv2.blur(turn.astype(np.float32), (15, 1))
    tears = np.clip((level - 10) / 18, 0, 1) * np.clip(jumps / 0.6, 0, 1)
    # A stripe is a few rows tall; its edges carry the jumps, so spread them.
    reach = max(1, height // 60)
    tears = cv2.dilate(tears, np.ones((2 * reach + 1, 1), np.uint8))
    return cv2.GaussianBlur(tears, (9, 3), 0).astype(np.float32)


class _FrameData:
    """Per-frame data computed once and shared by every window it appears in."""

    def __init__(self, frame: np.ndarray) -> None:
        self.ycc = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
        height, width = frame.shape[:2]
        self.size = _grid(width, height)
        coarse = cv2.resize(self.ycc, self.size, interpolation=cv2.INTER_AREA).astype(np.float32)
        self.luma = cv2.GaussianBlur(coarse[:, :, 0], (3, 3), 0)
        # Chroma as one complex plane: Cr is the real and Cb the imaginary part.
        self.chroma = ((coarse[:, :, 1] - 128) + 1j * (coarse[:, :, 2] - 128)).astype(np.complex64)
        factor = min(1.0, FLOW_WIDTH / width)
        self.small = cv2.resize(self.ycc[:, :, 0], (max(8, round(width * factor)),
                                                    max(8, round(height * factor))),
                                interpolation=cv2.INTER_AREA)
        self.tears = _hue_tears(self.chroma)
        rows = (np.abs(self.chroma) * (1 - self.tears)).mean(axis=1)
        rows = cv2.blur(rows[:, None], (1, 5))[:, 0]
        self.colour = np.clip((rows - DROPOUT_LEVEL) / DROPOUT_RAMP, 0, 1).astype(np.float32)[:, None]


class _Aligner:
    def __init__(self) -> None:
        self.flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)
        self.grid: tuple[np.ndarray, np.ndarray] | None = None

    def warp(self, center: _FrameData,
             other: _FrameData) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Luma, chroma, tear score and colour level of ``other`` on ``center``'s grid."""
        width, height = center.size
        if self.grid is None or self.grid[0].shape != (height, width):
            self.grid = tuple(np.meshgrid(np.arange(width, dtype=np.float32),
                                          np.arange(height, dtype=np.float32)))
        flow = cv2.resize(self.flow.calc(center.small, other.small, None), (width, height),
                          interpolation=cv2.INTER_LINEAR)
        map_x = self.grid[0] + flow[:, :, 0] * (width / center.small.shape[1])
        map_y = self.grid[1] + flow[:, :, 1] * (height / center.small.shape[0])
        colour = np.broadcast_to(other.colour, other.luma.shape)
        planes = np.dstack([other.luma, other.chroma.real, other.chroma.imag, other.tears, colour])
        moved = cv2.remap(planes, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        return (moved[:, :, 0], (moved[:, :, 1] + 1j * moved[:, :, 2]).astype(np.complex64),
                moved[:, :, 3], moved[:, :, 4])


def repair_window(aligner: _Aligner, window: list[_FrameData], center: int,
                  amount: int, overlay: np.ndarray) -> np.ndarray:
    """Return the repaired BGR frame for ``window[center]``.

    ``overlay`` marks OSD graphics on the repair grid (0..1). Their coloured
    bars are drawn, not received, and are never treated as hue tears.
    """
    present = window[center]
    aligned = []
    for index, other in enumerate(window):
        if index == center:
            continue
        luma, chroma, tears, colour = aligner.warp(present, other)
        moved_fit = np.exp(-np.square((luma - present.luma) / LUMA_MATCH))
        # Static screen content (OSD, a still scene) matches best without motion.
        still_fit = np.exp(-np.square((other.luma - present.luma) / LUMA_MATCH))
        still = still_fit > moved_fit
        aligned.append((np.where(still, other.chroma, chroma),
                        np.where(still, other.tears, tears) * (1 - overlay),
                        np.where(still, np.broadcast_to(other.colour, still.shape), colour),
                        np.maximum(moved_fit, still_fit)))
    # A hue tear is transient. An edge that shows the same jump in the aligned
    # neighbours is part of the picture (or a drawn bar), not a broken carrier.
    lasting = (np.mean([item[1] for item in aligned], axis=0) if aligned
               else np.zeros_like(present.tears))
    own_tears = present.tears * (1 - overlay) * (1 - lasting)
    samples = [present.chroma]
    weights = [present.colour * (1 - own_tears)]
    for chroma, tears, colour, fit in aligned:
        samples.append(chroma)
        # A small floor keeps colour available for a dropout frame during fast
        # motion, when no neighbour passes the luma check.
        weights.append(colour * (1 - tears * (1 - lasting)) * (fit + 0.05))
    stack = np.stack(samples)
    prior = np.stack(weights)
    estimate = (stack * prior).sum(axis=0) / np.maximum(prior.sum(axis=0), 1e-3)
    for _ in range(2):
        cauchy = prior / (1 + np.square(np.abs(stack - estimate) / OUTLIER_SCALE))
        estimate = (stack * cauchy).sum(axis=0) / np.maximum(cauchy.sum(axis=0), 1e-3)
    backing = np.minimum(prior[1:].sum(axis=0), 1)
    # Without usable neighbours the goal fades to neutral grey: a stripe is
    # never copied into a frame that lacks trustworthy colour.
    goal = estimate * backing
    drained = 1 - present.colour * np.clip(np.abs(present.chroma) / np.maximum(
        0.6 * np.abs(estimate), 1.0), 0, 1)
    stray = np.clip((np.abs(present.chroma - estimate) - 8) / 12, 0, 1) * backing
    calming = amount / 100 * 0.9 * np.minimum(prior[1:].sum(axis=0) / 2, 1)
    strength = np.maximum(np.maximum(drained, stray), np.maximum(own_tears, calming))
    change = (goal - present.chroma) * strength
    height, width = present.ycc.shape[:2]
    planes = cv2.resize(np.dstack([change.real, change.imag]).astype(np.float32),
                        (width, height), interpolation=cv2.INTER_LINEAR)
    result = present.ycc.astype(np.float32)
    result[:, :, 1:] += planes
    return cv2.cvtColor(np.clip(result, 0, 255).astype(np.uint8), cv2.COLOR_YCrCb2BGR)


def repair_stream(frames: Iterable[np.ndarray], amount: int, osd: np.ndarray | None = None,
                  radius: int = RADIUS) -> Iterator[np.ndarray]:
    """Yield repaired frames in order, each delayed by ``radius`` input frames.

    ``osd`` is an optional full-resolution 0/255 mask of overlay graphics.
    """
    if amount == 0:
        yield from frames
        return
    aligner = _Aligner()
    window: deque[_FrameData] = deque()
    overlay = None
    first = 0
    emitted = 0
    for count, frame in enumerate(frames, start=1):
        window.append(_FrameData(frame))
        if overlay is None:
            width, height = window[0].size
            overlay = (np.zeros((height, width), np.float32) if osd is None else
                       cv2.resize(osd, (width, height), interpolation=cv2.INTER_AREA).astype(np.float32) / 255)
        while emitted + radius < count:
            yield repair_window(aligner, list(window), emitted - first, amount, overlay)
            emitted += 1
            while emitted - first > radius:
                window.popleft()
                first += 1
    while emitted < first + len(window):
        yield repair_window(aligner, list(window), emitted - first, amount, overlay)
        emitted += 1
        while emitted - first > radius and len(window) > 1:
            window.popleft()
            first += 1
