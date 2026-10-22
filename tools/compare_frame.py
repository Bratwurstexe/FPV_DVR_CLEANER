"""Inspect one local source frame against an optional neural profile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from flugbild.neural import NeuralProfile


def chroma_texture(frame: np.ndarray) -> float:
    signal = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    chroma = signal[:, :, 1:]
    return float(np.abs(chroma - cv2.GaussianBlur(chroma, (3, 3), 0)).mean())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="A frame extracted from the original footage")
    parser.add_argument("profile", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    original = cv2.imread(str(args.source))
    if original is None:
        raise ValueError("Could not load source frame")
    restored = NeuralProfile.load(args.profile).improve(original)
    difference = np.abs(original.astype(np.float32) - restored.astype(np.float32))
    report = {
        "mean_absolute_change_8bit": float(difference.mean()),
        "changed_pixels_percent": float(100 * np.any(difference > 0, axis=2).mean()),
        "chroma_texture_before": chroma_texture(original),
        "chroma_texture_after": chroma_texture(restored),
        "luma_mean_absolute_change_8bit": float(np.abs(
            cv2.cvtColor(original, cv2.COLOR_BGR2YCrCb)[:, :, 0].astype(np.float32) -
            cv2.cvtColor(restored, cv2.COLOR_BGR2YCrCb)[:, :, 0].astype(np.float32)).mean()),
    }
    h, w = original.shape[:2]
    canvas = np.empty((h, w * 2, 3), np.uint8)
    canvas[:, :w] = original
    canvas[:, w:] = restored
    cv2.putText(canvas, "BEFORE", (22, 52), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(canvas, "AFTER", (w + 22, 52), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                (255, 255, 255), 2, cv2.LINE_AA)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.output), canvas)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
