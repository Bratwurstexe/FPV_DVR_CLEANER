import os
import unittest
from unittest import mock

import cv2
import numpy as np

from flugbild import gpu
from flugbild.upscale import fuse_stream


def flight(count=8, width=320, height=240):
    """A textured scene drifting sideways, with grain."""
    rng = np.random.default_rng(3)
    scene = cv2.GaussianBlur((rng.random((height, width + 4 * count, 3)) * 255).astype(np.uint8), (0, 0), 2)
    frames = []
    for index in range(count):
        frame = scene[:, 3 * index:3 * index + width].astype(np.int16) + rng.integers(-12, 13, (height, width, 3))
        frames.append(np.clip(frame, 0, 255).astype(np.uint8))
    return frames


class GpuTest(unittest.TestCase):
    def test_gpu_can_be_turned_off(self):
        with mock.patch.dict(os.environ, {"FLUGBILD_GPU": "0"}):
            self.assertFalse(gpu._probe())

    def compare(self, interpolation, tolerance):
        frames = flight()
        options = dict(percent=150, calm=0.5, lift=0.3, radius=2, interpolation=interpolation)
        on_gpu = list(fuse_stream(frames, **options))
        with mock.patch.object(gpu, "enabled", return_value=False):
            on_cpu = list(fuse_stream(frames, **options))
        self.assertEqual(len(on_gpu), len(frames))
        self.assertEqual(on_gpu[0].shape, (360, 480, 3))
        for a, b in zip(on_gpu, on_cpu):
            self.assertLessEqual(int(np.abs(a.astype(np.int16) - b).max()), tolerance)

    @unittest.skipUnless(gpu.enabled(), "no OpenCL GPU on this computer")
    def test_gpu_fusion_matches_the_cpu(self):
        self.compare(cv2.INTER_LINEAR, 1)

    @unittest.skipUnless(gpu.enabled(), "no OpenCL GPU on this computer")
    def test_gpu_cubic_warp_matches_the_cpu(self):
        # Built from bilinear lookups on the GPU; OpenCV's CPU bicubic rounds
        # its fractions to 1/32 pixel, so a little more tolerance.
        self.compare(cv2.INTER_CUBIC, 2)


if __name__ == "__main__":
    unittest.main()
