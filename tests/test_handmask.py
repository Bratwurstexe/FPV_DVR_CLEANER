import json
import subprocess
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np

from flugbild import handmask
from flugbild.bridge import run
from flugbild.osd import find_osd, remove_stream
from flugbild.recipe import Arbeitsrezept
from flugbild.restoration import _crop, encoder_detail

from test_osd import flight_with_osd


def make_clip(path: Path, seconds: float = 1.5) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=160x96:rate=12", "-t", str(seconds),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


class MergeTest(unittest.TestCase):
    def test_add_joins_and_erase_leaves_the_mask(self):
        mask = np.zeros((20, 30), np.uint8)
        mask[2:8, 2:8] = 255
        guide = np.where(mask > 0, 40, 0).astype(np.uint8)
        add = np.zeros_like(mask)
        add[12:16, 20:28] = 255
        erase = np.zeros_like(mask)
        erase[2:8, 2:5] = 255
        merged, cleared, always, never = handmask.merge((mask, guide), (add, erase), mask.shape)
        self.assertTrue((merged[12:16, 20:28] == 255).all())
        self.assertTrue((merged[2:8, 2:5] == 0).all())
        self.assertTrue((merged[2:8, 5:8] == 255).all())
        # The on-screen check never gates corrected pixels.
        self.assertTrue((cleared[(add > 0) | (erase > 0)] == 0).all())
        self.assertTrue((cleared[2:8, 5:8] == 40).all())
        np.testing.assert_array_equal(always, add)
        np.testing.assert_array_equal(never, erase)

    def test_without_corrections_nothing_changes(self):
        overlay = (np.full((4, 4), 255, np.uint8), np.zeros((4, 4), np.uint8))
        self.assertIs(handmask.merge(overlay, None, (4, 4)), overlay)
        self.assertIsNone(handmask.merge(None, None, (4, 4)))

    def test_hand_mask_alone_without_a_search(self):
        add = np.zeros((6, 6), np.uint8)
        add[1:3, 1:3] = 255
        merged, *_ = handmask.merge(None, (add, np.zeros_like(add)), add.shape)
        np.testing.assert_array_equal(merged, add)

    def test_file_round_trip_and_digest(self):
        with tempfile.TemporaryDirectory() as workdir:
            path = Path(workdir) / "hand ä.png"
            add = np.zeros((10, 12), np.uint8)
            add[1:4, 1:4] = 255
            erase = np.zeros_like(add)
            erase[6:9, 6:10] = 255
            handmask.write(path, add, erase)
            read_add, read_erase = handmask.read(path, (10, 12))
            np.testing.assert_array_equal(read_add, add)
            np.testing.assert_array_equal(read_erase, erase)
            # A mask painted on another size is scaled to the frame.
            self.assertEqual(handmask.read(path, (20, 24))[0].shape, (20, 24))
            first = handmask.digest(path)
            self.assertEqual(len(first), 16)
            erase[0, 0] = 255
            handmask.write(path, add, erase)
            self.assertNotEqual(handmask.digest(path), first)
            self.assertEqual(handmask.digest(None), "")
            self.assertIsNone(handmask.read("", (10, 12)))
            with self.assertRaises(ValueError):
                handmask.read(Path(workdir) / "missing.png", (10, 12))

    def test_crop_cuts_every_layer(self):
        layers = tuple(np.arange(48, dtype=np.uint8).reshape(6, 8) for _ in range(4))
        cropped = _crop(layers, (2, 1, 4, 3))
        self.assertEqual(len(cropped), 4)
        for layer in cropped:
            self.assertEqual(layer.shape, (3, 4))


class RemovalTest(unittest.TestCase):
    def test_added_area_is_always_removed(self):
        frames, text, scene = flight_with_osd(24)
        # The search missed the text completely; the hand correction adds it.
        add = cv2.dilate(text, np.ones((9, 9), np.uint8))
        empty = np.zeros_like(text)
        mask, guide, always, never = handmask.merge((empty, empty), (add, empty), text.shape)
        cleaned = list(remove_stream(frames, mask, guide, always=always, never=never))
        self.assertEqual(len(cleaned), len(frames))
        area = text > 0
        for index in (3, 12, 20):
            before = np.abs(frames[index].astype(int) - scene[index])[area].mean()
            after = np.abs(cleaned[index].astype(int) - scene[index])[area].mean()
            self.assertLess(after, before * 0.6)

    def test_erased_area_is_never_touched(self):
        frames, text, _ = flight_with_osd(24)
        found = find_osd(np.stack(frames))
        erase = cv2.dilate(text, np.ones((25, 25), np.uint8))
        mask, guide, always, never = handmask.merge(found, (np.zeros_like(text), erase), text.shape)
        cleaned = list(remove_stream(frames, mask, guide, always=always, never=never))
        # Away from the seam feather, the erased area is exactly the input.
        inside = cv2.erode(erase, np.ones((17, 17), np.uint8)) > 0
        self.assertTrue(inside[text > 0].all())
        for original, result in zip(frames, cleaned):
            np.testing.assert_array_equal(result[inside], original[inside])


class BridgeTest(unittest.TestCase):
    def test_osd_action_merges_the_hand_mask(self):
        with tempfile.TemporaryDirectory() as workdir:
            folder = Path(workdir)
            source = folder / "flight.mp4"
            make_clip(source)
            add = np.zeros((96, 160), np.uint8)
            add[0:24, 0:40] = 255
            hand = folder / "hand.png"
            handmask.write(hand, add, np.zeros_like(add))
            lines = []
            import flugbild.bridge as bridge
            original = bridge.emit
            bridge.emit = lambda kind, **fields: lines.append((kind, fields))
            try:
                run({"action": "osd", "source": str(source), "target": str(folder / "marked.png"),
                     "mask_target": str(folder / "auto.png"), "frame_target": str(folder / "frame.png"),
                     "manual_mask": str(hand)})
                result = lines[-1][1]
                self.assertTrue(result["found"])
                self.assertGreaterEqual(result["coverage"], 100 * (add > 0).mean() - 1e-6)
                self.assertEqual(cv2.imread(str(folder / "auto.png"), cv2.IMREAD_GRAYSCALE).shape, (96, 160))
                self.assertEqual(cv2.imread(str(folder / "frame.png")).shape, (96, 160, 3))
                # Everything erased: nothing is left to remove.
                handmask.write(hand, np.zeros_like(add), np.full_like(add, 255))
                run({"action": "osd", "source": str(source), "target": str(folder / "marked.png"),
                     "manual_mask": str(hand)})
                self.assertFalse(lines[-1][1]["found"])
                self.assertEqual(lines[-1][1]["coverage"], 0.0)
            finally:
                bridge.emit = original

    def test_reference_render(self):
        with tempfile.TemporaryDirectory() as workdir:
            folder = Path(workdir)
            source = folder / "flight.mp4"
            make_clip(source)
            plain = dict(signalart="digital", blockpflege=0, kantendetail=0)
            looked = dict(plain, look=True, look_vignette=1.0)
            # A reference equal to the recipe gives the same picture on both sides.
            paths = {"before": str(folder / "before.png"), "after": str(folder / "after.png")}
            run({"action": "preview", "source": str(source), "seconds": 0.7, "recipe": looked,
                 "reference_recipe": looked, **paths})
            np.testing.assert_array_equal(cv2.imread(paths["before"]), cv2.imread(paths["after"]))
            run({"action": "preview", "source": str(source), "seconds": 0.7, "recipe": looked,
                 "reference_recipe": plain, **paths})
            self.assertLess(cv2.imread(paths["after"])[:4, :4].mean(), cv2.imread(paths["before"])[:4, :4].mean() - 5)


class RecipeTest(unittest.TestCase):
    def test_recipes_without_the_hand_mask_still_load(self):
        with tempfile.TemporaryDirectory() as workdir:
            path = Path(workdir) / "old.json"
            data = asdict(Arbeitsrezept(signalart="digital"))
            del data["osd_handmaske"]
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(Arbeitsrezept.load(path).osd_handmaske, "")
        with self.assertRaises(ValueError):
            Arbeitsrezept(osd_handmaske=3).validate()

    def test_encoder_detail_matches_the_setting(self):
        random = np.random.default_rng(3)
        texture = random.integers(0, 255, size=(32, 32, 3), dtype=np.uint8)
        flat = np.full((32, 32, 3), 120, np.uint8)
        off = Arbeitsrezept(kantendetail=0)
        on = Arbeitsrezept(kantendetail=60)
        np.testing.assert_array_equal(encoder_detail(texture, off), texture)
        self.assertFalse(np.array_equal(encoder_detail(texture, on), texture))
        self.assertLessEqual(np.abs(encoder_detail(flat, on).astype(int) - flat).max(), 1)
        # Fusion lifts detail itself; the encoder then adds none.
        fused = Arbeitsrezept(kantendetail=60, rauschminderung=30)
        np.testing.assert_array_equal(encoder_detail(texture, fused), texture)


if __name__ == "__main__":
    unittest.main()
