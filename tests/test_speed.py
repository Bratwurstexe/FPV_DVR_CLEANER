import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

import cv2
import numpy as np

from flugbild.media import inspect_video, tools_available
from flugbild.pipeline import ordered_map, window_map
from flugbild.recipe import Arbeitsrezept
from flugbild.restoration import picture_box, process_temporal


class PipelineTest(unittest.TestCase):
    def test_parallel_results_keep_their_order(self):
        self.assertEqual(list(ordered_map(lambda value: value * value, range(50))),
                         [value * value for value in range(50)])

    def test_windows_match_a_sequential_run(self):
        frames = list(range(10))
        parallel = list(window_map(frames, 2, lambda value: value * 10,
                                   lambda window, center: (tuple(window), window[center])))
        self.assertEqual(len(parallel), 10)
        self.assertEqual(parallel[0], ((0, 10, 20), 0))
        self.assertEqual(parallel[5], ((30, 40, 50, 60, 70), 50))
        self.assertEqual(parallel[9], ((70, 80, 90), 90))


class RecipeTest(unittest.TestCase):
    def test_recipe_saved_before_the_effort_setting_loads(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "recipe.json"
            data = json.loads(json.dumps(Arbeitsrezept().__dict__))
            del data["aufwand"]
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(Arbeitsrezept.load(path).aufwand, "balanced")

    def test_unknown_effort_is_rejected(self):
        with self.assertRaises(ValueError):
            Arbeitsrezept(aufwand="turbo").validate()


@unittest.skipUnless(tools_available(), "FFmpeg tools are required")
class SideBarTest(unittest.TestCase):
    def test_bars_are_skipped_and_come_back_black(self):
        with tempfile.TemporaryDirectory(prefix="flugbild-bars-") as scratch:
            folder = Path(scratch)
            source = folder / "goggles.mp4"
            # A 4:3 picture inside a wider frame, as goggles record it.
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=20",
                            "-t", "2", "-vf", "pad=240:120:40:0:black",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True)
            info = inspect_video(source)
            self.assertEqual(picture_box(source, 0, info.duration, info.width, info.height),
                             (40, 0, 160, 120))
            target = folder / "clean.mp4"
            recipe = Arbeitsrezept(signalart="analog", farbberuhigung=40, rauschminderung=30,
                                   vergroesserung_prozent=150, aufwand="fast")
            result = process_temporal(source, target, recipe, info, None, lambda ratio: None,
                                      threading.Event())
            self.assertTrue(result.success, result.message)
            capture = cv2.VideoCapture(str(target))
            okay, frame = capture.read()
            capture.release()
            self.assertTrue(okay)
            self.assertEqual(frame.shape[:2], (180, 360))
            self.assertLess(frame[:, :50].max(), 30)
            self.assertLess(frame[:, -50:].max(), 30)
            self.assertGreater(frame[:, 70:290].mean(), 40)


if __name__ == "__main__":
    unittest.main()
