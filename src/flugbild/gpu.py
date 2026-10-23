"""Optional OpenCL acceleration for full-resolution frame work.

OpenCV's transparent API runs the same calls on the graphics card when they
are given ``cv2.UMat`` images instead of arrays. OpenCL drivers exist for
NVIDIA, AMD, Intel and Apple GPUs, so no vendor-specific code is needed.

* Stages keep their small, branchy work (optical flow on reduced frames,
  masks, bookkeeping) on the CPU threads and hand only the heavy per-pixel
  arithmetic to the GPU. The results match the CPU path to one grey level.
* A few frames at a time share the GPU; more would only queue up there, while
  the other pool threads keep the CPU busy with flow and the other stages.
* The GPU is used only when OpenCL reports one and a short self-test agrees
  with the CPU. Otherwise, or with ``FLUGBILD_GPU=0``, everything stays on
  the CPU exactly as before.
"""

from __future__ import annotations

import os
import threading

# Reuse device buffers instead of allocating new ones for every call; without
# this, allocation dominates and serialises the threads. Read by OpenCV when
# its OpenCL context starts, so it must be set before the first GPU call.
os.environ.setdefault("OPENCV_OPENCL_BUFFERPOOL_LIMIT", str(1 << 30))

import cv2  # noqa: E402
import numpy as np  # noqa: E402


_lock = threading.Lock()
_ready: bool | None = None
_slots: threading.BoundedSemaphore | None = None


def _wanted() -> bool:
    return os.environ.get("FLUGBILD_GPU", "").strip().lower() not in ("0", "off", "no", "false", "cpu")


def _self_test() -> bool:
    """The same small warp and arithmetic on both sides must agree."""
    image = (np.random.default_rng(7).random((96, 128)) * 255).astype(np.float32)
    columns, rows = np.meshgrid(np.arange(128, dtype=np.float32) + 0.37, np.arange(96, dtype=np.float32) - 0.61)

    def chain(source, map_x, map_y):
        warped = cv2.remap(source, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        return cv2.exp(cv2.multiply(cv2.absdiff(warped, source), 0.01))

    expected = chain(image, columns, rows)
    result = chain(cv2.UMat(image), cv2.UMat(columns), cv2.UMat(rows)).get()
    return bool(np.abs(expected - result).max() < 1e-3)


def _probe() -> bool:
    if not _wanted():
        return False
    try:
        if not cv2.ocl.haveOpenCL():
            return False
        cv2.ocl.setUseOpenCL(True)
        device = cv2.ocl.Device.getDefault()
        if not device.available() or not device.type() & cv2.ocl.DEVICE_TYPE_GPU:
            return False
        return _self_test()
    except cv2.error:
        return False


def enabled() -> bool:
    """Whether stages should put their full-resolution work on the GPU."""
    global _ready
    with _lock:
        if _ready is None:
            _ready = _probe()
        return _ready


def name() -> str:
    """The GPU in use, for status messages; empty when the CPU does the work."""
    return cv2.ocl.Device.getDefault().name().strip() if enabled() else ""


def slot() -> threading.BoundedSemaphore:
    """Context manager that limits how many frames are on the GPU at once."""
    global _slots
    with _lock:
        if _slots is None:
            configured = os.environ.get("FLUGBILD_GPU_SLOTS", "")
            _slots = threading.BoundedSemaphore(int(configured) if configured.isdigit() and int(configured) > 0 else 2)
        return _slots
