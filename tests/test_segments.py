"""Multi-segment export, joining and the waveform outline."""

from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from flugbild.media import inspect_video, tools_available
from flugbild.processor import process_one
from flugbild.recipe import Arbeitsrezept
from flugbild.segments import audio_outline, export_segments, normalize_segments, segment_recipe


class SegmentMathTests(unittest.TestCase):
    def test_whole_clip_when_nothing_given(self):
        self.assertEqual(normalize_segments(None, 12.0), [(0.0, 12.0)])
        self.assertEqual(normalize_segments([], 12.0), [(0.0, 12.0)])

    def test_segments_are_sorted_clipped_and_merged(self):
        self.assertEqual(normalize_segments([[8, 20], [1, 3], [2.5, 4]], 10.0), [(1.0, 4.0), (8.0, 10.0)])

    def test_tiny_segments_are_dropped(self):
        self.assertEqual(normalize_segments([[1, 1.05], [2, 3]], 10.0), [(2.0, 3.0)])
        with self.assertRaises(ValueError):
            normalize_segments([[1, 1.01]], 10.0)

    def test_last_segment_to_the_end_has_no_out_point(self):
        recipe = segment_recipe(Arbeitsrezept(signalart="digital"), (2.0, 10.0), 10.0)
        self.assertEqual((recipe.schnittbeginn, recipe.schnittende), (2.0, 0.0))
        recipe = segment_recipe(Arbeitsrezept(signalart="digital"), (2.0, 5.0), 10.0)
        self.assertEqual((recipe.schnittbeginn, recipe.schnittende), (2.0, 5.0))


@unittest.skipUnless(tools_available(), "FFmpeg is required")
class SegmentExportTests(unittest.TestCase):
    def test_segments_are_rendered_and_joined(self):
        with tempfile.TemporaryDirectory() as workdir:
            folder = Path(workdir)
            source = folder / "flight.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "testsrc2=size=160x96:rate=12",
                            "-f", "lavfi", "-i", "sine=frequency=440",
                            "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", str(source)], check=True)
            info = inspect_video(source)
            recipe = Arbeitsrezept(signalart="digital", blockpflege=0, kantendetail=0)
            updates = []

            def render(part_source, part_target, part_recipe, report):
                return process_one(part_source, part_target, part_recipe, info, report, threading.Event())

            target = folder / "joined.mp4"
            result = export_segments(source, target, recipe, info, [(0.5, 1.5), (2.5, 4.0)], render,
                                     updates.append, threading.Event())
            self.assertTrue(result.success, result.message)
            joined = inspect_video(target)
            self.assertAlmostEqual(joined.duration, 2.5, delta=0.3)
            self.assertTrue(joined.has_audio)
            self.assertAlmostEqual(updates[-1], 1.0)
            self.assertEqual(sorted(updates), updates)
            self.assertFalse(list(folder.glob(".joined-part*")))

            levels = audio_outline(source, info, buckets=40)
            self.assertEqual(len(levels), 40)
            self.assertTrue(all(0 <= level <= 1 for level in levels))
            self.assertAlmostEqual(max(levels), 1.0)


if __name__ == "__main__":
    unittest.main()
