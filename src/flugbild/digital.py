"""Breakup repair for HDZero recordings.

When the digital link drops packets, the decoder paints parts of a frame,
or all of it, with blocks of random colour ("confetti"), or with streaks of
such blocks. The frames around it are usually intact.

* Finding: per 16 px block, the energy of fine colour detail (chroma minus
  its 3x3 blur, measured at half size). Real pictures have little of it, and
  motion blur even less, while confetti is made of it. A block counts as
  broken when its energy is high and clearly lower in both neighbouring
  frames, so colourful things that are always there, like the OSD, do not.
* Repair: the nearest frames before and after that are clean at those blocks
  are aligned with optical flow measured between the two of them, never in
  the broken frame, and the in-between picture is blended into the broken
  blocks with a soft edge. This works even when most of the frame is broken.
"""

from __future__ import annotations

from typing import Iterable, Iterator

import cv2
import numpy as np

from .pipeline import PerThread, window_map


RADIUS = 3
BLOCK = 16
FLOW_WIDTH = 320
# A frame is repaired only from this many broken blocks on (about 1 % of a
# 720p picture). Below that it is mostly OSD digits that changed.
MIN_BLOCKS = 30
# Fine colour energy of one pixel that makes it a confetti speck.
SPECK = 24.0
# Share of broken blocks from which the whole frame is rebuilt.
WHOLE_FRAME = 0.35


def level(amount: int) -> float:
    """Fine-colour energy that marks a broken block; higher amounts find more."""
    return 10.0 - 6.0 * amount / 100


class _Frame:
    """Per-frame data shared by every window the frame appears in."""

    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame
        height, width = frame.shape[:2]
        half = cv2.resize(frame, (max(2, width // 2), max(2, height // 2)), interpolation=cv2.INTER_AREA)
        chroma = cv2.cvtColor(half, cv2.COLOR_BGR2YCrCb)[:, :, 1:].astype(np.float32)
        fine = cv2.absdiff(chroma, cv2.blur(chroma, (3, 3)))
        fine = fine[:, :, 0] + fine[:, :, 1]
        grid = (max(1, width // BLOCK), max(1, height // BLOCK))
        self.energy = cv2.resize(fine, grid, interpolation=cv2.INTER_AREA)
        # Single confetti specks are too small to lift a block's mean energy;
        # their share of the block's pixels shows them.
        self.specks = cv2.resize((fine > SPECK).astype(np.float32), grid, interpolation=cv2.INTER_AREA)

    def small(self) -> np.ndarray:
        height, width = self.frame.shape[:2]
        factor = min(1.0, FLOW_WIDTH / width)
        return cv2.resize(cv2.cvtColor(self.frame, cv2.COLOR_BGR2GRAY),
                          (max(8, round(width * factor)), max(8, round(height * factor))),
                          interpolation=cv2.INTER_AREA)


def broken_blocks(window: list[_Frame], center: int, threshold: float) -> np.ndarray:
    """Boolean block grid of confetti that is new in ``window[center]``."""
    present = window[center]
    around = [window[index] for index in (center - 1, center + 1) if 0 <= index < len(window)]
    if not around:
        return np.zeros(present.energy.shape, bool)
    # New in this frame means missing in both neighbours: OSD text that just
    # appeared is still there in the next frame, confetti is not.
    calm = np.maximum.reduce([frame.energy for frame in around])
    clear = np.maximum.reduce([frame.specks for frame in around])
    if np.median(present.energy) > 2.5 * max(np.median(frame.energy) for frame in around) + 0.5:
        # Confetti all over, some of it too grey to stand out block by block.
        return np.ones(present.energy.shape, bool)
    blocks = (present.energy > threshold / 2) & (present.energy > 4 * calm + 1.5)
    blocks |= (present.specks > 0.02) & (present.specks > 3 * clear + 0.02)
    # The outermost blocks flicker with edge effects of the recording.
    blocks[[0, -1], :] = False
    blocks[:, [0, -1]] = False
    if blocks.mean() > WHOLE_FRAME:
        blocks[:] = True
    return blocks


def _clean_side(window: list[_Frame], center: int, step: int, blocks: np.ndarray,
                threshold: float) -> int | None:
    """Nearest frame in direction ``step`` that is calm at nearly all ``blocks``."""
    index = center + step
    while 0 <= index < len(window):
        if (window[index].energy[blocks] <= threshold).mean() >= 0.9:
            return index
        index += step
    return None


class _Mender:
    def __init__(self) -> None:
        self.flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)

    def between(self, before: _Frame, after: _Frame, share: float) -> np.ndarray:
        """Picture at ``share`` of the way from ``before`` to ``after`` (0..1)."""
        first, last = before.small(), after.small()
        height, width = before.frame.shape[:2]
        flow = cv2.resize(self.flow.calc(first, last, None), (width, height), interpolation=cv2.INTER_LINEAR)
        flow[:, :, 0] *= width / first.shape[1]
        flow[:, :, 1] *= height / first.shape[0]
        columns, rows = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
        # Backward warp with the flow at the target pixel, the usual
        # approximation for a short step between two frames.
        from_before = cv2.remap(before.frame, columns - share * flow[:, :, 0], rows - share * flow[:, :, 1],
                                cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        from_after = cv2.remap(after.frame, columns + (1 - share) * flow[:, :, 0],
                               rows + (1 - share) * flow[:, :, 1],
                               cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        return cv2.addWeighted(from_before, 1 - share, from_after, share, 0)


def repair_window(mender: _Mender, window: list[_Frame], center: int, threshold: float) -> np.ndarray:
    present = window[center]
    blocks = broken_blocks(window, center, threshold)
    if blocks.sum() < MIN_BLOCKS:
        return present.frame
    # Confetti often scatters around the blocks that were found.
    blocks = cv2.dilate(blocks.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    before = _clean_side(window, center, -1, blocks, threshold)
    after = _clean_side(window, center, 1, blocks, threshold)
    if before is None and after is None:
        return present.frame
    if before is None or after is None:
        fill = window[after if before is None else before].frame
    else:
        fill = mender.between(window[before], window[after], (center - before) / (after - before))
    height, width = present.frame.shape[:2]
    mask = cv2.resize(blocks.astype(np.float32), (width, height), interpolation=cv2.INTER_NEAREST)
    mask = cv2.GaussianBlur(mask, (0, 0), BLOCK / 4)
    return cv2.blendLinear(fill, present.frame, mask, 1 - mask)


def repair_stream(frames: Iterable[np.ndarray], amount: int, radius: int = RADIUS) -> Iterator[np.ndarray]:
    """Yield frames with HDZero breakups replaced, each delayed by ``radius`` frames."""
    if amount == 0:
        yield from frames
        return
    threshold = level(amount)
    menders = PerThread(_Mender)
    yield from window_map(frames, radius, _Frame,
                          lambda window, center: repair_window(menders.get(), window, center, threshold))
