"""Find on-screen display graphics in a clip and paint them out.

Detection uses gradient coherence. An overlay edge points the same way in every
frame, while edges of the moving scene point in all directions over time. Per
colour channel, the summed gradient vectors are compared with the summed
gradient lengths; values near one mark edges that hold still. Bars and icons
are found by their coloured outlines and filled to the enclosed area.

* Across probes spread over the whole cut this finds permanent overlays.
* A sliding window over the export stream finds overlays that are shown only
  briefly, such as warnings, but only while the scene itself is moving.

Removal works per overlay region: of the neighbouring frames, the one whose
motion-aligned content matches the ring around the region best is pasted in
with its brightness offset corrected. Without a good match the region is
inpainted from its surroundings.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
import subprocess
from typing import Iterable, Iterator

import cv2
import numpy as np

from .media import PROCESS_FLAGS


PROBES = 40
WINDOW = 6
FIT_LIMIT = 14.0


def _probe_frames(source: Path, start: float, duration: float,
                  width: int, height: int) -> np.ndarray:
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "quiet"]
    if duration >= 40:
        # Long cuts: keyframes alone are spread widely enough and decode fast.
        args.extend(["-skip_frame", "nokey"])
    if start:
        args.extend(["-ss", str(start)])
    args.extend(["-i", str(source), "-t", str(duration), "-map", "0:v:0",
                 "-vf", f"fps={max(PROBES / duration, 0.01):.6f}",
                 "-frames:v", str(PROBES), "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"])
    result = subprocess.run(args, capture_output=True, timeout=600, creationflags=PROCESS_FLAGS)
    size = width * height * 3
    count = len(result.stdout) // size
    return np.frombuffer(result.stdout[:count * size], np.uint8).reshape(count, height, width, 3)


class _Coherence:
    """Running per-channel sums of gradient vectors and gradient lengths."""

    def __init__(self, shape: tuple[int, int]) -> None:
        self.vector_x = np.zeros((*shape, 3), np.float32)
        self.vector_y = np.zeros((*shape, 3), np.float32)
        self.length = np.zeros((*shape, 3), np.float32)
        self.count = 0

    @staticmethod
    def gradients(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        source = frame.astype(np.float32)
        step_x = cv2.Sobel(source, cv2.CV_32F, 1, 0, ksize=3)
        step_y = cv2.Sobel(source, cv2.CV_32F, 0, 1, ksize=3)
        return step_x, step_y, cv2.magnitude(step_x, step_y)

    def add(self, parts: tuple[np.ndarray, np.ndarray, np.ndarray], sign: int = 1) -> None:
        self.vector_x += sign * parts[0]
        self.vector_y += sign * parts[1]
        self.length += sign * parts[2]
        self.count += sign

    def still_edges(self, steadiness: float, strength: float) -> np.ndarray:
        """Edges whose direction held over the frames and that are clearly drawn."""
        steady = cv2.magnitude(self.vector_x, self.vector_y) / np.maximum(self.length, 1e-3)
        drawn = self.length / max(self.count, 1)
        return ((steady > steadiness) & (drawn > strength)).any(axis=2).astype(np.uint8)


def _picture_area(frame: np.ndarray) -> np.ndarray:
    """Active picture without letterbox bars and their static border."""
    luma = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.erode((luma > 14).astype(np.uint8), np.ones((9, 9), np.uint8))


def _overlay_shapes(edges: np.ndarray, picture: np.ndarray) -> np.ndarray:
    """Join edge fragments into text lines and shapes; drop what is not overlay."""
    height, width = edges.shape
    # The border between letterbox and picture holds still as well; leave it
    # out so it cannot chain the overlays along the edge into one shape.
    edges = edges & cv2.erode(picture, np.ones((7, 7), np.uint8))
    # Long straight runs are frame borders, not OSD.
    for run in ((max(3, width // 4), 1), (1, max(3, height // 6))):
        edges &= 1 - cv2.dilate(cv2.morphologyEx(edges, cv2.MORPH_OPEN, np.ones(run[::-1], np.uint8)),
                                np.ones((5, 5), np.uint8))
    lines = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, width // 64), 3)))
    lines = cv2.morphologyEx(lines, cv2.MORPH_CLOSE, np.ones((max(3, height // 120), 3), np.uint8))
    columns = np.flatnonzero(picture.any(axis=0))
    left_edge, right_edge = (columns[0], columns[-1]) if len(columns) else (0, width - 1)
    shapes = np.zeros((height, width), np.uint8)
    outlines, _ = cv2.findContours(lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for outline in outlines:
        x, y, w, h = cv2.boundingRect(outline)
        hugs_side = x - left_edge < 12 or right_edge - (x + w) < 12
        spans_picture = x - left_edge < 12 and right_edge - (x + w) < 12
        # A whole OSD row may join into one wide shape; it stays a short strip.
        too_big = h > height * 0.22 or (w > width * 0.45 and h > height * 0.1)
        drawn = int(edges[y:y + h, x:x + w].sum())
        if drawn < 40 or spans_picture or (hugs_side and w < 1.5 * h):
            continue
        if too_big:
            # Several elements merged with something larger: keep only the
            # still edges themselves, without filling the merged outline.
            region = np.zeros_like(shapes)
            cv2.drawContours(region, [outline], -1, 1, cv2.FILLED)
            shapes |= ((region & lines) * 255).astype(np.uint8)
            continue
        cv2.drawContours(shapes, [outline], -1, 255, cv2.FILLED)
    shapes = cv2.dilate(shapes, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    return shapes & (picture * 255)


def find_osd(frames: np.ndarray) -> np.ndarray:
    """Return a 0/255 mask of overlay graphics from BGR probes of one clip."""
    if len(frames) < 8:
        raise ValueError("Too few readable frames to find the OSD")
    coherence = _Coherence(frames.shape[1:3])
    for frame in frames:
        coherence.add(_Coherence.gradients(frame))
    picture = np.zeros(frames.shape[1:3], np.uint8)
    for frame in frames[:: max(1, len(frames) // 8)]:
        picture |= _picture_area(frame)
    return _overlay_shapes(coherence.still_edges(0.7, 80.0), picture)


def detect_osd(source: Path, start: float, duration: float, width: int, height: int) -> np.ndarray:
    return find_osd(_probe_frames(source, start, duration, width, height))


class _Patcher:
    """Paste each overlay region from the neighbour that fits its rim best."""

    def __init__(self) -> None:
        self.flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)

    @staticmethod
    def _small(frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        size = (min(width, 320), max(8, round(height * min(width, 320) / width)))
        return cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), size, interpolation=cv2.INTER_AREA)

    def _motion(self, present: np.ndarray, other: np.ndarray, blocked: np.ndarray) -> np.ndarray:
        flow = self.flow.calc(present, other, None)
        # Overlays do not move and would hold the flow at zero; carry the
        # surrounding scene motion into those areas instead.
        open_area = (blocked == 0).astype(np.float32)
        reach = cv2.blur(open_area, (41, 41))
        for channel in range(2):
            spread = cv2.blur(flow[:, :, channel] * open_area, (41, 41)) / np.maximum(reach, 1e-3)
            flow[:, :, channel] = np.where(blocked > 0, spread, flow[:, :, channel])
        return flow

    def blocked(self, mask: np.ndarray, small_shape: tuple[int, int]) -> np.ndarray:
        return cv2.dilate(cv2.resize(mask, small_shape[::-1], interpolation=cv2.INTER_NEAREST),
                          np.ones((5, 5), np.uint8))

    def apply(self, frame: np.ndarray, mask: np.ndarray,
              neighbours: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
        """``neighbours`` holds (frame, small-scale flow from ``frame`` into it)."""
        result = frame.copy()
        if not mask.any() or not neighbours:
            return _inpaint(result, mask, _picture_area(frame)) if mask.any() else result
        height, width = mask.shape
        small_height, small_width = neighbours[0][1].shape[:2]
        scale_x, scale_y = small_width / width, small_height / height
        count, regions, boxes, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        unresolved = np.zeros_like(mask)
        for region in range(1, count):
            x, y, w, h, _ = boxes[region]
            left, top = max(0, x - 10), max(0, y - 10)
            right, bottom = min(width, x + w + 10), min(height, y + h + 10)
            inside = regions[top:bottom, left:right] == region
            rim = (cv2.dilate(inside.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0) & ~inside
            rim &= mask[top:bottom, left:right] == 0
            columns, rows = np.meshgrid(np.arange(left, right, dtype=np.float32),
                                        np.arange(top, bottom, dtype=np.float32))
            best, best_fit = None, FIT_LIMIT
            for other, flow in neighbours:
                at_x = np.minimum((columns * scale_x).astype(int), flow.shape[1] - 1)
                at_y = np.minimum((rows * scale_y).astype(int), flow.shape[0] - 1)
                map_x = columns + flow[at_y, at_x, 0] / scale_x
                map_y = rows + flow[at_y, at_x, 1] / scale_y
                landing = cv2.remap(mask, map_x, map_y, cv2.INTER_NEAREST, borderValue=255)
                if (landing[inside] > 0).mean() > 0.05:
                    continue  # The neighbour shows overlay or picture edge there too.
                patch = cv2.remap(other, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
                if not rim.any():
                    best, best_fit = (patch, np.zeros(3, np.float32)), 0.0
                    break
                here = frame[top:bottom, left:right][rim].astype(np.float32)
                there = patch[rim].astype(np.float32)
                offset = (here - there).mean(axis=0)
                fit = float(np.abs(here - there - offset).mean())
                if fit < best_fit:
                    best, best_fit = (patch, offset), fit
                    if fit < FIT_LIMIT / 2:
                        break  # Neighbours are ordered by distance; this one is good enough.
            if best is None:
                unresolved[top:bottom, left:right][inside] = 255
                continue
            patch, offset = best
            corrected = np.clip(patch.astype(np.float32) + offset, 0, 255).astype(np.uint8)
            result[top:bottom, left:right][inside] = corrected[inside]
        if unresolved.any():
            result = _inpaint(result, unresolved, _picture_area(frame))
        # Soften the seam so analog noise differences do not draw an outline.
        edge = cv2.GaussianBlur(mask.astype(np.float32) / 255, (7, 7), 0)[:, :, None]
        return (result * edge + frame * (1 - edge)).astype(np.uint8)


def _inpaint(frame: np.ndarray, mask: np.ndarray, picture: np.ndarray) -> np.ndarray:
    """Spatial fallback: inpaint each region from the picture around it."""
    result = frame.copy()
    # Letterbox pixels next to a hole are treated as unknown so no black bleeds
    # into the fill; only a narrow band, to keep the inpainting work small.
    letterbox = (1 - cv2.dilate(picture, np.ones((15, 15), np.uint8))) * 255
    unknown = mask | (letterbox & cv2.dilate(mask, np.ones((17, 17), np.uint8)))
    count, _, boxes, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    height, width = mask.shape
    for region in range(1, count):
        x, y, w, h, _ = boxes[region]
        left, top = max(0, x - 12), max(0, y - 12)
        right, bottom = min(width, x + w + 12), min(height, y + h + 12)
        hole = mask[top:bottom, left:right] > 0
        painted = cv2.inpaint(result[top:bottom, left:right], unknown[top:bottom, left:right],
                              5, cv2.INPAINT_TELEA)
        result[top:bottom, left:right][hole] = painted[hole]
    return result


def remove_osd(frame: np.ndarray, mask: np.ndarray,
               neighbours: list[np.ndarray] | None = None) -> np.ndarray:
    """Remove the OSD from one frame, preferring content from ``neighbours``."""
    patcher = _Patcher()
    present = patcher._small(frame)
    blocked = patcher.blocked(mask, present.shape)
    return patcher.apply(frame, mask, [(other, patcher._motion(present, patcher._small(other), blocked))
                                       for other in neighbours or []])


class _Chain:
    """Flows between consecutive frames, joined to reach farther neighbours.

    One flow estimate cannot follow large shifts across several frames, but
    each step between consecutive frames is small. A step is computed once and
    reused for every window it falls into.
    """

    def __init__(self, patcher: _Patcher, mask: np.ndarray) -> None:
        self.patcher = patcher
        self.mask = mask
        self.small: dict[int, np.ndarray] = {}
        self.steps: dict[tuple[int, int], np.ndarray] = {}
        self.grid: tuple[np.ndarray, np.ndarray] | None = None
        self.blocked: np.ndarray | None = None

    def add(self, number: int, frame: np.ndarray) -> None:
        self.small[number] = self.patcher._small(frame)
        if self.blocked is None:
            shape = self.small[number].shape
            self.blocked = self.patcher.blocked(self.mask, shape)
            self.grid = tuple(np.meshgrid(np.arange(shape[1], dtype=np.float32),
                                          np.arange(shape[0], dtype=np.float32)))

    def drop(self, number: int) -> None:
        self.small.pop(number, None)
        for key in [key for key in self.steps if number in key]:
            del self.steps[key]

    def flow(self, start: int, end: int) -> np.ndarray:
        """Small-scale flow from frame ``start`` into frame ``end``."""
        total = np.zeros((*self.small[start].shape, 2), np.float32)
        direction = 1 if end > start else -1
        for here in range(start, end, direction):
            key = (here, here + direction)
            if key not in self.steps:
                self.steps[key] = self.patcher._motion(self.small[here], self.small[here + direction],
                                                       self.blocked)
            step = self.steps[key]
            total += cv2.remap(step, self.grid[0] + total[:, :, 0], self.grid[1] + total[:, :, 1],
                               cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        return total


def remove_stream(frames: Iterable[np.ndarray], mask: np.ndarray,
                  radius: int = WINDOW) -> Iterator[np.ndarray]:
    """Yield frames with the clip mask plus briefly shown overlays painted out."""
    window: deque[tuple[np.ndarray, tuple[np.ndarray, np.ndarray, np.ndarray], float]] = deque()
    patcher = _Patcher()
    chain = _Chain(patcher, mask)
    coherence = None
    picture = None

    first = 0

    def finish(index: int) -> np.ndarray:
        frame = window[index][0]
        regions = mask
        # Brief overlays can only be told apart from the scene while it moves.
        if len(window) > 4 and np.median([item[2] for item in window]) > 4.0:
            brief = _overlay_shapes(coherence.still_edges(0.85, 80.0), picture)
            regions = mask | brief
        # Analog chroma smears colour sideways past the drawn graphic.
        regions = cv2.dilate(regions, np.ones((3, 11), np.uint8))
        order = sorted((item for item in range(len(window)) if item != index),
                       key=lambda item: abs(item - index))
        return patcher.apply(frame, regions, [(window[item][0], chain.flow(first + index, first + item))
                                              for item in order])

    emitted = 0
    previous = None
    for count, frame in enumerate(frames, start=1):
        if coherence is None:
            coherence = _Coherence(frame.shape[:2])
            picture = _picture_area(frame)
        else:
            picture |= _picture_area(frame)
        small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 90), interpolation=cv2.INTER_AREA)
        movement = 0.0 if previous is None else float(np.abs(small.astype(np.int16) - previous).mean())
        previous = small.astype(np.int16)
        parts = _Coherence.gradients(frame)
        coherence.add(parts)
        chain.add(count - 1, frame)
        window.append((frame, parts, movement))
        while emitted + radius < count:
            yield finish(emitted - first)
            emitted += 1
            while emitted - first > radius:
                coherence.add(window.popleft()[1], -1)
                chain.drop(first)
                first += 1
    while emitted < first + len(window):
        yield finish(emitted - first)
        emitted += 1
        while emitted - first > radius and len(window) > 1:
            coherence.add(window.popleft()[1], -1)
            chain.drop(first)
            first += 1


def preview(frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Mark the detected OSD in orange on one frame."""
    shown = frame.copy()
    tint = np.zeros_like(frame)
    tint[:] = (27, 119, 239)
    area = mask > 0
    shown[area] = cv2.addWeighted(frame, 0.35, tint, 0.65, 0)[area]
    outline = cv2.morphologyEx(mask, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    shown[outline] = (27, 119, 239)
    return shown
