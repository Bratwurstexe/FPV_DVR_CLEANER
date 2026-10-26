"""Recordings whose sound ends a little before the picture export in full.

The encoder used to stop with the sound (-shortest) while frames were still
being piped in, and the export failed with "Broken pipe".
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from flugbild.bridge import run
from flugbild.media import inspect_video, tools_available


def clip_with_short_sound(path: Path) -> None:
    """2 s of picture, 1.6 s of sound, in one file."""
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=0x406080:size=320x240:rate=25:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1.6",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)


def stream_duration(path: Path, kind: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", kind, "-show_entries", "stream=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["streams"][0]["duration"])


@unittest.skipUnless(tools_available(), "FFmpeg is required")
class ShortSoundTest(unittest.TestCase):
    def test_whole_clip_exports_with_padded_sound(self):
        with tempfile.TemporaryDirectory() as workdir:
            folder = Path(workdir)
            source = folder / "flight.mp4"
            clip_with_short_sound(source)
            self.assertLess(stream_duration(source, "a"), stream_duration(source, "v") - 0.2)
            for name, recipe in (("analog", dict(signalart="analog", farbberuhigung=40, rauschminderung=30)),
                                 ("osd", dict(signalart="analog", farbberuhigung=0, osd_entfernen=True)),
                                 ("hdzero", dict(signalart="digital", blockpflege=35))):
                with self.subTest(name):
                    target = folder / f"{name}.mp4"
                    run({"action": "export", "source": str(source), "target": str(target), "mode": "manual",
                         "recipe": recipe})
                    made = inspect_video(target)
                    self.assertTrue(made.has_audio)
                    self.assertAlmostEqual(made.duration, 2.0, delta=0.15)
                    self.assertAlmostEqual(stream_duration(target, "v"), 2.0, delta=0.15)


if __name__ == "__main__":
    unittest.main()
