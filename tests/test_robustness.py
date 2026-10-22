"""Failure cases that must not destroy an earlier render or load bad settings."""

import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from flugbild.learning import Signalsignatur
from flugbild.media import VideoKenndaten, inspect_video, tools_available
from flugbild.processor import process_one
from flugbild.recipe import Arbeitsrezept
from flugbild.restoration import process_temporal


class ModelValidationTest(unittest.TestCase):
    def test_non_numeric_signal_measure_is_rejected(self):
        with tempfile.TemporaryDirectory() as scratch:
            model_path = Path(scratch) / "model.json"
            data = dict(formatstand=1, signalart="analog", stichproben=2,
                        farbrauschen="high", blocknaht=1.0, quelldateien=1)
            model_path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                Signalsignatur.load(model_path)


@unittest.skipUnless(tools_available(), "FFmpeg tools are required")
class ExportRecoveryTest(unittest.TestCase):
    def test_failed_export_keeps_existing_result(self):
        with tempfile.TemporaryDirectory() as scratch:
            folder = Path(scratch)
            target = folder / "result.mp4"
            target.write_bytes(b"existing result")
            info = VideoKenndaten(1.0, 64, 64, False)
            result = process_one(folder / "missing.mp4", target, Arbeitsrezept(),
                                 info, lambda value: None, threading.Event())
            self.assertFalse(result.success)
            self.assertEqual(target.read_bytes(), b"existing result")
            self.assertFalse(list(folder.glob(".result-*.mp4")))

    def test_canceled_temporal_export_keeps_existing_result(self):
        with tempfile.TemporaryDirectory() as scratch:
            folder = Path(scratch)
            source = folder / "source.mp4"
            target = folder / "result.mp4"
            target.write_bytes(b"existing result")
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=8",
                            "-t", "1", "-c:v", "libx264", str(source)], check=True)
            model = Signalsignatur(1, "digital", 2, 3.0, 2.0, 1)
            cancel = threading.Event()
            cancel.set()
            result = process_temporal(source, target, Arbeitsrezept(signalart="digital"),
                                      inspect_video(source), model, lambda value: None, cancel)
            self.assertFalse(result.success)
            self.assertEqual(target.read_bytes(), b"existing result")
            self.assertFalse(list(folder.glob(".result-*.mp4")))


if __name__ == "__main__":
    unittest.main()
