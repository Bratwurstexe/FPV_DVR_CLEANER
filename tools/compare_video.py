"""Create synchronized before/after contact sheets from two local videos."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def frame_at(capture: cv2.VideoCapture, index: int) -> np.ndarray:
    capture.set(cv2.CAP_PROP_POS_FRAMES, index)
    okay, frame = capture.read()
    if not okay:
        raise ValueError(f"Could not decode frame {index}")
    return frame


def chroma_texture(frame: np.ndarray) -> float:
    channels = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb).astype(np.float32)[:, :, 1:]
    return float(np.abs(channels - cv2.GaussianBlur(channels, (3, 3), 0)).mean())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("original", type=Path)
    parser.add_argument("cleaned", type=Path)
    parser.add_argument("output_folder", type=Path)
    parser.add_argument("--seconds", type=float, nargs="+", default=[2, 7.5, 13])
    args = parser.parse_args()
    args.output_folder.mkdir(parents=True, exist_ok=True)
    before = cv2.VideoCapture(str(args.original))
    after = cv2.VideoCapture(str(args.cleaned))
    try:
        fps_a = before.get(cv2.CAP_PROP_FPS)
        fps_b = after.get(cv2.CAP_PROP_FPS)
        frames_a = before.get(cv2.CAP_PROP_FRAME_COUNT)
        frames_b = after.get(cv2.CAP_PROP_FRAME_COUNT)
        if abs(fps_a - fps_b) > 0.01 or abs(frames_a - frames_b) > 1:
            raise ValueError("Videos do not have matching frame timing")
        results = []
        for second in args.seconds:
            index = round(second * fps_a)
            first = frame_at(before, index)
            second_frame = frame_at(after, index)
            if first.shape != second_frame.shape:
                raise ValueError("Video dimensions differ")
            out_width = 960
            out_height = round(first.shape[0] * out_width / first.shape[1])
            pair = np.hstack([
                cv2.resize(first, (out_width, out_height), interpolation=cv2.INTER_AREA),
                cv2.resize(second_frame, (out_width, out_height), interpolation=cv2.INTER_AREA),
            ])
            board = np.zeros((out_height + 50, out_width * 2, 3), np.uint8)
            board[:50] = (37, 43, 49)
            board[50:] = pair
            cv2.putText(board, f"ORIGINAL  |  {second:g} s", (20, 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (245, 245, 245), 2, cv2.LINE_AA)
            cv2.putText(board, f"TRAINED MODEL  |  {second:g} s", (out_width + 20, 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (245, 245, 245), 2, cv2.LINE_AA)
            destination = args.output_folder / f"before-after-{str(second).replace('.', '-')}.png"
            cv2.imwrite(str(destination), board)
            results.append({"second": second, "image": str(destination),
                            "chroma_texture_original": chroma_texture(first),
                            "chroma_texture_cleaned": chroma_texture(second_frame),
                            "mean_absolute_pixel_difference": float(np.abs(
                                first.astype(np.float32) - second_frame.astype(np.float32)).mean())})
        print(json.dumps(results, indent=2))
    finally:
        before.release()
        after.release()


if __name__ == "__main__":
    main()
