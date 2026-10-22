import unittest

import cv2
import numpy as np

from flugbild.analog import repair_stream


def moving_scene(count: int) -> list[np.ndarray]:
    random = np.random.default_rng(5)
    base = cv2.resize(random.integers(40, 220, size=(24, 40, 3), dtype=np.uint8), (320, 192),
                      interpolation=cv2.INTER_CUBIC)
    wide = cv2.copyMakeBorder(base, 0, 0, 0, 40, cv2.BORDER_REFLECT)
    return [wide[:, 2 * index:2 * index + 320].copy() for index in range(count)]


def chroma(frame: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb).astype(np.float32)[:, :, 1:]


class AnalogRepairTest(unittest.TestCase):
    def test_black_and_white_frame_gets_colour_back(self):
        clean = moving_scene(9)
        damaged = [frame.copy() for frame in clean]
        damaged[4] = cv2.cvtColor(cv2.cvtColor(clean[4], cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        repaired = list(repair_stream(damaged, 50))
        self.assertEqual(len(repaired), 9)
        before = np.abs(chroma(damaged[4]) - chroma(clean[4]))[8:-8, 16:-16].mean()
        after = np.abs(chroma(repaired[4]) - chroma(clean[4]))[8:-8, 16:-16].mean()
        self.assertLess(after, before * 0.5)
        luma_change = np.abs(cv2.cvtColor(repaired[4], cv2.COLOR_BGR2GRAY).astype(int)
                             - cv2.cvtColor(damaged[4], cv2.COLOR_BGR2GRAY)).mean()
        self.assertLess(luma_change, 3)

    def test_rainbow_band_is_removed(self):
        clean = moving_scene(9)
        damaged = [frame.copy() for frame in clean]
        ycc = cv2.cvtColor(clean[4], cv2.COLOR_BGR2YCrCb).astype(np.float32)
        hue = np.linspace(0, 6 * np.pi, ycc.shape[1])
        ycc[90:98, :, 1] = 128 + 70 * np.cos(hue)
        ycc[90:98, :, 2] = 128 + 70 * np.sin(hue)
        damaged[4] = cv2.cvtColor(np.clip(ycc, 0, 255).astype(np.uint8), cv2.COLOR_YCrCb2BGR)
        repaired = list(repair_stream(damaged, 50))
        band = (slice(90, 98), slice(16, -16))
        before = np.abs(chroma(damaged[4]) - chroma(clean[4]))[band].mean()
        after = np.abs(chroma(repaired[4]) - chroma(clean[4]))[band].mean()
        self.assertLess(after, before * 0.4)

    def test_zero_amount_passes_frames_through(self):
        clean = moving_scene(3)
        self.assertTrue(all(a is b for a, b in zip(repair_stream(clean, 0), clean)))


if __name__ == "__main__":
    unittest.main()
