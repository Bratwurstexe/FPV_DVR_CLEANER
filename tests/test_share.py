"""Share videos: pairing, repair highlights, best stretch, 9:16 pan and the finished files.

The clips are flat colours with a small moving square, written frame by frame
and read back sequentially; checks compare colour classes, not fine levels, so
they hold across FFmpeg builds.
"""

import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

import numpy as np

from flugbild.bridge import run
from flugbild.media import tools_available
from flugbild.share import (Teilauftrag, best_window, extra_seconds, find_repairs, frame_plan, make_share,
                            output_rate, repair_peaks, smooth_pan, source_pieces, source_time)

FPS = 30
SIZE = (96, 72)
# One colour per source second (BGR), all clearly lit and told apart by their strongest channels.
COLOURS = [(60, 60, 220), (60, 200, 60), (220, 70, 60), (60, 210, 210), (210, 60, 210), (210, 210, 60)]


def scene(index: int) -> np.ndarray:
    """Source frame ``index``: the colour of its second and a small square moving along the top."""
    width, height = SIZE
    frame = np.empty((height, width, 3), np.uint8)
    frame[:] = COLOURS[(index // FPS) % len(COLOURS)]
    x = (index * 2) % (width - 10)
    frame[4:14, x:x + 10] = 245
    return frame


def broken(index: int) -> np.ndarray:
    """A frame lost to confetti: random coloured 8 px blocks."""
    width, height = SIZE
    blocks = np.random.default_rng(index).integers(0, 256, (height // 8, width // 8, 3), dtype=np.uint8)
    return np.repeat(np.repeat(blocks, 8, axis=0), 8, axis=1)


def write(path: Path, frames: list[np.ndarray], audio: bool = False) -> None:
    width, height = SIZE
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}", "-r", str(FPS), "-i", "pipe:0"]
    if audio:
        args.extend(["-f", "lavfi", "-i", f"sine=frequency=440:duration={len(frames) / FPS}"])
    args.extend(["-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-g", "1", "-pix_fmt", "yuv420p"])
    if audio:
        args.extend(["-c:a", "aac", "-shortest"])
    args.append(str(path))
    subprocess.run(args, input=b"".join(frame.tobytes() for frame in frames), check=True)


def probe(path: Path) -> dict:
    result = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
                             "stream=codec_type,width,height,r_frame_rate,nb_read_frames,color_space",
                             "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return {stream["codec_type"]: stream for stream in json.loads(result.stdout)["streams"]}


def frames_of(path: Path) -> np.ndarray:
    info = probe(path)["video"]
    width, height = int(info["width"]), int(info["height"])
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, height, width, 3)


def colour_class(patch: np.ndarray) -> tuple[int, ...]:
    """Which channels are lit (above the middle of the patch's range)."""
    mean = patch.reshape(-1, 3).mean(axis=0)
    return tuple(int(value > (mean.max() + mean.min()) / 2) for value in mean)


class PlanTests(unittest.TestCase):
    def test_kept_parts_map_cleaned_frames_to_the_source(self):
        segments = [(0.0, 2.0), (4.0, 6.0)]
        self.assertEqual(source_pieces(segments, 30, 45, 75), [(1.5, 0.5, 15), (4.0, 0.5, 15)])
        self.assertAlmostEqual(source_time(segments, 30, 59), 59 / 30)
        self.assertAlmostEqual(source_time(segments, 30, 60), 4.0)
        self.assertAlmostEqual(source_time(segments, 30, 90), 5.0)

    def test_single_frame_repairs_stand_out_and_steady_change_does_not(self):
        rng = np.random.default_rng(1)
        difference = 8 + rng.normal(0, 0.4, 600).astype(np.float32)   # noise reduction everywhere
        difference[200:300] += 6                                       # a stretch changed more, steadily
        difference[120] += 40
        difference[121] += 30                                          # same breakup, next frame
        difference[450] += 25
        peaks = repair_peaks(difference, 30)
        self.assertEqual([index for index, _ in peaks], [120, 450])
        self.assertGreater(peaks[0][1], peaks[1][1])

    def test_nothing_is_highlighted_in_a_clean_clip(self):
        difference = 5 + np.random.default_rng(2).normal(0, 0.5, 300).astype(np.float32)
        self.assertEqual(repair_peaks(difference, 30), [])

    def test_plan_slows_down_and_freezes_on_the_repair(self):
        plain = frame_plan(0, 120, 60, 60, [])
        self.assertEqual(plain.indices, list(range(120)))
        self.assertEqual(set(plain.modes), {"play"})
        slowed = frame_plan(0, 120, 60, 60, [60])
        self.assertAlmostEqual(slowed.duration, 2 + extra_seconds(), delta=2 / 60)
        frozen = [index for index, mode in zip(slowed.indices, slowed.modes) if mode == "freeze"]
        self.assertEqual(set(frozen), {60})
        self.assertEqual(len(frozen), 60)
        self.assertEqual(slowed.indices, sorted(slowed.indices))
        # Sound runs: playing, silent through slow motion and freeze, playing again.
        self.assertEqual([position is None for position, _ in slowed.runs], [False, True, False])

    def test_fast_recordings_are_shown_at_sixty_frames(self):
        plan = frame_plan(0, 90, 90, output_rate(90), [])
        self.assertEqual(len(plan.indices), 60)
        self.assertEqual(plan.indices[0], 0)
        self.assertTrue(all(0 < b - a <= 2 for a, b in zip(plan.indices, plan.indices[1:])))
        self.assertEqual([output_rate(rate) for rate in (25, 30, 50, 60, 90, 100, 120, 180)],
                         [25, 30, 50, 60, 60, 50, 60, 60])

    def test_best_window_holds_the_strongest_repairs(self):
        difference = 6 + np.random.default_rng(3).normal(0, 0.3, 36 * 30).astype(np.float32)
        difference[28 * 30] += 50
        difference[5 * 30] += 12
        peaks = repair_peaks(difference, 30)
        for highlights in (False, True):
            first, last = best_window(difference, peaks, 30, 15, highlights)
            self.assertLessEqual(first + 30, 28 * 30)
            self.assertLess(28 * 30, last - 30)
            self.assertLessEqual(last - first, 15 * 30)
        first, last = best_window(difference, peaks, 30, 15, True)
        self.assertAlmostEqual((last - first) / 30 + extra_seconds(), 15, delta=0.2)

    def test_pan_stays_inside_and_moves_smoothly(self):
        rng = np.random.default_rng(4)
        centre = np.where(np.arange(600) < 300, 0.15, 0.9) + rng.normal(0, 0.08, 600)
        centre[::7] = np.nan                                         # frames without motion
        for window in (0.42, 0.75):
            pan = smooth_pan(centre.astype(np.float32), 30, window)
            self.assertTrue(np.all(pan >= window / 2 - 1e-6) and np.all(pan <= 1 - window / 2 + 1e-6))
            self.assertLessEqual(float(np.abs(np.diff(pan)).max()), 0.15 / 30 + 1e-4)
            self.assertLess(float(np.abs(np.diff(pan, 2)).max()), 0.002)
            self.assertLess(pan[100], 0.5)
            self.assertGreater(pan[550], 0.5)
        self.assertTrue(np.allclose(smooth_pan(centre.astype(np.float32), 30, 1.0), 0.5))

    def test_job_is_checked(self):
        Teilauftrag().validate()
        self.assertEqual(Teilauftrag().length, 30)
        for bad in ({"format": "square"}, {"format": "vertical", "layout": "side"},
                    {"format": "landscape", "layout": "stacked"}, {"length": 20}, {"sound": "yes"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Teilauftrag(**bad).validate()
        with self.assertRaises(ValueError):
            run({"action": "share", "source": "a.mp4", "target": "b.mp4", "output": "c.mp4", "layout": "flip",
                 "format": "vertical"})


@unittest.skipUnless(tools_available(), "FFmpeg is required")
class ShareVideoTests(unittest.TestCase):
    """A 6 s recording trimmed to 0-2 s and 4-6 s, with breakups at 0.5 s and 5.5 s in the original."""

    SEGMENTS = [[0.0, 2.0], [4.0, 6.0]]
    BREAKS = (15, 165)

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        folder = Path(cls.folder.name)
        cls.original = folder / "flight.mp4"
        cls.cleaned = folder / "flight-cleaned.mp4"
        write(cls.original, [broken(index) if index in cls.BREAKS else scene(index) for index in range(6 * FPS)])
        kept = [index for start, end in cls.SEGMENTS for index in range(round(start * FPS), round(end * FPS))]
        write(cls.cleaned, [scene(index) for index in kept], audio=True)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def share(self, name: str, **options):
        job = Teilauftrag(**{"length": 0, "effort": "fast", **options})
        target = Path(self.folder.name) / name
        outcome = make_share(self.original, self.cleaned, target, self.SEGMENTS, job, lambda fraction: None,
                             threading.Event())
        self.assertTrue(outcome.result.success, outcome.result.message)
        return outcome, target

    def test_breakups_are_found_in_cleaned_time(self):
        # Source 0.5 s stays at 0.5 s; source 5.5 s is 3.5 s into the cleaned file once 2-4 s is removed.
        found = sorted(find_repairs(self.original, self.cleaned, self.SEGMENTS))
        self.assertEqual(len(found), 2)
        for moment, expected in zip(found, (0.5, 3.5)):
            self.assertAlmostEqual(moment, expected, delta=1.5 / FPS)
        # A 4 s video gets one slowed-down highlight, on one of them.
        outcome, target = self.share("found.mp4", layout="side", sound=True)
        self.assertEqual(len(outcome.highlights), 1)
        self.assertTrue(any(abs(outcome.highlights[0] - moment) < 0.01 for moment in found))
        streams = probe(target)
        self.assertEqual((int(streams["video"]["width"]), int(streams["video"]["height"])), (1280, 720))
        self.assertEqual(streams["video"]["r_frame_rate"], "30/1")
        self.assertEqual(streams["video"].get("color_space"), "bt709")
        self.assertIn("audio", streams)
        self.assertAlmostEqual(int(streams["video"]["nb_read_frames"]) / FPS, 4 + extra_seconds(), delta=0.1)

    def test_side_by_side_pairs_the_same_moment_across_the_cut(self):
        outcome, target = self.share("pairs.mp4", layout="side", highlights=False, sound=False, watermark=False)
        self.assertFalse(outcome.sound)
        frames = frames_of(target)
        self.assertEqual(len(frames), 4 * FPS)
        self.assertNotIn("audio", probe(target))
        height, width = frames.shape[1:3]
        matched = checked = 0
        for number, frame in enumerate(frames):
            if number % FPS in (0, 1, FPS - 1) or number in (59, 60):
                continue  # colour changes at whole seconds; stay clear of them
            left = frame[height // 2 - 8:height // 2 + 8, width // 4 - 8:width // 4 + 8]
            right = frame[height // 2 - 8:height // 2 + 8, 3 * width // 4 - 8:3 * width // 4 + 8]
            checked += 1
            matched += colour_class(left) == colour_class(right)
        self.assertGreaterEqual(matched, checked * 0.95)

    def test_every_layout_has_its_size_rate_length_and_sound(self):
        cases = [("landscape", "wipe", True, (1280, 720)), ("landscape", "flip", False, (1280, 720)),
                 ("vertical", "stacked", False, (1080, 1920)), ("vertical", "wipe", True, (1080, 1920))]
        for form, layout, sound, size in cases:
            with self.subTest(form=form, layout=layout):
                outcome, target = self.share(f"{form}-{layout}.mp4", format=form, layout=layout,
                                             sound=sound, highlights=False)
                streams = probe(target)
                self.assertEqual((int(streams["video"]["width"]), int(streams["video"]["height"])), size)
                self.assertEqual(streams["video"]["r_frame_rate"], "30/1")
                self.assertEqual(int(streams["video"]["nb_read_frames"]), 4 * FPS)
                self.assertEqual("audio" in streams, sound)
                self.assertEqual(outcome.size, size)

    def test_best_seconds_choose_the_stretch_with_the_breakup(self):
        with tempfile.TemporaryDirectory() as workdir:
            folder = Path(workdir)
            original, cleaned = folder / "long.mp4", folder / "long-cleaned.mp4"
            write(original, [broken(index) if index == 28 * FPS else scene(index) for index in range(36 * FPS)])
            write(cleaned, [scene(index) for index in range(36 * FPS)])
            job = Teilauftrag(length=15, effort="fast", sound=False)
            outcome = make_share(original, cleaned, folder / "long-share.mp4", None, job, lambda fraction: None,
                                 threading.Event())
            self.assertTrue(outcome.result.success, outcome.result.message)
            first, last = outcome.window
            self.assertLess(first, 28.0)
            self.assertLess(28.0, last)
            self.assertEqual(len(outcome.highlights), 1)
            self.assertAlmostEqual(outcome.highlights[0], 28.0, delta=1.5 / FPS)
            self.assertAlmostEqual(outcome.duration, 15, delta=0.3)
            streams = probe(folder / "long-share.mp4")
            self.assertAlmostEqual(int(streams["video"]["nb_read_frames"]) / FPS, outcome.duration, delta=0.05)

    def test_share_request_through_the_bridge(self):
        target = Path(self.folder.name) / "bridge-short.mp4"
        run({"action": "share", "source": str(self.original), "target": str(self.cleaned), "output": str(target),
             "segments": self.SEGMENTS, "format": "vertical", "layout": "stacked", "length": 0,
             "highlights": False, "sound": False, "effort": "fast"})
        self.assertTrue(target.is_file())
        self.assertEqual(len(list(Path(self.folder.name).glob(".bridge-short-*"))), 0)


if __name__ == "__main__":
    unittest.main()
