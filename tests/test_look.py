"""Finishing look: the five steps, recipe fields and a real export."""

from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import cv2
import numpy as np

from flugbild.bridge import run
from flugbild.look import PRESETS, Look, look_active, look_frame, look_stream
from flugbild.media import inspect_video, tools_available
from flugbild.recipe import LOOK_WERTE, Arbeitsrezept


def scene(height: int = 48, width: int = 64) -> np.ndarray:
    """A grey ramp with a bright square and a dark bar, BGR uint8."""
    ramp = np.tile(np.linspace(40, 200, width, dtype=np.float32), (height, 1))
    frame = np.dstack([ramp, ramp * 0.9 + 10, ramp * 0.8 + 20]).astype(np.uint8)
    frame[18:30, 26:38] = 250
    frame[:, 4:6] = 10
    return frame


def luma(frame: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)


class LookStepTests(unittest.TestCase):
    def test_no_look_is_exactly_the_input(self):
        frame = scene()
        self.assertIs(look_frame(frame, Look(), 7), frame)
        self.assertIs(look_frame(frame, PRESETS["off"], 7), frame)
        self.assertEqual([*look_stream([frame, frame], Look())][1].tobytes(), frame.tobytes())

    def test_grade_raises_contrast(self):
        frame = scene()
        graded = look_frame(frame, Look(grade=1.0))
        self.assertGreater(luma(graded).std(), luma(frame).std() * 1.05)
        # Shadows lean to blue, highlights to red.
        dark, bright = graded[24, 8].astype(int), graded[24, 60].astype(int)
        source_dark, source_bright = frame[24, 8].astype(int), frame[24, 60].astype(int)
        self.assertGreater(dark[0] - dark[2], source_dark[0] - source_dark[2])
        self.assertGreater(bright[2] - bright[0], source_bright[2] - source_bright[0])

    def test_grade_follows_the_reference_formula(self):
        frame = np.random.default_rng(1).integers(0, 256, (37, 53, 3), dtype=np.uint8)
        amount = 0.8
        rgb = frame[:, :, ::-1].astype(np.float64) / 255
        lum = (rgb @ np.array([0.2126, 0.7152, 0.0722]))[:, :, None]
        expected = 0.5 + (rgb - 0.5) * (1 + 0.12 * amount)
        expected = lum + (expected - lum) * (1 + 0.12 * amount)
        expected += amount * (np.array([-0.012, 0.006, 0.02]) * (1 - lum) + np.array([0.03, 0.012, -0.022]) * lum)
        expected = np.clip(expected, 0, 1)[:, :, ::-1] * 255
        graded = look_frame(frame, Look(grade=amount)).astype(np.float64)
        self.assertLessEqual(np.abs(graded - expected).max(), 0.51)

    def test_vignette_darkens_corners_only(self):
        frame = np.full((60, 80, 3), 160, np.uint8)
        shaded = look_frame(frame, Look(vignette=1.0))
        self.assertLessEqual(abs(int(shaded[30, 40, 1]) - 160), 1)
        self.assertLess(int(shaded[0, 0, 1]), 160 * 0.5 + 2)
        self.assertLess(int(shaded[0, 0, 1]), int(shaded[0, 40, 1]))
        self.assertTrue(np.all(shaded <= frame))
        # Symmetric around the centre.
        np.testing.assert_array_equal(shaded, shaded[::-1, ::-1])

    def test_fringe_moves_red_and_blue_apart_at_the_edge_only(self):
        # A white vertical line near the right edge and one through the centre.
        frame = np.zeros((41, 401, 3), np.uint8)
        frame[:, 200] = 255
        frame[:, 380] = 255
        fringed = look_frame(frame, Look(fringe=1.0))
        row = fringed[20].astype(float)
        columns = np.arange(401)

        def centre_of(channel: int, low: int, high: int) -> float:
            weights = row[low:high, channel]
            return float((columns[low:high] * weights).sum() / weights.sum())

        # Red is scaled outwards, blue inwards; green stays.
        self.assertAlmostEqual(centre_of(1, 360, 400), 380, delta=0.01)
        self.assertGreater(centre_of(2, 360, 400), 380.5)
        self.assertLess(centre_of(0, 360, 400), 379.5)
        for channel in range(3):
            self.assertAlmostEqual(centre_of(channel, 190, 211), 200, delta=0.05)

    def test_glow_brightens_around_highlights(self):
        frame = np.full((480, 480, 3), 30, np.uint8)
        frame[200:280, 200:280] = 255
        glowing = look_frame(frame, Look(glow=1.0))
        self.assertGreater(int(glowing[240, 290, 1]), 30 + 5)
        self.assertLessEqual(abs(int(glowing[5, 5, 1]) - 30), 1)
        # Nothing bright, nothing glows.
        dim = np.full((96, 96, 3), 150, np.uint8)
        np.testing.assert_array_equal(look_frame(dim, Look(glow=1.0)), dim)

    def test_grain_repeats_per_frame_number(self):
        frame = np.full((48, 64, 3), 128, np.uint8)
        look = Look(grain=1.0)
        first = look_frame(frame, look, 12)
        np.testing.assert_array_equal(first, look_frame(frame, look, 12))
        self.assertFalse(np.array_equal(first, look_frame(frame, look, 13)))
        # Monochrome grain of a few levels.
        self.assertTrue(np.array_equal(first[:, :, 0], first[:, :, 2]))
        self.assertGreater(first.astype(float).std(), 2)
        streamed = [*look_stream([frame] * 3, look, first_index=11)]
        np.testing.assert_array_equal(streamed[1], first)

    def test_every_preset_keeps_size_and_type(self):
        frame = scene()
        for name, look in PRESETS.items():
            with self.subTest(preset=name):
                result = look_frame(frame, look, 3)
                self.assertEqual((result.shape, result.dtype), (frame.shape, np.uint8))


class LookRecipeTests(unittest.TestCase):
    def test_default_is_off(self):
        recipe = Arbeitsrezept()
        self.assertFalse(recipe.look)
        self.assertFalse(look_active(recipe))
        self.assertFalse(Look.of(replace(recipe, look_farbstimmung=1.0)).active)
        self.assertFalse(look_active(replace(recipe, look=True)))

    def test_values_map_to_the_steps(self):
        recipe = Arbeitsrezept(look=True, look_farbstimmung=0.6, look_leuchten=0.45, look_farbsaum=0.35,
                               look_vignette=0.5, look_koernung=0.25)
        self.assertEqual(Look.of(recipe), PRESETS["cinematic"])
        self.assertTrue(look_active(recipe))

    def test_values_are_checked(self):
        for name in LOOK_WERTE:
            Arbeitsrezept(**{name: 1}).validate()
            for bad in (-0.01, 1.01, float("nan"), True, "0.5"):
                with self.subTest(name=name, value=bad), self.assertRaises(ValueError):
                    Arbeitsrezept(**{name: bad}).validate()
        with self.assertRaises(ValueError):
            Arbeitsrezept(look=1).validate()

    def test_old_recipe_without_look_loads_as_off(self):
        with tempfile.TemporaryDirectory() as workdir:
            path = Path(workdir) / "old.json"
            data = asdict(Arbeitsrezept(signalart="digital"))
            for name in ("look", *LOOK_WERTE, "aufwand", "dateiformat"):
                del data[name]
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertFalse(look_active(Arbeitsrezept.load(path)))

    def test_look_survives_save_and_load(self):
        with tempfile.TemporaryDirectory() as workdir:
            path = Path(workdir) / "recipe.json"
            Arbeitsrezept(look=True, look_vignette=0.85, look_koernung=0.9).save(path)
            loaded = Arbeitsrezept.load(path)
            self.assertEqual(Look.of(loaded), Look(vignette=0.85, grain=0.9))
            data = json.loads(path.read_text(encoding="utf-8"))
            data["look_glanz"] = 0.5
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                Arbeitsrezept.load(path)


@unittest.skipUnless(tools_available(), "FFmpeg is required")
class LookExportTests(unittest.TestCase):
    def test_plain_export_with_look_goes_through_the_frame_path(self):
        with tempfile.TemporaryDirectory() as workdir:
            folder = Path(workdir)
            source = folder / "flight.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "testsrc2=size=160x96:rate=12",
                            "-f", "lavfi", "-i", "sine=frequency=440",
                            "-t", "1.5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", str(source)], check=True)
            info = inspect_video(source)
            # HDZero without any repair: without the look this is a plain FFmpeg copy.
            plain = dict(signalart="digital", blockpflege=0, kantendetail=0, kompressionswert=14, aufwand="fast")
            styled = dict(plain, look=True, look_farbstimmung=1.0, look_leuchten=0.7, look_farbsaum=0.5,
                          look_vignette=0.6, look_koernung=0.15)
            for name, recipe in (("plain", plain), ("styled", styled)):
                target = folder / f"{name}.mp4"
                run({"action": "export", "source": str(source), "target": str(target),
                     "mode": "manual", "recipe": recipe})
                made = inspect_video(target)
                self.assertEqual((made.width, made.height), (160, 96))
                self.assertTrue(made.has_audio)
                self.assertAlmostEqual(made.duration, 1.5, delta=0.3)

            def average_frame(path: Path) -> np.ndarray:
                # Every frame read in order and averaged: seeking lands on different frames of
                # the moving test pattern with different FFmpeg builds (it did on macOS).
                capture = cv2.VideoCapture(str(path))
                frames = []
                while True:
                    okay, frame = capture.read()
                    if not okay:
                        break
                    frames.append(frame.astype(np.float32))
                capture.release()
                self.assertGreater(len(frames), 10)
                return np.mean(frames, axis=0)

            before, after = average_frame(folder / "plain.mp4"), average_frame(folder / "styled.mp4")
            # The vignette darkens the corners, the middle stays close.
            corner = (slice(0, 12), slice(0, 16))
            self.assertLess(after[corner].mean(), before[corner].mean() - 8)
            self.assertLess(abs(after[40:56, 72:88].mean() - before[40:56, 72:88].mean()), 25)

    def test_preview_writes_the_frame_without_the_look_too(self):
        with tempfile.TemporaryDirectory() as workdir:
            folder = Path(workdir)
            source = folder / "flight.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "testsrc2=size=160x96:rate=12", "-t", "1.5",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True)
            paths = {name: str(folder / f"{name}.png") for name in ("before", "after", "plain")}
            recipe = dict(signalart="digital", blockpflege=0, kantendetail=0, look=True, look_vignette=1.0)
            run({"action": "preview", "source": str(source), "seconds": 0.7, "recipe": recipe, **paths})
            plain, after = cv2.imread(paths["plain"]), cv2.imread(paths["after"])
            self.assertEqual(plain.shape, after.shape)
            self.assertLess(after[:8, :8].mean(), plain[:8, :8].mean() - 10)
            # Without a look no extra frame is written.
            Path(paths["plain"]).unlink()
            run({"action": "preview", "source": str(source), "seconds": 0.7,
                 "recipe": dict(recipe, look=False), **paths})
            self.assertFalse(Path(paths["plain"]).exists())


if __name__ == "__main__":
    unittest.main()
