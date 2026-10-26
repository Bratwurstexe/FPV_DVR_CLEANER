"""Hand corrections of the detected OSD mask, painted in the desktop mask editor.

The automatic search can miss small icons or a short label such as REC, or
mark a piece of the scene that only looks like an overlay. The editor stores
the user's corrections as one 8-bit grey PNG in source-frame coordinates (the
full recorded frame, black bars included):

* 255: always removed and refilled ("add"),
* 128: never removed ("erase"),
* 0:   left to the automatic search.

Merged into the clip mask, added areas are removed in every frame of the cut,
regardless of whether the automatic search thinks they are on screen, and
erased areas are never touched, not even by the search for brief warnings.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np


ADD = 255
ERASE = 128

# Layers of a merged overlay: mask, guide, always removed, never removed.
Layers = tuple[np.ndarray, np.ndarray]


def read(path: str | Path | None, shape: tuple[int, int]) -> Layers | None:
    """(add, erase) as 0/255 masks of ``shape`` (height, width), or None without a file."""
    if not path:
        return None
    try:
        # imdecode instead of imread: file names with umlauts work on Windows too.
        image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_GRAYSCALE)
    except (OSError, ValueError):
        image = None
    if image is None:
        raise ValueError("Could not read the hand-corrected OSD mask")
    if image.shape != tuple(shape):
        image = cv2.resize(image, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)
    add = image >= (ADD + ERASE) // 2
    erase = (image >= ERASE // 2) & ~add
    return add.astype(np.uint8) * 255, erase.astype(np.uint8) * 255


def write(path: str | Path, add: np.ndarray, erase: np.ndarray) -> None:
    """Store the two layers as one PNG; ``add`` wins where both are set."""
    image = np.zeros(add.shape, np.uint8)
    image[erase > 0] = ERASE
    image[add > 0] = ADD
    okay, data = cv2.imencode(".png", image)
    if not okay:
        raise ValueError("Could not encode the OSD mask")
    Path(path).write_bytes(data.tobytes())


def digest(path: str | Path | None) -> str:
    """Short hash of the correction file, '' without one; for cache keys."""
    if not path or not Path(path).is_file():
        return ""
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()[:16]


def merge(overlay: tuple[np.ndarray, ...] | None, layers: Layers | None,
          shape: tuple[int, int]) -> tuple[np.ndarray, ...] | None:
    """The automatic (mask, guide) with the hand corrections applied.

    Returns (mask, guide, always, never): erased pixels leave the mask, added
    ones join it. Inside both layers the guide is cleared, so the on-screen
    check of the automatic search never gates them. Without corrections the
    overlay is returned unchanged.
    """
    if layers is None:
        return overlay
    add, erase = layers
    if overlay is None:
        mask = np.zeros(shape, np.uint8)
        guide = np.zeros(shape, np.uint8)
    else:
        mask, guide = overlay[0], overlay[1]
    touched = (add > 0) | (erase > 0)
    merged = np.where(erase > 0, 0, mask).astype(np.uint8)
    merged[add > 0] = 255
    cleared = np.where(touched, 0, guide).astype(guide.dtype)
    return merged, cleared, add, erase
