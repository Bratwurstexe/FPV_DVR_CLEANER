"""Share videos: the cleaned clip in 16:9 for YouTube or 9:16 for Shorts, Reels and TikTok.

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
from flugbild.share import Teilauftrag, frame_indices, make_share, output_rate, smooth_pan

FPS = 30
SECONDS = 4
SIZE = (96, 72)
RED, BLUE = (40, 40, 220), (220, 60, 40)   # BGR


def scene(index: int, size: tuple[int, int] = SIZE) -> np.ndarray:
    """Red left half, blue right half, a white square going up and down at a fifth of the width."""
    width, height = size
    frame = np.empty((height, width, 3), np.uint8)
    frame[:, :width // 2] = RED
    frame[:, width // 2:] = BLUE
    side = max(6, width // 10)
    y = 4 + (index * 2) % (height - side - 8)
    x = width // 5 - side // 2
    frame[y:y + side, x:x + side] = 245
    return frame


def write(path: Path, count: int, size: tuple[int, int] = SIZE, audio: bool = True) -> None:
    width, height = size
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}", "-r", str(FPS), "-i", "pipe:0"]
    if audio:
        args.extend(["-f", "lavfi", "-i", f"sine=frequency=440:duration={count / FPS}"])
    args.extend(["-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-g", "1", "-pix_fmt", "yuv420p"])
    if audio:
        args.extend(["-c:a", "aac", "-shortest"])
    args.append(str(path))
    subprocess.run(args, input=b"".join(scene(index, size).tobytes() for index in range(count)), check=True)


def probe(path: Path) -> dict:
    result = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
                             "stream=codec_type,codec_name,pix_fmt,width,height,r_frame_rate,nb_read_frames,"
                             "color_space", "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return {stream["codec_type"]: stream for stream in json.loads(result.stdout)["streams"]}


def frames_of(path: Path) -> np.ndarray:
    info = probe(path)["video"]
    width, height = int(info["width"]), int(info["height"])
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, height, width, 3)


def colour_class(patch: np.ndarray) -> str:
    blue, _, red = patch.reshape(-1, 3).mean(axis=0)
    return "red" if red > blue + 60 else "blue" if blue > red + 60 else "other"


class PlanTests(unittest.TestCase):
    def test_frame_rate_follows_the_source_up_to_sixty(self):
        self.assertEqual([output_rate(rate) for rate in (25, 30, 50, 60, 90, 100, 120, 180)],
                         [25, 30, 50, 60, 60, 50, 60, 60])
        self.assertEqual(frame_indices(120, 30, 30), list(range(120)))
        fast = frame_indices(90, 90, output_rate(90))
        self.assertEqual(len(fast), 60)
        self.assertEqual(fast[0], 0)
        self.assertTrue(all(0 < b - a <= 2 for a, b in zip(fast, fast[1:])))

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

    def test_job_is_checked_and_old_fields_are_ignored(self):
        Teilauftrag().validate()
        for bad in ({"format": "square"}, {"sound": "yes"}, {"effort": "slow"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Teilauftrag(**bad).validate()
        old = Teilauftrag.of({"format": "vertical", "layout": "flip", "length": 30, "highlights": True,
                              "watermark": True, "wipe": "moving", "source": "a.mp4"})
        self.assertEqual(old, Teilauftrag(format="vertical", sound=False))
        # Sound defaults: on for 16:9, off for 9:16; a choice wins.
        self.assertTrue(Teilauftrag.of({"format": "landscape"}).sound)
        self.assertFalse(Teilauftrag.of({"format": "vertical"}).sound)
        self.assertTrue(Teilauftrag.of({"format": "vertical", "sound": True}).sound)


@unittest.skipUnless(tools_available(), "FFmpeg is required")
class ShareVideoTests(unittest.TestCase):
    """A 4 s cleaned clip with sound, 4:3."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.cleaned = Path(cls.folder.name) / "flight-cleaned.mp4"
        write(cls.cleaned, SECONDS * FPS)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def share(self, name: str, source: Path | None = None, **options):
        job = Teilauftrag(**{"effort": "fast", **options})
        target = Path(self.folder.name) / name
        outcome = make_share(source or self.cleaned, target, job, lambda fraction: None, threading.Event())
        self.assertTrue(outcome.result.success, outcome.result.message)
        return outcome, target

    def assert_video(self, target: Path, size: tuple[int, int], sound: bool) -> None:
        streams = probe(target)
        video = streams["video"]
        self.assertEqual((int(video["width"]), int(video["height"])), size)
        self.assertEqual(video["codec_name"], "h264")
        self.assertEqual(video["pix_fmt"], "yuv420p")
        self.assertEqual(video["r_frame_rate"], "30/1")
        self.assertEqual(video.get("color_space"), "bt709")
        self.assertEqual(int(video["nb_read_frames"]), SECONDS * FPS)   # the whole cleaned clip
        self.assertEqual("audio" in streams, sound)

    def test_youtube_centres_the_picture_on_black(self):
        for sound in (True, False):
            with self.subTest(sound=sound):
                outcome, target = self.share(f"youtube-{sound}.mp4", sound=sound)
                self.assertEqual(outcome.size, (1280, 720))
                self.assertEqual(outcome.sound, sound)
                self.assertAlmostEqual(outcome.duration, SECONDS, delta=0.01)
                self.assert_video(target, (1280, 720), sound)
        frames = frames_of(target)
        # 4:3 in 16:9: 960 px picture, 160 px black bars on both sides.
        for frame in frames[::15]:
            self.assertLess(float(frame[:, :140].max()), 40)
            self.assertLess(float(frame[:, -140:].max()), 40)
            self.assertEqual(colour_class(frame[500:600, 450:600]), "red")
            self.assertEqual(colour_class(frame[500:600, 700:1000]), "blue")

    def test_hd_sources_become_1080p(self):
        source = Path(self.folder.name) / "hd-cleaned.mp4"
        write(source, SECONDS * FPS, size=(800, 600), audio=False)
        outcome, target = self.share("hd-youtube.mp4", source=source)
        self.assertFalse(outcome.sound)                      # nothing to keep
        self.assert_video(target, (1920, 1080), False)

    def test_short_fills_the_phone_screen_and_follows_the_motion(self):
        outcome, target = self.share("flight-short.mp4", format="vertical", sound=False)
        self.assertEqual(outcome.size, (1080, 1920))
        self.assert_video(target, (1080, 1920), False)
        frames = frames_of(target)
        for frame in frames[::15]:
            # No bars: the picture covers the whole screen.
            for patch in (frame[900:1000, :60], frame[900:1000, -60:], frame[:60, 500:580], frame[-60:, 500:580]):
                self.assertGreater(float(patch.reshape(-1, 3).mean(axis=0).max()), 120)
            # The motion is in the red half: the crop sits there instead of in the middle,
            # where the right side of the screen would be blue.
            self.assertEqual(colour_class(frame[1400:1500, 760:860]), "red")

    def test_cancel_leaves_nothing_behind(self):
        target = Path(self.folder.name) / "canceled-short.mp4"
        for form in ("landscape", "vertical"):
            with self.subTest(form=form):
                cancel = threading.Event()
                outcome = make_share(self.cleaned, target, Teilauftrag(format=form, effort="fast"),
                                     lambda fraction: cancel.set(), cancel)
                self.assertFalse(outcome.result.success)
                self.assertFalse(target.exists())
                self.assertEqual(list(Path(self.folder.name).glob(".canceled-short-*")), [])

    def test_old_share_request_through_the_bridge(self):
        target = Path(self.folder.name) / "bridge-short.mp4"
        run({"action": "share", "source": str(self.cleaned), "target": str(self.cleaned), "output": str(target),
             "segments": [[0.0, 4.0]], "format": "vertical", "layout": "wipe", "length": 15,
             "highlights": True, "watermark": True, "effort": "fast"})
        self.assertTrue(target.is_file())
        self.assert_video(target, (1080, 1920), False)
        self.assertEqual(list(Path(self.folder.name).glob(".bridge-short-*")), [])


if __name__ == "__main__":
    unittest.main()
