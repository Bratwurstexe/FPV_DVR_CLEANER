from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

import cv2

from flugbild.media import inspect_video, tools_available
from flugbild.neural import NeuralProfile, train_network
from flugbild.recipe import Arbeitsrezept
from flugbild.restoration import process_temporal


@unittest.skipUnless(tools_available(), "FFmpeg tools are required")
class NeuralWorkflowTest(unittest.TestCase):
    def test_local_training_profile_and_export(self):
        with tempfile.TemporaryDirectory(prefix="fpv-neural-test-") as scratch:
            folder = Path(scratch)
            source = folder / "sample.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=8",
                            "-t", "1", "-c:v", "libx264", str(source)], check=True)
            model = train_network(folder, "digital", 2, lambda samples, error: None,
                                  threading.Event())
            self.assertGreaterEqual(model.samples, 100)
            self.assertLess(model.validation_mae, 0.4)
            profile = folder / "local-model.json"
            model.save(profile)
            loaded = NeuralProfile.load(profile)
            self.assertEqual(loaded.architecture, "motion5")
            continued = train_network(folder, "digital", 1, lambda samples, error: None,
                                      threading.Event(), loaded)
            self.assertEqual(continued.training_runs, 2)
            self.assertGreater(continued.samples, loaded.samples)
            self.assertEqual(continued.architecture, "motion5")
            with self.assertRaisesRegex(ValueError, "signal type"):
                train_network(folder, "analog", 1, lambda samples, error: None,
                              threading.Event(), loaded)
            frame = cv2.VideoCapture(str(source))
            try:
                okay, picture = frame.read()
                self.assertTrue(okay)
                self.assertEqual(loaded.improve(picture).shape, picture.shape)
            finally:
                frame.release()
            target = folder / "result.mp4"
            report = process_temporal(source, target, Arbeitsrezept(signalart="digital"),
                                      inspect_video(source), loaded, lambda ratio: None,
                                      threading.Event())
            self.assertTrue(report.success, report.message)
            self.assertGreater(inspect_video(target).duration, 0)
