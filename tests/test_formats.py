"""Output file formats: recipe field, encoder settings and real exports."""

from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from flugbild.bridge import run
from flugbild.media import VideoKenndaten, inspect_video, tools_available
from flugbild.processor import encoding_options, process_one, video_filters
from flugbild.recipe import DATEIFORMATE, Arbeitsrezept
from flugbild.segments import export_segments, join_options

INFO = VideoKenndaten(10.0, 720, 576, True, 25.0)


def option(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


class FormatRecipeTests(unittest.TestCase):
    def test_default_is_mp4_h264(self):
        self.assertEqual(Arbeitsrezept().dateiformat, "mp4-h264")
        self.assertEqual(DATEIFORMATE["mp4-h264"], ".mp4")

    def test_every_format_validates_and_unknown_is_rejected(self):
        for form in DATEIFORMATE:
            Arbeitsrezept(dateiformat=form).validate()
        for bad in ("gif", "MP4-H264", ""):
            with self.assertRaises(ValueError):
                Arbeitsrezept(dateiformat=bad).validate()

    def test_old_recipe_without_format_still_loads(self):
        with tempfile.TemporaryDirectory() as workdir:
            path = Path(workdir) / "old.json"
            data = asdict(Arbeitsrezept(signalart="digital"))
            del data["dateiformat"], data["aufwand"]
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(Arbeitsrezept.load(path).dateiformat, "mp4-h264")

    def test_format_survives_save_and_load(self):
        with tempfile.TemporaryDirectory() as workdir:
            path = Path(workdir) / "recipe.json"
            Arbeitsrezept(dateiformat="webm-vp9").save(path)
            self.assertEqual(Arbeitsrezept.load(path).dateiformat, "webm-vp9")
            data = json.loads(path.read_text(encoding="utf-8"))
            data["dateiformat"] = "avi"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                Arbeitsrezept.load(path)

    def test_export_request_needs_the_matching_extension(self):
        with self.assertRaisesRegex(ValueError, r"\.webm"):
            run({"action": "export", "source": "in.mp4", "target": "out.mp4", "mode": "manual",
                 "recipe": {"dateiformat": "webm-vp9"}})


class EncoderSettingsTests(unittest.TestCase):
    def test_h264_is_unchanged(self):
        args = encoding_options(Arbeitsrezept(kompressionswert=20), INFO, 10.0)
        self.assertEqual(args, ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                                "-crf", "20", "-preset", "faster", "-c:a", "aac", "-b:a", "128k"])

    def test_hevc_maps_quality_and_tags_for_apple(self):
        args = encoding_options(Arbeitsrezept(dateiformat="mp4-hevc", kompressionswert=20), INFO, 10.0)
        self.assertEqual(option(args, "-c:v"), "libx265")
        self.assertEqual(option(args, "-crf"), "25")
        self.assertEqual(option(args, "-tag:v"), "hvc1")
        self.assertEqual(option(args, "-c:a"), "aac")

    def test_mkv_is_h264_without_mp4_flags(self):
        args = encoding_options(Arbeitsrezept(dateiformat="mkv-h264", kompressionswert=20), INFO, 10.0)
        self.assertEqual(option(args, "-c:v"), "libx264")
        self.assertEqual(option(args, "-crf"), "20")
        self.assertNotIn("-movflags", args)

    def test_webm_is_constant_quality_vp9_with_opus(self):
        args = encoding_options(Arbeitsrezept(dateiformat="webm-vp9", kompressionswert=20, aufwand="best"),
                                INFO, 10.0)
        self.assertEqual(option(args, "-c:v"), "libvpx-vp9")
        self.assertEqual(option(args, "-crf"), "31")
        self.assertEqual(option(args, "-b:v"), "0")
        self.assertEqual(option(args, "-cpu-used"), "2")
        self.assertEqual(option(args, "-c:a"), "libopus")
        self.assertNotIn("-movflags", args)
        self.assertNotIn("-preset", args)

    def test_quality_range_stays_inside_each_encoder(self):
        for form, low, high in (("mp4-hevc", 19, 37), ("webm-vp9", 23, 47)):
            values = [int(option(encoding_options(Arbeitsrezept(dateiformat=form, kompressionswert=q), INFO, 10.0),
                                 "-crf")) for q in (14, 32)]
            self.assertEqual(values, [low, high])

    def test_size_cap_sets_bitrate_for_compressed_formats(self):
        for form in ("mp4-h264", "mp4-hevc", "mkv-h264", "webm-vp9"):
            args = encoding_options(Arbeitsrezept(dateiformat=form, dateiziel_mb=10), INFO, 10.0)
            self.assertNotIn("-crf", args)
            self.assertEqual(option(args, "-b:v"), str(int(10 * 8_000_000 * 0.94 / 10) - 128_000))

    def test_prores_ignores_quality_and_size_cap(self):
        args = encoding_options(Arbeitsrezept(dateiformat="mov-prores", dateiziel_mb=10), INFO, 10.0)
        self.assertEqual(option(args, "-c:v"), "prores_ks")
        self.assertEqual(option(args, "-profile:v"), "2")
        self.assertEqual(option(args, "-pix_fmt"), "yuv422p10le")
        self.assertEqual(option(args, "-c:a"), "pcm_s16le")
        self.assertNotIn("-crf", args)
        self.assertNotIn("-b:v", args)

    def test_filter_chain_ends_in_the_format_pixel_layout(self):
        self.assertTrue(video_filters(Arbeitsrezept()).endswith("format=yuv420p"))
        self.assertTrue(video_filters(Arbeitsrezept(dateiformat="mov-prores")).endswith("format=yuv422p10le"))
        self.assertTrue(video_filters(Arbeitsrezept(dateiformat="mov-prores"), preview=True).endswith("format=rgb24"))

    def test_join_flags_follow_the_container(self):
        self.assertEqual(join_options(Path("a.mp4"), "h264"), ["-movflags", "+faststart"])
        self.assertEqual(join_options(Path("a.mp4"), "hevc"), ["-movflags", "+faststart", "-tag:v", "hvc1"])
        self.assertEqual(join_options(Path("a.mov"), "prores"), ["-movflags", "+faststart"])
        self.assertEqual(join_options(Path("a.mkv"), "h264"), [])
        self.assertEqual(join_options(Path("a.webm"), "vp9"), [])


@unittest.skipUnless(tools_available(), "FFmpeg is required")
class FormatExportTests(unittest.TestCase):
    EXPECTED = {"mp4-h264": ("h264", "aac"), "mp4-hevc": ("hevc", "aac"), "mov-prores": ("prores", "pcm_s16le"),
                "mkv-h264": ("h264", "aac"), "webm-vp9": ("vp9", "opus")}

    @staticmethod
    def codecs(path: Path) -> tuple[str, str]:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
                                 "-of", "json", str(path)], capture_output=True, text=True, check=True)
        streams = json.loads(result.stdout)["streams"]
        pick = lambda kind: next(s["codec_name"] for s in streams if s["codec_type"] == kind)
        return pick("video"), pick("audio")

    def test_every_format_exports_and_joins(self):
        with tempfile.TemporaryDirectory() as workdir:
            folder = Path(workdir)
            source = folder / "flight.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "testsrc2=size=160x96:rate=12",
                            "-f", "lavfi", "-i", "sine=frequency=440",
                            "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", str(source)], check=True)
            info = inspect_video(source)

            def render(part_source, part_target, part_recipe, report):
                return process_one(part_source, part_target, part_recipe, info, report, threading.Event())

            for form, extension in DATEIFORMATE.items():
                with self.subTest(form=form):
                    recipe = Arbeitsrezept(signalart="digital", blockpflege=0, kantendetail=0,
                                           aufwand="fast", dateiformat=form)
                    target = folder / f"out-{form}{extension}"
                    result = export_segments(source, target, recipe, info, [(0.0, 0.8), (1.2, 2.0)], render,
                                             lambda fraction: None, threading.Event())
                    self.assertTrue(result.success, result.message)
                    self.assertEqual(self.codecs(target), self.EXPECTED[form])
                    self.assertAlmostEqual(inspect_video(target).duration, 1.6, delta=0.3)


if __name__ == "__main__":
    unittest.main()
