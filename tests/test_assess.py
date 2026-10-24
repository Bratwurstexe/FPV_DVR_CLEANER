"""The gain / headroom estimate that guides a second pass."""

import unittest

import cv2
import numpy as np

from flugbild.assess import artefacts, assess


def scene(seed: int, noise: float, colour_noise: float) -> np.ndarray:
    random = np.random.default_rng(seed)
    base = cv2.resize(random.integers(60, 190, size=(9, 16), dtype=np.uint8), (640, 360), interpolation=cv2.INTER_CUBIC)
    frame = np.dstack([base, base, base]).astype(np.float32)
    frame += random.normal(0, noise, frame.shape)
    frame[:, :, 0] += random.normal(0, colour_noise, frame.shape[:2])
    frame[:, :, 2] -= random.normal(0, colour_noise, frame.shape[:2])
    return np.clip(frame, 0, 255).astype(np.uint8)


class AssessTests(unittest.TestCase):
    def test_artefact_levels_follow_the_noise(self):
        calm = artefacts(scene(1, 1, 0))
        noisy = artefacts(scene(1, 12, 10))
        self.assertGreater(noisy["grain"], calm["grain"] * 2)
        self.assertGreater(noisy["colour"], calm["colour"] * 2)

    def test_gain_and_headroom_are_percentages(self):
        original = np.stack([scene(i, 14, 12) for i in range(4)])
        half = np.stack([scene(i, 7, 6) for i in range(4)])
        report = assess(original, half, quality=18, signal="analog")
        self.assertTrue(0 <= report["gain_percent"] <= 100)
        self.assertTrue(0 <= report["headroom_percent"] <= 100)
        self.assertGreater(report["gain_percent"], 20)
        self.assertGreater(report["headroom_percent"], 5)
        self.assertAlmostEqual(report["reencode_loss_percent"], 3.6)
        clean = np.stack([scene(i, 1, 0) for i in range(4)])
        done = assess(original, clean, quality=14, signal="analog")
        self.assertGreater(done["gain_percent"], report["gain_percent"])
        self.assertLess(done["headroom_percent"], report["headroom_percent"])
        self.assertFalse(done["second_pass_worth"])


if __name__ == "__main__":
    unittest.main()
