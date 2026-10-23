import unittest

import cv2
import numpy as np

from flugbild.digital import repair_stream


def flight(count: int) -> list[np.ndarray]:
    random = np.random.default_rng(5)
    relief = cv2.resize(random.integers(40, 200, size=(12, 40), dtype=np.uint8), (960, 240),
                        interpolation=cv2.INTER_CUBIC)
    terrain = np.dstack([relief, (relief * 0.9).astype(np.uint8), (relief * 0.7).astype(np.uint8)])
    return [terrain[:, 12 * index:12 * index + 320].copy() for index in range(count)]


def confetti(frame: np.ndarray, top: int) -> np.ndarray:
    """Lower part of ``frame`` replaced by random 4 px colour blocks, like an HDZero breakup."""
    broken = frame.copy()
    random = np.random.default_rng(9)
    height, width = frame.shape[:2]
    blocks = random.integers(0, 256, size=((height - top) // 4, width // 4, 3), dtype=np.uint8)
    broken[top:top + blocks.shape[0] * 4] = np.repeat(np.repeat(blocks, 4, axis=0), 4, axis=1)
    return broken


class BreakupTest(unittest.TestCase):
    def test_confetti_is_rebuilt_from_neighbours(self):
        clean = flight(12)
        frames = list(clean)
        frames[6] = confetti(clean[6], 120)
        repaired = list(repair_stream(frames, 35))
        self.assertEqual(len(repaired), len(frames))
        before = np.abs(frames[6].astype(int) - clean[6])[120:].mean()
        after = np.abs(repaired[6].astype(int) - clean[6])[120:].mean()
        self.assertLess(after, before * 0.15)

    def test_whole_frame_of_confetti_is_rebuilt(self):
        clean = flight(12)
        frames = list(clean)
        frames[5] = confetti(clean[5], 0)
        repaired = list(repair_stream(frames, 35))
        before = np.abs(frames[5].astype(int) - clean[5]).mean()
        after = np.abs(repaired[5].astype(int) - clean[5]).mean()
        self.assertLess(after, before * 0.15)

    def test_clean_frames_stay_untouched(self):
        frames = flight(12)
        for original, result in zip(frames, repair_stream(frames, 100)):
            np.testing.assert_array_equal(original, result)

    def test_zero_amount_passes_frames_through(self):
        frames = flight(4)
        frames[2] = confetti(frames[2], 0)
        self.assertTrue(all(a is b for a, b in zip(frames, repair_stream(frames, 0))))


if __name__ == "__main__":
    unittest.main()
