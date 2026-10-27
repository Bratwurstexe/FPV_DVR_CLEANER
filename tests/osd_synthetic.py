"""Synthetic OSD footage with a known ground truth, for the OSD reading tests.

The pixel font below was drawn for these tests: 6 x 9 design pixels per
character, each design pixel 2 x 2 screen pixels in a 12 x 18 cell like an
analog OSD chip, white with a black outline. It is deliberately not the font
the reader compares against, so the tests check that reading generalises.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


FONT = {
    "0": ["011110", "110011", "110111", "111011", "110011", "110011", "110011", "011110", "000000"],
    "1": ["001100", "011100", "111100", "001100", "001100", "001100", "001100", "111111", "000000"],
    "2": ["011110", "110011", "000011", "000110", "001100", "011000", "110000", "111111", "000000"],
    "3": ["111110", "000011", "000011", "011110", "000011", "000011", "000011", "111110", "000000"],
    "4": ["000110", "001110", "011110", "110110", "111111", "000110", "000110", "000110", "000000"],
    "5": ["111111", "110000", "110000", "111110", "000011", "000011", "110011", "011110", "000000"],
    "6": ["011110", "110000", "110000", "111110", "110011", "110011", "110011", "011110", "000000"],
    "7": ["111111", "000011", "000110", "000110", "001100", "001100", "011000", "011000", "000000"],
    "8": ["011110", "110011", "110011", "011110", "110011", "110011", "110011", "011110", "000000"],
    "9": ["011110", "110011", "110011", "110011", "011111", "000011", "000011", "011110", "000000"],
    ".": ["000000", "000000", "000000", "000000", "000000", "000000", "001100", "001100", "000000"],
    ":": ["000000", "000000", "001100", "001100", "000000", "001100", "001100", "000000", "000000"],
    "V": ["110011", "110011", "110011", "110011", "110011", "011110", "011110", "001100", "000000"],
    "A": ["001100", "011110", "110011", "110011", "111111", "110011", "110011", "110011", "000000"],
    "L": ["110000", "110000", "110000", "110000", "110000", "110000", "110000", "111111", "000000"],
    "O": ["011110", "110011", "110011", "110011", "110011", "110011", "110011", "011110", "000000"],
    "W": ["110011", "110011", "110011", "110011", "111111", "111111", "110011", "110011", "000000"],
    "B": ["111110", "110011", "110011", "111110", "110011", "110011", "110011", "111110", "000000"],
    "T": ["111111", "001100", "001100", "001100", "001100", "001100", "001100", "001100", "000000"],
    "E": ["111111", "110000", "110000", "111110", "110000", "110000", "110000", "111111", "000000"],
    "R": ["111110", "110011", "110011", "111110", "111000", "110110", "110011", "110011", "000000"],
    "Y": ["110011", "110011", "110011", "011110", "001100", "001100", "001100", "001100", "000000"],
    "Q": ["011110", "110011", "110011", "110011", "110011", "110111", "110110", "011011", "000000"],
    "U": ["110011", "110011", "110011", "110011", "110011", "110011", "110011", "011110", "000000"],
    "D": ["111100", "110110", "110011", "110011", "110011", "110011", "110110", "111100", "000000"],
    " ": ["000000"] * 9,
    # Symbols of this test font (stand-ins for the chip's icon glyphs).
    "bat": ["001100", "111111", "100001", "100001", "111111", "111111", "111111", "111111", "000000"],
    "mah": ["101010", "111110", "101010", "000000", "011000", "100100", "111100", "100100", "000000"],
    "clk": ["011110", "100101", "100101", "100111", "100001", "100001", "011110", "000000", "000000"],
}


def glyph_image(name: str, scale: int) -> tuple[np.ndarray, np.ndarray]:
    """White fill and black outline masks of one cell (12 x 18 design at ``scale``)."""
    rows = FONT[name]
    design = np.array([[int(bit) for bit in row] for row in rows], np.uint8)
    # 10 x 16 inside the 12 x 18 cell: one pixel of space around every character.
    fill = np.pad(cv2.resize(design, (10, 16), interpolation=cv2.INTER_NEAREST), 1)
    outline = cv2.dilate(fill, np.ones((3, 3), np.uint8)) & (1 - fill)
    size = (12 * scale, 18 * scale)
    return (cv2.resize(fill, size, interpolation=cv2.INTER_NEAREST),
            cv2.resize(outline, size, interpolation=cv2.INTER_NEAREST))


@dataclass
class Layout:
    frame: tuple[int, int]
    origin: tuple[int, int]  # top-left of cell (0, 0)
    cell: tuple[int, int]  # pixels
    columns: int
    rows: int
    scale: float


ANALOG = Layout((1280, 720), (160, 0), (32, 45), 30, 16, 2.6667)
HD = Layout((1280, 720), (40, 36), (24, 36), 50, 18, 2.0)


def background(index: int, size: tuple[int, int], seed: int = 3) -> np.ndarray:
    """Moving textured terrain under a sky gradient, pillarboxed to 4:3."""
    width, height = size
    random = np.random.default_rng(seed)
    relief = cv2.resize(random.integers(30, 220, size=(24, 80), dtype=np.uint8), (width * 3, height),
                        interpolation=cv2.INTER_CUBIC)
    shift = (37 * index) % (width * 2)
    part = relief[:, shift:shift + width].astype(np.float32)
    sky = np.linspace(200, 90, height, dtype=np.float32)[:, None]
    mix = np.clip((np.arange(height) - height * (0.35 + 0.1 * np.sin(index / 7))) / 60, 0, 1)[:, None]
    gray = sky * (1 - mix) + part * mix
    frame = np.dstack([gray * 1.05, gray * 0.95, gray * 0.8]).clip(0, 255).astype(np.uint8)
    noise = np.random.default_rng(seed * 1000 + index).normal(0, 6, frame.shape)
    return np.clip(frame + noise, 0, 255).astype(np.uint8)


def draw_text(frame: np.ndarray, layout: Layout, column: int, row: int, items: list[str]) -> None:
    for offset, name in enumerate(items):
        if name == " ":
            continue
        scale = layout.scale
        fill, outline = glyph_image(name, 1)
        width, height = layout.cell
        fill = cv2.resize(fill, (width, height), interpolation=cv2.INTER_NEAREST)
        outline = cv2.resize(outline, (width, height), interpolation=cv2.INTER_NEAREST)
        x = layout.origin[0] + (column + offset) * width
        y = layout.origin[1] + row * height
        area = frame[y:y + height, x:x + width]
        area[outline > 0] = (16, 16, 16)
        area[fill > 0] = (240, 240, 240)


def chars(text: str) -> list[str]:
    return list(text)


@dataclass
class Truth:
    time: list[int]  # flight seconds per frame
    voltage: list[float]
    current: list[float]
    mah: list[int]
    warning: list[str | None]


def clip(layout: Layout, count: int, rate: float = 5.0, breakups: tuple[int, ...] = (),
         noise: float = 0.0, seed: int = 3, analog_blur: bool | None = None) -> tuple[list[np.ndarray], Truth]:
    """``count`` frames at ``rate`` per second with a ticking timer and a falling voltage."""
    frames = []
    truth = Truth([], [], [], [], [])
    blur = analog_blur if analog_blur is not None else layout is ANALOG
    for index in range(count):
        seconds = 47 + int(index / rate)  # starts at 00:47, crosses a minute
        voltage = round(16.8 - 0.035 * index, 1)
        current = round(12 + 6 * np.sin(index / 9), 1)
        mah = 120 + int(index * 2.5)
        warning = "LOW BATTERY" if voltage < 15.4 else None
        frame = background(index, layout.frame, seed)
        if layout is ANALOG:
            frame[:, :160] = 0
            frame[:, 1120:] = 0
        bottom = layout.rows - 2
        draw_text(frame, layout, 2, bottom, ["bat"] + chars(f"{voltage:4.1f}V"))
        draw_text(frame, layout, 10, bottom, chars(f"{current:4.1f}A"))
        draw_text(frame, layout, 17, bottom, chars(f"{mah:4d}") + ["mah"])
        minutes, rest = divmod(seconds, 60)
        draw_text(frame, layout, layout.columns - 7, bottom, ["clk"] + chars(f"{minutes:02d}:{rest:02d}"))
        draw_text(frame, layout, 2, 1, chars("QUAD"))
        if warning and index % 10 < 7:  # blinking
            start = (layout.columns - len(warning)) // 2
            draw_text(frame, layout, start, layout.rows // 2, chars(warning))
        if blur:
            frame = cv2.GaussianBlur(frame, (3, 3), 0.9)
        if noise:
            jitter = np.random.default_rng(seed + index).normal(0, noise, frame.shape)
            frame = np.clip(frame + jitter, 0, 255).astype(np.uint8)
        if index in breakups:
            random = np.random.default_rng(index)
            blocks = random.integers(0, 255, size=(layout.frame[1] // 16, layout.frame[0] // 16, 3), dtype=np.uint8)
            confetti = cv2.resize(blocks, layout.frame, interpolation=cv2.INTER_NEAREST)
            band = slice(layout.frame[1] // 2, layout.frame[1])
            frame[band] = confetti[band]
        frames.append(frame)
        truth.time.append(seconds)
        truth.voltage.append(voltage)
        truth.current.append(current)
        truth.mah.append(mah)
        truth.warning.append(warning if index % 10 < 7 else None)
    return frames, truth
