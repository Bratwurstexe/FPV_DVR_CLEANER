"""Animated README logo: the quad from the app's progress bar, hovering over a filling bar.

Usage: python tools/make_logo.py docs/media/logo-quad.gif

Drawn at four times the size and scaled down, with a transparent background
and a light outline, so it reads on GitHub's light and dark theme alike.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

WIDTH, HEIGHT = 168, 112
SCALE = 4
FRAMES = 48
FRAME_MS = 40

FRAME = (44, 50, 56)
OUTLINE = (236, 239, 241)
MOTOR = (75, 84, 93)
PROP = (138, 147, 155)
PROP_TIP = (240, 138, 58)
BATTERY = (255, 200, 87)
CAMERA = (17, 21, 24)
LENS = (95, 168, 232)
TAIL = (229, 72, 77)
TRACK = (210, 213, 208)
SHADOW = (0, 0, 0, 55)
FILL = [(166, 70, 0), (240, 138, 58), (255, 200, 87)]


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def gradient(t: float):
    return lerp(FILL[0], FILL[1], t / 0.7) if t < 0.7 else lerp(FILL[1], FILL[2], (t - 0.7) / 0.3)


def frame(index: int) -> Image.Image:
    s = SCALE
    image = Image.new("RGBA", (WIDTH * s, HEIGHT * s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image, "RGBA")
    phase = index / FRAMES
    time = phase * 2 * math.pi

    # Progress track that fills once per loop, with a light sweeping along it.
    left, right, top, height = 14 * s, (WIDTH - 14) * s, 92 * s, 8 * s
    draw.rounded_rectangle((left, top, right, top + height), radius=height // 2, fill=TRACK + (255,))
    filled = left + (right - left) * (0.12 + 0.88 * phase)
    steps = 60
    for step in range(steps):
        x0 = left + (filled - left) * step / steps
        x1 = left + (filled - left) * (step + 1) / steps + 1
        draw.rectangle((x0, top, x1, top + height), fill=gradient(step / steps) + (255,))
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((left, top, right, top + height), radius=height // 2, fill=255)
    track = image.copy()
    image = Image.new("RGBA", image.size, (0, 0, 0, 0))
    image.paste(track, (0, 0), mask)
    draw = ImageDraw.Draw(image, "RGBA")

    # The quad flies at the tip of the bar and hovers.
    cx = min(max(filled, 50 * s), (WIDTH - 50) * s)
    cy = 50 * s + math.sin(time * 2) * 3 * s
    shadow = 1 - (math.sin(time * 2) + 1) / 8
    draw.ellipse((cx - 30 * s * shadow, 86 * s - 3 * s, cx + 30 * s * shadow, 86 * s + 3 * s), fill=SHADOW)

    def scaled(points):
        return [(cx + x * s, cy + y * s) for x, y in points]

    arms = [(-30, -22), (30, -22), (-30, 22), (30, 22)]
    for dx, dy in arms:
        draw.line(scaled([(0, 0), (dx, dy)]), fill=OUTLINE + (255,), width=round(8.5 * s))
    for dx, dy in arms:
        draw.line(scaled([(0, 0), (dx, dy)]), fill=FRAME + (255,), width=round(5.5 * s))
    for number, (dx, dy) in enumerate(arms):
        mx, my = cx + dx * s, cy + dy * s
        # Spinning prop disc and two blades.
        draw.ellipse((mx - 16 * s, my - 9.5 * s, mx + 16 * s, my + 9.5 * s), fill=(138, 147, 155, 60), outline=(150, 158, 165, 255), width=round(1.2 * s))
        angle = time * 9 + number * 1.3
        for blade in range(2):
            a = angle + blade * math.pi
            tip = (mx + math.cos(a) * 15 * s, my + math.sin(a) * 9 * s)
            draw.line([(mx, my), tip], fill=PROP + (255,), width=round(4.5 * s))
            draw.ellipse((tip[0] - 2.2 * s, tip[1] - 2.2 * s, tip[0] + 2.2 * s, tip[1] + 2.2 * s), fill=PROP_TIP + (255,))
        draw.ellipse((mx - 6.5 * s, my - 6.5 * s, mx + 6.5 * s, my + 6.5 * s), fill=MOTOR + (255,), outline=OUTLINE + (255,), width=round(1.4 * s))
        draw.ellipse((mx - 2.5 * s, my - 2.5 * s, mx + 2.5 * s, my + 2.5 * s), fill=FRAME + (255,))

    # Body, battery with strap, camera and lens at the front, antenna and tail LED at the back.
    draw.rounded_rectangle((cx - 15 * s, cy - 8 * s, cx + 15 * s, cy + 8 * s), radius=4 * s, fill=FRAME + (255,), outline=OUTLINE + (255,), width=round(1.6 * s))
    draw.rounded_rectangle((cx - 11 * s, cy - 5.5 * s, cx + 6 * s, cy + 5.5 * s), radius=2.5 * s, fill=BATTERY + (255,))
    for x in (-5.5, 0.5):
        draw.rectangle((cx + x * s, cy - 6.5 * s, cx + (x + 2.2) * s, cy + 6.5 * s), fill=FRAME + (255,))
    draw.rounded_rectangle((cx + 13 * s, cy - 6 * s, cx + 22 * s, cy + 6 * s), radius=2.5 * s, fill=CAMERA + (255,), outline=OUTLINE + (255,), width=round(1.2 * s))
    draw.ellipse((cx + 17.5 * s, cy - 3.4 * s, cx + 24.3 * s, cy + 3.4 * s), fill=LENS + (255,), outline=OUTLINE + (255,), width=s)
    draw.line([(cx - 15 * s, cy - 2 * s), (cx - 27 * s, cy - 9 * s)], fill=OUTLINE + (255,), width=round(4 * s))
    draw.line([(cx - 15 * s, cy - 2 * s), (cx - 27 * s, cy - 9 * s)], fill=FRAME + (255,), width=round(2 * s))
    draw.ellipse((cx - 29.5 * s, cy - 11.5 * s, cx - 24.5 * s, cy - 6.5 * s), fill=FRAME + (255,), outline=OUTLINE + (255,), width=s)
    led = TAIL if (index // 6) % 2 == 0 else (120, 40, 44)
    draw.ellipse((cx - 19 * s, cy + 1.5 * s, cx - 13.5 * s, cy + 7 * s), fill=led + (255,), outline=OUTLINE + (255,), width=s)
    return image.resize((WIDTH, HEIGHT), Image.LANCZOS)


def main() -> None:
    target = Path(sys.argv[1])
    frames = [frame(index) for index in range(FRAMES)]
    key = (255, 0, 255)
    # One shared palette for every frame, with a key colour for transparency.
    flattened = []
    for image in frames:
        alpha = image.getchannel("A").point(lambda value: 255 if value >= 110 else 0)
        base = Image.new("RGB", image.size, key)
        base.paste(image.convert("RGB"), (0, 0), alpha)
        flattened.append(base)
    sheet = Image.new("RGB", (WIDTH, HEIGHT * FRAMES))
    for index, image in enumerate(flattened):
        sheet.paste(image, (0, index * HEIGHT))
    palette = sheet.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    entries = palette.getpalette()
    count = len(entries) // 3
    distances = [sum((entries[i * 3 + c] - key[c]) ** 2 for c in range(3)) for i in range(count)]
    transparent = distances.index(min(distances))
    indexed = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image in flattened]
    indexed[0].save(target, save_all=True, append_images=indexed[1:], duration=FRAME_MS, loop=0,
                    transparency=transparent, disposal=2, optimize=False)
    print(f"{target} {WIDTH}x{HEIGHT}, {FRAMES} frames, {target.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
