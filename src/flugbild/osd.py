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
from concurrent.futures import Future
from functools import partial
from pathlib import Path
import subprocess
from typing import Callable, Iterable, Iterator

import cv2
import numpy as np

from .media import PROCESS_FLAGS
from .pipeline import PerThread, ordered_map, submit


PROBES = 40
WINDOW = 6
FIT_LIMIT = 14.0
# An element of the clip mask counts as on screen when this share of its
# steady edges is found in a frame, and stays removed for HOLD frames after.
SHOWN_SHARE = 0.35
HOLD = 150


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
        # 3x3 Sobel steps of 8-bit pixels fit in int16; half the memory of
        # float32 for the frames a sliding window keeps.
        step_x = cv2.Sobel(frame, cv2.CV_16S, 1, 0, ksize=3)
        step_y = cv2.Sobel(frame, cv2.CV_16S, 0, 1, ksize=3)
        length = cv2.magnitude(step_x.astype(np.float32), step_y.astype(np.float32))
        return step_x, step_y, np.rint(length).astype(np.int16)

    def add(self, parts: tuple[np.ndarray, np.ndarray, np.ndarray], sign: int = 1) -> None:
        combine = cv2.add if sign > 0 else cv2.subtract
        for total, part in zip((self.vector_x, self.vector_y, self.length), parts):
            combine(total, part, dst=total, dtype=cv2.CV_32F)
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


def _text_rows(shapes: np.ndarray, frame: np.ndarray) -> np.ndarray:
    """Keep only shapes that look like a line of OSD text.

    A straight scene edge that slides along itself while the drone flies
    keeps its direction just like a drawn overlay. Warnings such as CRASH FLIP
    differ: a flat, level row whose letters are full of upright strokes.
    """
    height = shapes.shape[0]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    across = np.abs(cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3))
    down = np.abs(cv2.Sobel(gray, cv2.CV_16S, 0, 1, ksize=3))
    strong = np.maximum(across, down) > 60
    upright = strong & (across > down)
    kept = np.zeros_like(shapes)
    outlines, _ = cv2.findContours(shapes, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for outline in outlines:
        (_, _), (side_a, side_b), angle = cv2.minAreaRect(outline)
        # Angle of the long side against the horizontal, 0..90 degrees.
        tilt = abs(angle if side_a >= side_b else angle - 90) % 180
        tilt = min(tilt, 180 - tilt)
        x, y, w, h = cv2.boundingRect(outline)
        if tilt > 8 or h > height * 0.1:
            continue
        region = np.zeros((h, w), np.uint8)
        cv2.drawContours(region, [outline], -1, 1, cv2.FILLED, offset=(-x, -y))
        inside = region > 0
        edges = int(strong[y:y + h, x:x + w][inside].sum())
        if edges and upright[y:y + h, x:x + w][inside].sum() >= 0.3 * edges:
            cv2.drawContours(kept, [outline], -1, 255, cv2.FILLED)
    return kept


def _orientation(step_x: np.ndarray, step_y: np.ndarray) -> np.ndarray:
    """Edge orientation in whole degrees, 1..180 (0 is kept for "no edge")."""
    return (np.degrees(np.arctan2(step_y, step_x)).astype(np.int16) % 180 + 1).astype(np.uint8)


def find_osd(frames: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mask (0/255) of overlay graphics from BGR probes of one clip, and its guide.

    The guide holds, inside the mask, the orientation of every edge that held
    still over the probes, 0 elsewhere. It tells whether an element is on
    screen in a given frame: digits that change have no steady edges, while
    the icon, point and unit around them do.
    """
    if len(frames) < 8:
        raise ValueError("Too few readable frames to find the OSD")
    coherence = _Coherence(frames.shape[1:3])
    for parts in ordered_map(_Coherence.gradients, frames):
        coherence.add(parts)
    picture = np.zeros(frames.shape[1:3], np.uint8)
    for frame in frames[:: max(1, len(frames) // 8)]:
        picture |= _picture_area(frame)
    edges = coherence.still_edges(0.7, 80.0)
    mask = _overlay_shapes(edges, picture)
    # The colour channel with the strongest steady edge sets the direction.
    strongest = cv2.magnitude(coherence.vector_x, coherence.vector_y).argmax(axis=2)[:, :, None]
    step_x = np.take_along_axis(coherence.vector_x, strongest, axis=2)[:, :, 0]
    step_y = np.take_along_axis(coherence.vector_y, strongest, axis=2)[:, :, 0]
    guide = _orientation(step_x, step_y) * ((edges > 0) & (mask > 0))
    return mask, guide


def detect_osd(source: Path, start: float, duration: float,
               width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    return find_osd(_probe_frames(source, start, duration, width, height))


def _shown_share(frame: np.ndarray, guide: np.ndarray, labels: np.ndarray, elements: int) -> np.ndarray:
    """Per mask element, the share of its guide edges found with the same orientation in ``frame``."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    step_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    step_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    known = guide > 0
    now = _orientation(step_x[known], step_y[known]).astype(np.int16)
    turn = np.abs(now - guide[known].astype(np.int16))
    turn = np.minimum(turn, 180 - turn)
    strong = np.hypot(step_x[known], step_y[known]) > 40
    matched = strong & (turn <= 20)
    where = labels[known]
    total = np.bincount(where, minlength=elements)
    return np.bincount(where[matched], minlength=elements) / np.maximum(total, 1)


class _Patcher:
    """Paste each overlay region from the neighbour that fits its rim best."""

    def __init__(self, preset: int = cv2.DISOPTICAL_FLOW_PRESET_FAST) -> None:
        self.flow = cv2.DISOpticalFlow_create(preset)

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

    @staticmethod
    def blocked(mask: np.ndarray, small_shape: tuple[int, int]) -> np.ndarray:
        return cv2.dilate(cv2.resize(mask, small_shape[::-1], interpolation=cv2.INTER_NEAREST),
                          np.ones((5, 5), np.uint8))

    def apply(self, frame: np.ndarray, mask: np.ndarray,
              neighbours: list[tuple[np.ndarray, np.ndarray]], fill_scale: float = 1.0) -> np.ndarray:
        """``neighbours`` holds (frame, small-scale flow from ``frame`` into it)."""
        result = frame.copy()
        if not mask.any() or not neighbours:
            return _inpaint(result, mask, _picture_area(frame), fill_scale) if mask.any() else result
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
            # Nearest lookup of the small-scale flow, the same for every neighbour.
            at_x = np.minimum((columns * scale_x).astype(int), small_width - 1)
            at_y = np.minimum((rows * scale_y).astype(int), small_height - 1)
            best, best_fit = None, FIT_LIMIT
            for other, flow in neighbours:
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
                offset = _brightness_plane(here - there, rim)
                fit = float(np.abs(here - there - offset[rim]).mean())
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
            result = _inpaint(result, unresolved, _picture_area(frame), fill_scale)
        # Soften the seam so analog noise differences do not draw an outline. The
        # feather runs outside the graphic only: inside it the refill stays at full
        # weight, otherwise the overlay would shine through along its edge.
        edge = cv2.GaussianBlur(cv2.dilate(mask, np.ones((5, 5), np.uint8)).astype(np.float32) / 255, (7, 7), 0)
        edge = np.maximum(edge, mask.astype(np.float32) / 255)
        return cv2.blendLinear(result, frame, edge, 1 - edge)


def _brightness_plane(difference: np.ndarray, rim: np.ndarray) -> np.ndarray:
    """Per channel, the plane a + b*x + c*y fitted to the rim differences.

    A single offset leaves a visible patch wherever the scene has a gradient,
    such as sky or a vignette; a plane follows that gradient across the hole.
    Few rim pixels fall back to the mean offset.
    """
    height, width = rim.shape
    ys, xs = np.nonzero(rim)
    if len(xs) < 24:
        mean = difference.mean(axis=0)
        return np.broadcast_to(mean, (height, width, 3)).astype(np.float32)
    design = np.column_stack([np.ones(len(xs), np.float32), xs / max(1, width - 1), ys / max(1, height - 1)])
    coefficients, *_ = np.linalg.lstsq(design, difference, rcond=None)
    # Keep the slope modest: a steep plane would invent a gradient from noisy rims.
    coefficients[1:] = np.clip(coefficients[1:], -40, 40)
    grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float32) / max(1, width - 1),
                                 np.arange(height, dtype=np.float32) / max(1, height - 1))
    plane = (coefficients[0][None, None, :] + grid_x[:, :, None] * coefficients[1][None, None, :]
             + grid_y[:, :, None] * coefficients[2][None, None, :])
    return plane.astype(np.float32)


def _inpaint(frame: np.ndarray, mask: np.ndarray, picture: np.ndarray,
             scale: float = 1.0) -> np.ndarray:
    """Spatial fallback: inpaint each region from the picture around it.

    Navier-Stokes inpainting carries the surrounding gradients into the hole
    and gives the same fill from frame to frame; the fast-marching method left
    faint streaks that flickered on analog footage.

    Below a ``scale`` of one the regions are filled at a reduced size and
    enlarged again: the fill is smooth anyway, and the cost falls with area.
    """
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
        area = result[top:bottom, left:right]
        gaps = unknown[top:bottom, left:right]
        if scale < 1 and min(area.shape[:2]) * scale >= 8:
            size = (max(1, round(area.shape[1] * scale)), max(1, round(area.shape[0] * scale)))
            small_gaps = cv2.dilate(cv2.resize(gaps, size, interpolation=cv2.INTER_NEAREST),
                                    np.ones((3, 3), np.uint8))
            painted = cv2.inpaint(cv2.resize(area, size, interpolation=cv2.INTER_AREA), small_gaps,
                                  max(2, round(5 * scale)), cv2.INPAINT_NS)
            painted = cv2.resize(painted, area.shape[1::-1], interpolation=cv2.INTER_LINEAR)
        else:
            painted = cv2.inpaint(area, gaps, 5, cv2.INPAINT_NS)
        area[hole] = painted[hole]
    return result


def remove_osd(frame: np.ndarray, mask: np.ndarray,
               neighbours: list[np.ndarray] | None = None) -> np.ndarray:
    """Remove the OSD from one frame, preferring content from ``neighbours``."""
    patcher = _Patcher()
    present = patcher._small(frame)
    blocked = patcher.blocked(mask, present.shape)
    return patcher.apply(frame, mask, [(other, patcher._motion(present, patcher._small(other), blocked))
                                       for other in neighbours or []])


def _chained(steps: dict[tuple[int, int], Future], start: int, end: int,
             grid: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
    """Small-scale flow from frame ``start`` into frame ``end``.

    One flow estimate cannot follow large shifts across several frames, but
    each step between consecutive frames is small. Steps are computed once
    and joined here for every window they fall into.
    """
    total = np.zeros((*grid[0].shape, 2), np.float32)
    direction = 1 if end > start else -1
    for here in range(start, end, direction):
        step = steps[here, here + direction].result()
        total += cv2.remap(step, grid[0] + total[:, :, 0], grid[1] + total[:, :, 1],
                           cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return total


class _Probe:
    """Per-frame data for overlay removal, computed on the pool."""

    def __init__(self, frame: np.ndarray, share: Callable[[np.ndarray], np.ndarray] | None = None) -> None:
        self.frame = frame
        self.small = _Patcher._small(frame)
        tiny = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 90), interpolation=cv2.INTER_AREA)
        self.tiny = tiny.astype(np.int16)
        self.parts = _Coherence.gradients(frame)
        self.picture = _picture_area(frame)
        self.share = share(frame) if share else None


def remove_stream(frames: Iterable[np.ndarray], mask: np.ndarray, guide: np.ndarray | None = None,
                  radius: int = WINDOW, preset: int = cv2.DISOPTICAL_FLOW_PRESET_FAST,
                  fill_scale: float = 1.0, always: np.ndarray | None = None,
                  never: np.ndarray | None = None) -> Iterator[np.ndarray]:
    """Yield frames with the clip mask plus briefly shown overlays painted out.

    The clip mask gathers everything shown at some point of the cut, such as
    the stats screen after landing. With the ``guide`` from ``find_osd`` an
    element is only painted out around the frames that show it.

    The sliding sums that find brief overlays are kept in order here; flows,
    overlay shapes and the refill of each frame run on the shared pool.
    ``fill_scale`` below one inpaints uncovered regions at a reduced size.

    ``always`` and ``never`` are hand corrections (0/255, see handmask.py):
    ``always`` is painted out in every frame, ``never`` in none, whatever the
    on-screen check and the search for brief overlays decide.
    """
    patchers = PerThread(lambda: _Patcher(preset))
    widen = np.ones((3, 11), np.uint8)
    # Analog chroma smears colour sideways past the drawn graphic.
    flat = cv2.dilate(mask, widen)
    elements, labels = cv2.connectedComponents(mask, connectivity=8)
    share = None
    if guide is not None:
        share = partial(_shown_share, guide=guide, labels=labels, elements=elements)
        # Elements without steady edges cannot be checked; they stay removed.
        unguided = np.bincount(labels[guide > 0], minlength=elements) == 0
    forced = cv2.dilate(always, widen) if always is not None and always.any() else None
    spared = cv2.bitwise_not(never) if never is not None and never.any() else None

    def finish(frame: np.ndarray, edges: np.ndarray | None, shown: np.ndarray | None, picture: np.ndarray,
               neighbours: list[np.ndarray], flows: list[Callable[[], np.ndarray]]) -> np.ndarray:
        regions = flat
        if shown is not None and not shown[1:].all():
            regions = cv2.dilate(shown[labels].astype(np.uint8) * 255, widen)
        if edges is not None:
            brief = _text_rows(_overlay_shapes(edges, picture), frame)
            regions = regions | cv2.dilate(brief, widen)
        if forced is not None:
            regions = regions | forced
        if spared is not None:
            regions = regions & spared
        return patchers.get().apply(frame, regions, [(other, flow()) for other, flow in zip(neighbours, flows)],
                                    fill_scale)

    def jobs() -> Iterator[tuple]:
        window: deque[_Probe] = deque()
        movement: deque[float] = deque()
        steps: dict[tuple[int, int], Future] = {}
        coherence = picture = blocked = grid = None
        first = emitted = 0
        last_seen = np.full(elements, -HOLD - 1)

        def job(index: int) -> tuple:
            here = first + index
            edges = shown = None
            # Brief overlays can only be told apart from the scene while it moves.
            if len(window) > 4 and np.median(movement) > 4.0:
                edges = coherence.still_edges(0.85, 80.0)
            if share is not None:
                # Seen up to ``radius`` frames ahead or HOLD frames before:
                # blinking warnings and changing digits do not flicker.
                shown = (here - last_seen <= HOLD) | unguided
                shown[0] = False
            order = sorted((item for item in range(len(window)) if item != index),
                           key=lambda item: abs(item - index))
            # The window slides on before the job runs: hand over the steps it needs.
            reach = dict(steps)
            flows = [partial(_chained, reach, here, first + item, grid) for item in order]
            return (window[index].frame, edges, shown, picture.copy(),
                    [window[item].frame for item in order], flows)

        def slide() -> None:
            nonlocal first
            coherence.add(window.popleft().parts, -1)
            movement.popleft()
            for key in [key for key in steps if first in key]:
                del steps[key]
            first += 1

        # Each probe holds three gradient planes; keep only a few ahead.
        probes = ordered_map(lambda frame: _Probe(frame, share), frames, ahead=8)
        for count, probe in enumerate(probes, start=1):
            if probe.share is not None:
                last_seen[probe.share >= SHOWN_SHARE] = count - 1
            if coherence is None:
                coherence = _Coherence(probe.frame.shape[:2])
                picture = probe.picture.copy()
                blocked = _Patcher.blocked(mask, probe.small.shape)
                grid = tuple(np.meshgrid(np.arange(probe.small.shape[1], dtype=np.float32),
                                         np.arange(probe.small.shape[0], dtype=np.float32)))
            else:
                picture |= probe.picture
            movement.append(float(np.abs(probe.tiny - window[-1].tiny).mean()) if window else 0.0)
            coherence.add(probe.parts)
            if window:
                last = count - 2
                before = window[-1].small
                steps[last, last + 1] = submit(lambda a, b: patchers.get()._motion(a, b, blocked),
                                               before, probe.small)
                steps[last + 1, last] = submit(lambda a, b: patchers.get()._motion(a, b, blocked),
                                               probe.small, before)
            window.append(probe)
            while emitted + radius < count:
                yield job(emitted - first)
                emitted += 1
                while emitted - first > radius:
                    slide()
        while emitted < first + len(window):
            yield job(emitted - first)
            emitted += 1
            while emitted - first > radius and len(window) > 1:
                slide()

    yield from ordered_map(lambda arguments: finish(*arguments), jobs())


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
