import unittest

import cv2
import numpy as np

from flugbild.osd import find_osd, remove_stream


def flight_with_osd(count: int) -> tuple[list[np.ndarray], np.ndarray, list[np.ndarray]]:
    random = np.random.default_rng(11)
    # Coarse brightness structure that optical flow can follow, lightly tinted.
    relief = cv2.resize(random.integers(40, 200, size=(12, 40), dtype=np.uint8), (960, 240),
                        interpolation=cv2.INTER_CUBIC)
    terrain = np.dstack([relief, (relief * 0.9).astype(np.uint8), (relief * 0.8).astype(np.uint8)])
    frames, scene = [], []
    for index in range(count):
        frame = terrain[:, 25 * index % 600:25 * index % 600 + 320].copy()
        scene.append(frame.copy())
        cv2.putText(frame, "3.90V", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 5)
        cv2.putText(frame, "3.90V", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        frames.append(frame)
    text = np.zeros((240, 320), np.uint8)
    cv2.putText(text, "3.90V", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 255, 2)
    return frames, text, scene


class OsdTest(unittest.TestCase):
    def test_static_text_is_found_and_removed(self):
        frames, text, scene = flight_with_osd(24)
        mask = find_osd(np.stack(frames))
        covered = (mask[text > 0] > 0).mean()
        self.assertGreater(covered, 0.95)
        self.assertLess((mask > 0).mean(), 0.1)
        cleaned = list(remove_stream(frames, mask))
        self.assertEqual(len(cleaned), len(frames))
        area = mask > 0
        before = np.abs(frames[12].astype(int) - scene[12])[area].mean()
        after = np.abs(cleaned[12].astype(int) - scene[12])[area].mean()
        self.assertLess(after, before * 0.45)

    def test_too_few_samples_are_rejected(self):
        frames, _, _ = flight_with_osd(3)
        with self.assertRaises(ValueError):
            find_osd(np.stack(frames))


if __name__ == "__main__":
    unittest.main()
