import unittest

import cv2
import numpy as np

from flugbild.learning import Signalsignatur
from flugbild.restoration import restore_frame


class RestorationTest(unittest.TestCase):
    def test_isolated_digital_damage_uses_neighbor(self):
        random = np.random.default_rng(17)
        clean = random.integers(45, 210, size=(128, 128, 3), dtype=np.uint8)
        damaged = clean.copy()
        damaged[48:80, 48:80] = random.integers(0, 256, size=(32, 32, 3), dtype=np.uint8)
        profile = Signalsignatur(1, "digital", 12, 3.0, 1.0, 1)
        repaired = restore_frame(damaged, clean, "digital", 80, profile)
        before = np.abs(damaged[52:76, 52:76].astype(np.float32) - clean[52:76, 52:76]).mean()
        after = np.abs(repaired[52:76, 52:76].astype(np.float32) - clean[52:76, 52:76]).mean()
        self.assertLess(after, before)

    def test_analog_chroma_noise_is_reduced(self):
        random = np.random.default_rng(23)
        clean = np.full((96, 128, 3), (50, 120, 150), np.uint8)
        ycc = cv2.cvtColor(clean, cv2.COLOR_BGR2YCrCb)
        noisy = ycc.astype(np.int16)
        noisy[:, :, 1:] += random.integers(-30, 31, size=(96, 128, 2))
        noisy = cv2.cvtColor(np.clip(noisy, 0, 255).astype(np.uint8), cv2.COLOR_YCrCb2BGR)
        profile = Signalsignatur(1, "analog", 12, 5.0, 0.0, 1)
        repaired = restore_frame(noisy, clean, "analog", 80, profile)
        self.assertLess(np.abs(repaired.astype(np.int16) - clean).mean(),
                        np.abs(noisy.astype(np.int16) - clean).mean())


if __name__ == "__main__":
    unittest.main()
