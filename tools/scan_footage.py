"""Find candidate signal disruptions in locally owned DVR recordings.

Scores are screening cues, not an objective quality metric. Review candidate
frames visually before choosing an export interval.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def score(frame: np.ndarray) -> tuple[float, float]:
    small = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    rows = np.abs(np.diff(gray, axis=0)).mean(axis=1)
    row_spike = float(np.percentile(rows, 99) / max(1, np.median(rows)))
    edge = np.abs(np.diff(gray, axis=1))
    seam = float(edge[:, 7::8].mean() / max(1, edge[:, 3::8].mean()))
    return row_spike, seam


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("videos", nargs="+", type=Path)
    parser.add_argument("--step", type=float, default=4)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    findings = []
    for path in args.videos:
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            continue
        fps = cap.get(cv2.CAP_PROP_FPS)
        duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
        candidates = []
        try:
            for second in np.arange(2, duration - 1, args.step):
                cap.set(cv2.CAP_PROP_POS_FRAMES, round(float(second) * fps))
                okay, frame = cap.read()
                if not okay:
                    continue
                row_spike, seam = score(frame)
                candidates.append((float(second), row_spike, seam, frame))
            # Keep high row-disruption and block-seam examples separately.
            picks = sorted(candidates, key=lambda item: item[1], reverse=True)[:8]
            picks += sorted(candidates, key=lambda item: item[2], reverse=True)[:8]
            unique = list({item[0]: item for item in picks}.values())
            unique.sort(key=lambda item: item[0])
            board = np.full((4 * 188, 4 * 320, 3), 30, np.uint8)
            for position, (second, line_score, block_score, frame) in enumerate(unique[:16]):
                thumbnail = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)
                y, x = divmod(position, 4)
                board[y * 188:y * 188 + 180, x * 320:x * 320 + 320] = thumbnail
                cv2.putText(board, f"{second:.0f}s  L{line_score:.1f} B{block_score:.1f}",
                            (x * 320 + 6, y * 188 + 21), cv2.FONT_HERSHEY_SIMPLEX,
                            0.52, (255, 255, 255), 2, cv2.LINE_AA)
            destination = args.output / f"{path.stem}-candidates.jpg"
            cv2.imwrite(str(destination), board)
            findings.append({"file": str(path), "duration": duration, "frames_read": len(candidates),
                             "contact_sheet": str(destination),
                             "top_line_times": [round(item[0], 2) for item in sorted(
                                 candidates, key=lambda item: item[1], reverse=True)[:8]],
                             "top_block_times": [round(item[0], 2) for item in sorted(
                                 candidates, key=lambda item: item[2], reverse=True)[:8]]})
        finally:
            cap.release()
    print(json.dumps(findings, indent=2))


if __name__ == "__main__":
    main()
