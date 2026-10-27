"""The new overlay: layout file, styles and templates, size, placement, animation, and the re-draw stage."""

from __future__ import annotations

import json
import unittest

import numpy as np

import osd_synthetic as syn

try:
    from flugbild.osd_render import (ANCHORS, STYLES, TEMPLATES, Layout, Overlay, Placement, default_layout,
                                     layout_schema, render_preview)
    from flugbild.typeface import inter
except ImportError as error:  # the overlay renderer is being rewritten against these tests
    raise unittest.SkipTest(f"OSD renderer not available yet: {error}")


VALUES = {"battery": 14.8, "cells": 4.0, "current": 18.4, "mah": 612.0, "flight_time": 83.0, "rssi": 76.0,
          "lq": 99.0, "speed": 54.0, "altitude": 23.5, "distance": 180.0, "home_direction": 30.0, "sats": 11.0,
          "warning": "LOW BATTERY", "raw:3": "TELETEXT"}


def picture(width: int, height: int) -> np.ndarray:
    rng = np.random.default_rng(1)
    base = rng.integers(40, 200, size=(9, 12, 3), dtype=np.uint8)
    import cv2
    return cv2.resize(base, (width, height), interpolation=cv2.INTER_CUBIC)


def drawn_box(before: np.ndarray, after: np.ndarray) -> tuple[int, int, int, int]:
    changed = np.abs(after.astype(int) - before.astype(int)).max(axis=2) > 2
    rows, columns = np.flatnonzero(changed.any(axis=1)), np.flatnonzero(changed.any(axis=0))
    return int(columns[0]), int(rows[0]), int(columns[-1]), int(rows[-1])


class LayoutTest(unittest.TestCase):
    def test_json_round_trip(self):
        layout = default_layout(list(VALUES), "corners", "racing", boxes={"raw:3": (0.05, 0.9, 0.2, 0.05)})
        layout.elements["battery"] = Placement(0.4, 0.2, "center", 1.3, True)
        layout.elements["raw:3"].text = "MY QUAD"
        data = json.loads(json.dumps(layout.to_json()))
        again = Layout.from_json(data)
        self.assertEqual(again.to_json(), layout.to_json())
        self.assertEqual(data["schema"], "flugbild.osd-layout/1")

    def test_schema_describes_the_file(self):
        schema = layout_schema()
        self.assertEqual(set(schema["properties"]["style"]["enum"]), set(STYLES))
        self.assertEqual(set(schema["properties"]["template"]["enum"]), set(TEMPLATES))
        placement = schema["properties"]["elements"]["additionalProperties"]
        self.assertEqual(set(placement["properties"]["anchor"]["enum"]), set(ANCHORS))

    def test_invalid_layouts_are_rejected(self):
        for change in ({"style": "neon"}, {"template": "spiral"}, {"scale": 5}):
            data = default_layout(["battery"]).to_json()
            data.update(change)
            with self.assertRaises(ValueError):
                Layout.from_json(data)
        data = default_layout(["battery"]).to_json()
        data["elements"]["battery"]["anchor"] = "middle"
        with self.assertRaises(ValueError):
            Layout.from_json(data)

    def test_templates_place_every_field(self):
        for template in TEMPLATES:
            layout = default_layout(list(VALUES), template, "modern", boxes={"raw:3": (0.05, 0.9, 0.2, 0.05)})
            self.assertIn("battery", layout.elements)
            self.assertIn("raw:3", layout.elements)
            self.assertNotIn("cells", layout.elements)  # drawn inside the battery element
            for place in layout.elements.values():
                self.assertTrue(0 <= place.x <= 1 and 0 <= place.y <= 1)
        minimal = default_layout(list(VALUES), "minimal")
        self.assertFalse(minimal.elements["speed"].visible)
        self.assertTrue(minimal.elements["battery"].visible)


class OverlayTest(unittest.TestCase):
    def test_every_style_and_template_draws_inside_the_safe_area(self):
        frame = picture(960, 720)
        for style in STYLES:
            for template in TEMPLATES:
                layout = default_layout(list(VALUES), template, style, boxes={"raw:3": (0.05, 0.9, 0.2, 0.05)})
                result = render_preview(frame, VALUES, layout)
                self.assertEqual(result.shape, frame.shape)
                self.assertEqual(result.dtype, np.uint8)
                left, top, right, bottom = drawn_box(frame, result)
                margin = int(layout.safe_area * 720) - 2
                if template == "bottom-bar" and style != "classic":
                    margin = 0  # the bar's backdrop runs to the picture edge
                self.assertGreaterEqual(left, margin, (style, template))
                self.assertGreaterEqual(top, margin, (style, template))
                self.assertLessEqual(right, 959 - margin, (style, template))
                self.assertLessEqual(bottom, 719 - margin, (style, template))

    def test_deterministic(self):
        frame = picture(960, 720)
        layout = default_layout(list(VALUES), "corners", "modern")
        first = Overlay(layout, (960, 720), 30.0)
        second = Overlay(layout, (960, 720), 30.0)
        for index in range(5):
            values = dict(VALUES, battery=14.8 - index * 0.1, flight_time=83.0 + index / 30)
            np.testing.assert_array_equal(first.draw(frame, values), second.draw(frame, values))

    def test_scales_with_the_picture(self):
        sizes = []
        for width, height in ((960, 720), (1440, 1080), (720, 540)):
            frame = picture(width, height)
            layout = default_layout(["flight_time"], "corners", "modern")
            left, top, right, bottom = drawn_box(frame, render_preview(frame, {"flight_time": 83.0}, layout))
            sizes.append(((right - left) / height, (bottom - top) / height))
        for size in sizes[1:]:
            self.assertAlmostEqual(size[0], sizes[0][0], delta=0.02)
            self.assertAlmostEqual(size[1], sizes[0][1], delta=0.02)

    def test_warning_fades_in_and_survives_blinking(self):
        frame = picture(960, 720)
        layout = default_layout(["warning"], "corners", "modern")
        overlay = Overlay(layout, (960, 720), 30.0)
        changes = []
        for index in range(30):
            # The original OSD blinks: shown two frames, hidden two.
            values = {"warning": "LOW BATTERY" if index % 4 < 2 else None}
            changes.append(np.abs(overlay.draw(frame, values).astype(int) - frame).sum())
        self.assertLess(changes[0], changes[8])  # fading in
        self.assertGreater(min(changes[10:]), 0.9 * max(changes[10:]))  # steady, no blinking
        for _ in range(60):  # held 1.2 s, then faded out
            last = overlay.draw(frame, {})
        np.testing.assert_array_equal(last, frame)

    def test_frame_size_is_checked(self):
        overlay = Overlay(default_layout(["battery"]), (960, 720))
        with self.assertRaises(ValueError):
            overlay.draw(picture(640, 480), {"battery": 4.0})


class TypefaceTest(unittest.TestCase):
    def test_weights_and_tabular_digits(self):
        light, black = inter(300), inter(900)
        thin, _ = light.render("Battery 3.91V", 40)
        bold, _ = black.render("Battery 3.91V", 40)
        self.assertGreater(bold.mean(), thin.mean() * 1.4)
        self.assertEqual(light.measure("1111", 30, True), light.measure("8888", 30, True))
        mask, baseline = inter(500).render("Hg", 48)
        self.assertGreater(mask[baseline + 4:].sum(), 0)  # the descender of g


class RedrawStageTest(unittest.TestCase):
    def test_osd_is_painted_out_and_overlay_drawn(self):
        from flugbild.osd_read import read_frames
        from flugbild.osd_redraw import RedrawPlan, overlay_stage, remove_stage

        frames, truth = syn.clip(syn.HD, 60, 5.0, analog_blur=False)
        reading = read_frames(frames, 5.0)
        plan = RedrawPlan(reading, default_layout(reading.fields, "corners", "modern"), (0, 0, 1280, 720))
        clean_scene = [syn.background(index, (1280, 720)) for index in range(60)]
        # Remove on a part of the clip; frame times count from the read start.
        cleaned = list(remove_stage(frames[20:32], plan, 20, 5.0, radius=3))
        self.assertEqual(len(cleaned), 12)
        # Inside the voltage cells the result is much nearer the scene than the OSD was.
        box = reading.elements[reading.fields["battery"].element].box
        x, y, w, h = box
        for index, frame in enumerate(cleaned):
            before = np.abs(frames[20 + index][y:y + h, x:x + w].astype(int) - clean_scene[20 + index][y:y + h, x:x + w])
            after = np.abs(frame[y:y + h, x:x + w].astype(int) - clean_scene[20 + index][y:y + h, x:x + w])
            self.assertLess(after.mean(), 0.6 * before.mean())
        drawn = list(overlay_stage(cleaned, plan, 20, 5.0))
        self.assertEqual(drawn[0].shape, cleaned[0].shape)
        self.assertGreater(np.abs(drawn[-1].astype(int) - cleaned[-1]).mean(), 0.5)
        values = plan.values_at(22 / 5.0)
        self.assertAlmostEqual(values["battery"], truth.voltage[22], delta=0.11)


if __name__ == "__main__":
    unittest.main()
