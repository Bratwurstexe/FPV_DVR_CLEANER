from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

import cv2
import numpy as np

from flugbild.learning import Signalsignatur, calibrate
from flugbild.media import inspect_video, still_frame, tools_available
from flugbild.processor import process_one
from flugbild.restoration import process_temporal
from flugbild.recipe import Arbeitsrezept


@unittest.skipUnless(tools_available(), "FFmpeg tools are required")
class WorkflowTest(unittest.TestCase):
    def test_sample_video_survives_learning_cut_and_export(self):
        with tempfile.TemporaryDirectory(prefix="flugbild-test-") as scratch:
            folder = Path(scratch)
            source = folder / "input.mp4"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=160x96:rate=12",
                "-f", "lavfi", "-i", "sine=frequency=440",
                "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(source),
            ], check=True)
            input_info = inspect_video(source)
            self.assertTrue(input_info.has_audio)

            learned = calibrate(folder, "digital", 1, lambda count, seconds: None, threading.Event())
            self.assertGreater(learned.stichproben, 0)
            model_file = folder / "model.json"
            learned.save(model_file)
            self.assertEqual(Signalsignatur.load(model_file), learned)

            recipe = Arbeitsrezept(signalart="digital", blockpflege=30, farbberuhigung=0,
                            schnittbeginn=0.3, schnittende=1.4, untertiteltext="Flight: 50% O'Brien",
                            vergroesserung_prozent=150, bewegungsspur=15)
            output = folder / "output.mp4"
            updates = []
            result = process_one(source, output, recipe, input_info, updates.append,
                                 threading.Event(), learned)
            self.assertTrue(result.success, result.message)
            self.assertTrue(output.exists())
            output_info = inspect_video(output)
            self.assertTrue(output_info.has_audio)
            self.assertGreater(output_info.width, input_info.width)
            self.assertLess(output_info.duration, input_info.duration)
            self.assertAlmostEqual(updates[-1], 1.0)

            temporal_output = folder / "temporal.mp4"
            temporal_recipe = Arbeitsrezept(signalart="digital", blockpflege=45,
                                     schnittbeginn=0.2, schnittende=1.2)
            temporal = process_temporal(source, temporal_output, temporal_recipe,
                                        input_info, learned, lambda fraction: None,
                                        threading.Event())
            self.assertTrue(temporal.success, temporal.message)
            self.assertTrue(inspect_video(temporal_output).has_audio)

            # With restoration disabled, both export paths must show the same
            # source moment after a nonzero cut, including on the first frames.
            neutral = Arbeitsrezept(signalart="digital", blockpflege=0,
                                    schnittbeginn=0.5, schnittende=1.5)
            plain_file = folder / "plain_cut.mp4"
            timed_file = folder / "timed_cut.mp4"
            self.assertTrue(process_one(source, plain_file, neutral, input_info,
                                        lambda value: None, threading.Event()).success)
            self.assertTrue(process_temporal(source, timed_file, neutral, input_info,
                                             learned, lambda value: None,
                                             threading.Event()).success)
            still_frame(plain_file, 0.25, folder / "plain.png")
            still_frame(timed_file, 0.25, folder / "timed.png")
            plain = cv2.imread(str(folder / "plain.png"))
            timed = cv2.imread(str(folder / "timed.png"))
            self.assertLess(float(np.abs(plain.astype(np.int16) - timed).mean()), 15)


if __name__ == "__main__":
    unittest.main()
