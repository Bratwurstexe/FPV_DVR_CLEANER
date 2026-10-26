"""Finishing look: colour grade, glow, lens fringe, vignette and film grain.

An optional last frame stage, after repair, OSD removal, noise reduction and
upscaling. It changes nothing unless it is switched on with a strength above
zero. The steps work on the picture as floating-point RGB 0..1, in this order:

* Colour grade: a little more contrast and saturation, cool shadows and warm
  highlights, both from the luminance of the incoming picture.
* Glow: the brightest parts, found and blurred at a quarter of the size, are
  added back on top.
* Lens fringe: red is scaled out from the picture centre and blue in, so edges
  near the border get thin coloured seams; the centre stays untouched.
* Vignette: the corners get darker, the middle keeps its brightness.
* Film grain: gaussian noise at half the size, the same for every colour. Its
  seed is the frame number, so an export can be repeated exactly.

The result is clipped to 0..1 once, at the end. Frames are BGR uint8 like
everywhere else in the engine; the colour constants below are written in that
order.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Iterator

import cv2
import numpy as np

from .pipeline import ordered_map
from .recipe import Arbeitsrezept


# Rec. 709 luminance weights, blue, green, red.
LUMA = np.array([0.0722, 0.7152, 0.2126], np.float32)
# Tint per unit of grade: towards teal in the shadows, towards orange in the highlights.
SHADOW_TINT = np.array([0.02, 0.006, -0.012], np.float32)
HIGHLIGHT_TINT = np.array([-0.022, 0.012, 0.03], np.float32)
GRADE_GAIN = 0.12
GLOW_LEVEL = 0.72
GLOW_SIGMA = 9.0
GLOW_GAIN = 1.6
FRINGE_SCALE = 0.004
VIGNETTE_DEPTH = 0.55
VIGNETTE_CURVE = 1.1
GRAIN_SIZE = 0.03
GRAIN_SEED = 0x4C4F4F4B


@dataclass(frozen=True)
class Look:
    """Strengths of the five steps, each 0..1; all zero is no look at all."""

    grade: float = 0.0
    glow: float = 0.0
    fringe: float = 0.0
    vignette: float = 0.0
    grain: float = 0.0

    @property
    def active(self) -> bool:
        return any(value > 0 for value in (self.grade, self.glow, self.fringe, self.vignette, self.grain))

    @classmethod
    def of(cls, recipe: Arbeitsrezept) -> Look:
        """The look a recipe asks for; switched off it is the empty look."""
        if not recipe.look:
            return cls()
        return cls(float(recipe.look_farbstimmung), float(recipe.look_leuchten), float(recipe.look_farbsaum),
                   float(recipe.look_vignette), float(recipe.look_koernung))


# The presets of the desktop app, for reference and tests.
PRESETS = {
    "off": Look(),
    "clean": Look(grade=0.3, glow=0.2, vignette=0.25, grain=0.1, fringe=0.0),
    "cinematic": Look(grade=0.6, glow=0.45, vignette=0.5, grain=0.25, fringe=0.35),
    "punchy": Look(grade=1.0, glow=0.7, vignette=0.6, grain=0.15, fringe=0.5),
    "retro-fpv": Look(grade=0.5, glow=0.8, vignette=0.85, grain=0.9, fringe=1.0),
}


def look_active(recipe: Arbeitsrezept) -> bool:
    return Look.of(recipe).active


@lru_cache(maxsize=8)
def _grade_matrix(amount: float) -> np.ndarray:
    """The grade as one affine colour matrix.

    With gain g, luminance L and tints S (shadows) and H (highlights) the steps
    contrast = (p - .5) g + .5, saturated = L + (contrast - L) g and
    graded = saturated + a (S (1 - L) + H L) add up to
    graded = g² p + (1 - g + a (H - S)) L + (.5 g (1 - g) + a S).
    """
    gain = 1 + GRADE_GAIN * amount
    per_luma = (1 - gain) + amount * (HIGHLIGHT_TINT - SHADOW_TINT)
    offset = 0.5 * gain * (1 - gain) + amount * SHADOW_TINT
    matrix = np.zeros((3, 4), np.float32)
    matrix[:, :3] = gain * gain * np.eye(3, dtype=np.float32) + np.outer(per_luma, LUMA)
    matrix[:, 3] = offset
    return matrix


def _grade(picture: np.ndarray, amount: float) -> np.ndarray:
    return cv2.transform(picture, _grade_matrix(amount))


def _glow(picture: np.ndarray, amount: float) -> np.ndarray:
    height, width = picture.shape[:2]
    small = cv2.resize(picture, (max(1, width // 4), max(1, height // 4)), interpolation=cv2.INTER_AREA)
    bright = cv2.GaussianBlur(np.maximum(small - GLOW_LEVEL, 0), (0, 0), GLOW_SIGMA)
    glow = cv2.resize(bright, (width, height), interpolation=cv2.INTER_LINEAR)
    return cv2.scaleAdd(glow, GLOW_GAIN * amount, picture)


@lru_cache(maxsize=8)
def _fringe_maps(width: int, height: int, scale: float) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    """Lookup maps for red (drawn from nearer the centre) and blue (from further out)."""
    centre_x, centre_y = (width - 1) / 2, (height - 1) / 2
    x_grid, y_grid = np.meshgrid(np.arange(width, dtype=np.float32) - centre_x,
                                 np.arange(height, dtype=np.float32) - centre_y)
    maps = []
    for factor in (1 - scale, 1 + scale):
        maps.append(cv2.convertMaps(centre_x + x_grid * factor, centre_y + y_grid * factor, cv2.CV_16SC2))
    return tuple(maps)


def _fringe(picture: np.ndarray, amount: float) -> np.ndarray:
    height, width = picture.shape[:2]
    red_maps, blue_maps = _fringe_maps(width, height, FRINGE_SCALE * amount)
    blue, green, red = cv2.split(picture)
    red = cv2.remap(red, *red_maps, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    blue = cv2.remap(blue, *blue_maps, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return cv2.merge((blue, green, red))


@lru_cache(maxsize=8)
def _vignette_shade(width: int, height: int, amount: float) -> np.ndarray:
    """1 - depth clip(r², 0, 1) ^ 1.1 with r² = (dx² + dy²) / 2, dx and dy from -1 to 1 across the picture."""
    dx = (np.arange(width, dtype=np.float32) + 0.5) / width * 2 - 1
    dy = (np.arange(height, dtype=np.float32) + 0.5) / height * 2 - 1
    radius = (dx[None, :] ** 2 + dy[:, None] ** 2) / 2
    shade = 1 - (VIGNETTE_DEPTH * amount) * np.power(np.clip(radius, 0, 1), VIGNETTE_CURVE)
    shade = cv2.cvtColor(shade.astype(np.float32), cv2.COLOR_GRAY2BGR)
    shade.setflags(write=False)
    return shade


def _vignette(picture: np.ndarray, amount: float) -> np.ndarray:
    height, width = picture.shape[:2]
    return cv2.multiply(picture, _vignette_shade(width, height, amount))


def _grain(picture: np.ndarray, amount: float, index: int) -> np.ndarray:
    height, width = picture.shape[:2]
    noise = np.random.default_rng((GRAIN_SEED, abs(int(index)))).standard_normal(
        (max(1, (height + 1) // 2), max(1, (width + 1) // 2)), dtype=np.float32)
    noise = cv2.resize(noise, (width, height), interpolation=cv2.INTER_LINEAR)
    return cv2.scaleAdd(cv2.cvtColor(noise, cv2.COLOR_GRAY2BGR), GRAIN_SIZE * amount, picture)


def look_frame(frame: np.ndarray, look: Look, index: int = 0) -> np.ndarray:
    """``frame`` (BGR uint8) with the look; ``index`` is its frame number, the grain seed."""
    if not look.active:
        return frame
    picture = cv2.addWeighted(frame, 1 / 255, frame, 0, 0, dtype=cv2.CV_32F)
    if look.grade > 0:
        picture = _grade(picture, look.grade)
    if look.glow > 0:
        picture = _glow(picture, look.glow)
    if look.fringe > 0:
        picture = _fringe(picture, look.fringe)
    if look.vignette > 0:
        picture = _vignette(picture, look.vignette)
    if look.grain > 0:
        picture = _grain(picture, look.grain, index)
    # Back to 8 bits; the conversion rounds and clips to 0..255, which is 0..1.
    return cv2.addWeighted(picture, 255, picture, 0, 0, dtype=cv2.CV_8U)


def look_stream(frames: Iterable[np.ndarray], look: Look, first_index: int = 0) -> Iterator[np.ndarray]:
    """The look on every frame, on all cores; frame numbers count from ``first_index``."""
    return ordered_map(lambda job: look_frame(job[1], look, job[0]), enumerate(frames, start=first_index))
