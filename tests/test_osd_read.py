"""Reading a burned-in OSD back from the pixels, on synthetic footage with a known ground truth.

The OSD is drawn with the pixel font of ``osd_synthetic`` (made for these
tests, unlike any reference design of the reader) onto moving terrain, on an
analog 30 x 16 grid in a pillarboxed 4:3 picture and on an HD 50 x 18 grid.
The timer ticks, the voltage falls, a warning blinks in late.
"""

from __future__ import annotations

import json
import unittest

import cv2
import numpy as np

import osd_synthetic as syn
from flugbild.osd_read import ink_map, read_frames


RATE = 5.0


def share(values, truth, tolerance):
    hits = sum(1 for value, expected in zip(values, truth)
               if value is not None and abs(value - expected) <= tolerance)
    return hits / len(truth)


class AnalogGridTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames, cls.truth = syn.clip(syn.ANALOG, 100, RATE)
        cls.reading = read_frames(cls.frames, RATE, picture=(160, 0, 960, 720))

    def test_grid_pitch_and_phase(self):
        grid = self.reading.grid
        self.assertIsNotNone(grid)
        self.assertAlmostEqual(grid.pitch_x, 32.0, delta=0.4)
        self.assertAlmostEqual(grid.pitch_y, 45.0, delta=0.6)
        self.assertEqual(grid.kind, "analog")
        # Cell centres fall on the drawn cells (cell 0 of the picture starts at x = 160).
        self.assertLess(abs((grid.origin_x - 16 + 16) % 32 - 16), 4.5)

    def test_timer_digits_are_learned_from_the_ticking_seconds(self):
        learned = {entry.label for entry in self.reading.atlas if entry.source == "timer"}
        self.assertGreaterEqual(len(learned & set("0123456789")), 9)
        timer = self.reading.fields["flight_time"]
        self.assertEqual(timer.kind, "time")
        self.assertGreaterEqual(share(timer.values, self.truth.time, 0.5), 0.75)

    def test_falling_voltage(self):
        battery = self.reading.fields.get("battery")
        self.assertIsNotNone(battery)
        self.assertEqual(battery.unit, "V")
        self.assertGreaterEqual(share(battery.values, self.truth.voltage, 0.051), 0.65)
        shown = [value for value in battery.values if value is not None]
        self.assertGreater(np.median(shown[:20]), np.median(shown[-20:]))

    def test_warning_appears_late(self):
        warning = self.reading.fields["warning"]
        self.assertTrue(all(value is None for value in warning.values[:30]))
        late = [value for value in warning.values[60:] if value]
        self.assertGreater(len(late), 20)
        self.assertGreaterEqual(late.count("LOW BATTERY") / len(late), 0.8)

    def test_field_boxes_are_fractions_of_the_picture(self):
        battery = self.reading.fields["battery"]
        x, y, w, h = battery.box
        # Drawn from picture column 2, row 14 of 16.
        self.assertAlmostEqual(x, 2 / 30, delta=0.03)
        self.assertAlmostEqual(y, 14 / 16, delta=0.03)
        json.dumps(self.reading.to_json())
        json.dumps(self.reading.summary())

    def test_manual_correction_hook(self):
        reading = self.reading
        element = reading.fields["raw:0"].element if "raw:0" in reading.fields else None
        self.assertIsNotNone(element, "the craft name stays a raw text field")
        codes = [int(code) for code in reading.codes[10, reading.elements[element].slots] if code >= 0]
        corrected = reading.relabel({codes[0]: "Q"})
        self.assertEqual(corrected.text_of(element, 10)[0], "Q")
        self.assertEqual(corrected.overrides[codes[0]], "Q")
        self.assertNotIn(codes[0], reading.overrides)


class HdGridTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames, cls.truth = syn.clip(syn.HD, 100, RATE)
        cls.reading = read_frames(cls.frames, RATE)

    def test_grid(self):
        grid = self.reading.grid
        self.assertAlmostEqual(grid.pitch_x, 24.0, delta=0.3)
        self.assertAlmostEqual(grid.pitch_y, 36.0, delta=0.5)
        self.assertEqual(grid.kind, "hd")

    def test_values(self):
        fields = self.reading.fields
        self.assertGreaterEqual(share(fields["flight_time"].values, self.truth.time, 0.5), 0.75)
        self.assertGreaterEqual(share(fields["battery"].values, self.truth.voltage, 0.051), 0.85)
        self.assertGreaterEqual(share(fields["mah"].values, self.truth.mah, 0.5), 0.65)
        self.assertEqual(fields["mah"].unit, "mAh")


class RobustnessTest(unittest.TestCase):
    def test_noise_and_breakups(self):
        breakups = (20, 21, 55, 70, 71)
        frames, truth = syn.clip(syn.ANALOG, 100, RATE, breakups=breakups, noise=12)
        reading = read_frames(frames, RATE)
        self.assertAlmostEqual(reading.grid.pitch_x, 32.0, delta=0.4)
        timer = reading.fields["flight_time"]
        # Breakup frames hold the last value instead of reading confetti.
        self.assertGreaterEqual(share(timer.values, truth.time, 0.5), 0.75)
        for index in breakups:
            self.assertIsNotNone(timer.values[index])
        warning = reading.fields["warning"]
        late = [value for value in warning.values[60:] if value]
        self.assertGreaterEqual(late.count("LOW BATTERY") / max(1, len(late)), 0.7)


class InkTest(unittest.TestCase):
    def test_outlined_text_on_bright_sky(self):
        frame = np.full((720, 1280, 3), 205, np.uint8)
        cv2.putText(frame, "88", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 2, (15, 15, 15), 9)
        cv2.putText(frame, "88", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 2, (245, 245, 245), 3)
        ink = ink_map(frame)
        text = np.zeros((720, 1280), np.uint8)
        cv2.putText(text, "88", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 2, 1, 3)
        self.assertGreater(ink[text > 0].mean(), 0.6)
        # The sky around and between the characters is no ink.
        self.assertEqual(int(ink[:15].sum()), 0)
        self.assertLess(ink[text == 0].mean(), 0.03)

    def test_too_few_frames(self):
        with self.assertRaises(ValueError):
            read_frames([], RATE)


if __name__ == "__main__":
    unittest.main()
