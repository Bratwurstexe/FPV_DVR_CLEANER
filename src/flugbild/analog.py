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
* Unbacked bands. Stripes that keep reappearing at the same edge survive the
  temporal step. Their top and bottom are colour edges without a brightness
  edge; there the colour is refilled from a local luma-to-colour model.

The current luma is kept. Chroma is re-estimated from three earlier and three
later frames aligned to the current one with optical flow, weighted by how
well their luma matches and by the two fault scores, with a Cauchy reweighting
against outliers. Where no aligned neighbour carries usable colour, the
estimate fades to neutral grey instead of spreading a stripe.
"""

from __future__ import annotations

from typing import Iterable, Iterator

import cv2
import numpy as np

from .pipeline import PerThread, window_map


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

    def __init__(self, frame: np.ndarray, flow_width: int = FLOW_WIDTH) -> None:
        self.ycc = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
        height, width = frame.shape[:2]
        self.size = _grid(width, height)
        coarse = cv2.resize(self.ycc, self.size, interpolation=cv2.INTER_AREA).astype(np.float32)
        self.luma = cv2.GaussianBlur(coarse[:, :, 0], (3, 3), 0)
        # Chroma as one complex plane: Cr is the real and Cb the imaginary part.
        self.chroma = ((coarse[:, :, 1] - 128) + 1j * (coarse[:, :, 2] - 128)).astype(np.complex64)
        factor = min(1.0, flow_width / width)
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


def _unbacked_bands(chroma: np.ndarray, luma: np.ndarray) -> np.ndarray:
    """Score 0..1 for horizontal colour bands that the brightness does not explain.

    Real colour edges almost always come with a brightness edge (another
    material, another light). The top and bottom of a carrier band are colour
    edges in flat brightness, running across a long stretch of the row.
    """
    height = chroma.shape[0]
    colour_step = np.abs(chroma[1:] - chroma[:-1])
    light_step = np.abs(luma[1:] - luma[:-1])
    vivid = np.maximum(np.abs(chroma[1:]), np.abs(chroma[:-1])) > 10
    lone = ((colour_step > 8) & (light_step < 5) & vivid).astype(np.float32)
    lone = np.vstack([lone, lone[-1:]])
    run = cv2.blur(lone, (31, 1))
    edges = np.clip((run - 0.3) / 0.3, 0, 1)
    reach = max(2, height // 30)
    bands = cv2.dilate(edges, np.ones((2 * reach + 1, 1), np.uint8))
    return cv2.GaussianBlur(bands, (9, 3), 0).astype(np.float32)


def _luma_guided(chroma: np.ndarray, luma: np.ndarray, doubt: np.ndarray) -> np.ndarray:
    """Chroma predicted from luma by a local linear model fitted on trusted pixels.

    Within a neighbourhood, one surface keeps its colour while its brightness
    varies, so colour is modelled as a linear function of luma there. The fit
    ignores doubtful pixels and then fills them. Without enough trusted pixels
    nearby the prediction fades to neutral grey.
    """
    trust = (1 - doubt).astype(np.float32)
    window = (61, 25)

    def mean(values: np.ndarray) -> np.ndarray:
        return cv2.blur(values * trust, window) / np.maximum(cv2.blur(trust, window), 1e-3)

    light = mean(luma)
    spread = mean(luma * luma) - light * light
    predicted = np.zeros(chroma.shape, np.complex64)
    for part, unit in ((chroma.real, 1), (chroma.imag, 1j)):
        colour = mean(part)
        slope = (mean(luma * part) - light * colour) / (spread + 25.0)
        offset = colour - slope * light
        slope, offset = cv2.blur(slope, (31, 13)), cv2.blur(offset, (31, 13))
        predicted += unit * (slope * luma + offset)
    coverage = np.clip(cv2.blur(trust, window) / 0.3, 0, 1)
    return predicted * coverage


def _follow_luma(chroma: np.ndarray, luma: np.ndarray, amount: float) -> np.ndarray:
    """Edge-aware chroma smoothing guided by luma (local linear guided filter)."""
    radius = (4, 2)  # the repair grid is already 4x coarser horizontally, 2x vertically
    eps = 20.0 + 120.0 * amount

    def box(values: np.ndarray) -> np.ndarray:
        return cv2.blur(values, (2 * radius[0] + 1, 2 * radius[1] + 1))

    light = box(luma)
    spread = box(luma * luma) - light * light
    smoothed = np.zeros_like(chroma)
    for part, unit in ((chroma.real.astype(np.float32), 1), (chroma.imag.astype(np.float32), 1j)):
        colour = box(part)
        slope = (box(luma * part) - light * colour) / (spread + eps)
        offset = colour - slope * light
        smoothed += unit * (box(slope) * luma + box(offset))
    return chroma + (smoothed - chroma) * (0.6 * amount)


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
    # Pixel-by-pixel decisions leave blotches where neighbouring pixels were
    # treated differently; let the repair fade in and out over an area.
    strength = np.maximum(strength, cv2.GaussianBlur(strength, (0, 0), 6) * 0.9).astype(np.float32)
    change = (goal - present.chroma) * strength
    # Bands that keep reappearing at the same edge survive the temporal step;
    # catch them in the repaired frame by their missing brightness edge.
    repaired = present.chroma + change
    bands = _unbacked_bands(repaired, present.luma) * (1 - overlay)
    if bands.max() > 0.05:
        change += (_luma_guided(repaired, present.luma, bands) - repaired) * bands
    if amount:
        # Colour carries little detail of its own in an analog signal: let it
        # follow the brightness contours, which survive the transmission.
        settled = _follow_luma(present.chroma + change, present.luma, amount / 100)
        change = settled - present.chroma
    height, width = present.ycc.shape[:2]
    planes = cv2.resize(np.dstack([change.real, change.imag]).astype(np.float32),
                        (width, height), interpolation=cv2.INTER_LINEAR)
    result = present.ycc.astype(np.float32)
    result[:, :, 1:] += planes
    return cv2.cvtColor(np.clip(result, 0, 255).astype(np.uint8), cv2.COLOR_YCrCb2BGR)


def repair_stream(frames: Iterable[np.ndarray], amount: int, osd: np.ndarray | None = None,
                  radius: int = RADIUS, flow_width: int = FLOW_WIDTH) -> Iterator[np.ndarray]:
    """Yield repaired frames in order, each delayed by ``radius`` input frames.

    ``osd`` is an optional full-resolution 0/255 mask of overlay graphics.
    Frames are repaired in parallel on the shared pool.
    """
    if amount == 0:
        yield from frames
        return
    aligners = PerThread(_Aligner)
    overlays: dict[tuple[int, int], np.ndarray] = {}

    def overlay_for(size: tuple[int, int]) -> np.ndarray:
        if size not in overlays:
            width, height = size
            overlays[size] = (np.zeros((height, width), np.float32) if osd is None else
                              cv2.resize(osd, size, interpolation=cv2.INTER_AREA).astype(np.float32) / 255)
        return overlays[size]

    def work(window: list[_FrameData], center: int) -> np.ndarray:
        return repair_window(aligners.get(), window, center, amount, overlay_for(window[center].size))

    yield from window_map(frames, radius, lambda frame: _FrameData(frame, flow_width), work)
