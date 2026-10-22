"""Small, original blind-neighborhood network trained from local video frames.

The center pixel is excluded from the input. This permits self-supervised
learning without pretending that damaged recordings are clean ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import tempfile
import threading
import time
from typing import Callable

import cv2
import numpy as np

from .learning import candidates


NEIGHBORS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1),
             (1, -1), (1, 0), (1, 1))
SPATIAL_INPUTS, MOTION_INPUTS, HIDDEN, OUTPUTS = 24, 36, 24, 3


def _space(frame: np.ndarray, signalart: str) -> np.ndarray:
    return cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb) if signalart == "analog" else frame


def _examples(frame: np.ndarray, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    row, col = points[:, 0], points[:, 1]
    blocks = [frame[row + dy, col + dx].astype(np.float32) / 255
              for dy, dx in NEIGHBORS]
    return np.concatenate(blocks, axis=1), frame[row, col].astype(np.float32) / 255


def _align_to(current: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Warp a neighboring frame into the current frame's coordinates."""
    height, width = current.shape[:2]
    size = (max(24, round(width * min(1.0, 320 / width))),
            max(24, round(height * min(1.0, 320 / width))))
    now = cv2.resize(cv2.cvtColor(current, cv2.COLOR_BGR2GRAY), size)
    other = cv2.resize(cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY), size)
    flow = cv2.calcOpticalFlowFarneback(now, other, None, 0.5, 3, 15, 3, 5, 1.1, 0)
    if size != (width, height):
        flow = cv2.resize(flow, (width, height), interpolation=cv2.INTER_LINEAR)
        flow[:, :, 0] *= width / size[0]
        flow[:, :, 1] *= height / size[1]
    xx, yy = np.meshgrid(np.arange(width, dtype=np.float32),
                         np.arange(height, dtype=np.float32))
    return cv2.remap(reference, xx + flow[:, :, 0], yy + flow[:, :, 1],
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)


def _motion_examples(frames: list[np.ndarray], points: np.ndarray,
                     signalart: str) -> tuple[np.ndarray, np.ndarray]:
    center = frames[2]
    base, target = _examples(_space(center, signalart), points)
    neighbors = [_space(_align_to(center, frames[index]), signalart)
                 for index in (0, 1, 3, 4)]
    row, col = points[:, 0], points[:, 1]
    temporal = [neighbor[row, col].astype(np.float32) / 255 for neighbor in neighbors]
    return np.concatenate([base, *temporal], axis=1), target


def _forward(x: np.ndarray, weights: tuple[np.ndarray, ...]) -> tuple[np.ndarray, np.ndarray]:
    w1, b1, w2, b2 = weights
    middle = np.maximum(0, x @ w1 + b1)
    return middle @ w2 + b2, middle


@dataclass(frozen=True)
class NeuralProfile:
    signalart: str
    samples: int
    sources: int
    validation_mae: float
    weights: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]
    architecture: str = "spatial"
    training_runs: int = 1

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"format": "fpv-motion-context-2", "signalart": self.signalart,
                   "samples": self.samples, "sources": self.sources,
                   "validation_mae": self.validation_mae, "architecture": self.architecture,
                   "training_runs": self.training_runs,
                   "weights": [weight.tolist() for weight in self.weights]}
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json",
                                         dir=path.parent, delete=False) as handle:
            json.dump(payload, handle, separators=(",", ":"))
            pending = Path(handle.name)
        pending.replace(path)

    @classmethod
    def load(cls, path: Path) -> NeuralProfile:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Unknown neural profile format")
        version = data.get("format")
        common = {"format", "signalart", "samples", "sources", "validation_mae", "weights"}
        if (version == "fpv-blind-neighborhood-1" and set(data) == common):
            architecture, runs = "spatial", 1
        elif (version == "fpv-motion-context-2" and
              set(data) == common | {"architecture", "training_runs"}):
            architecture, runs = data["architecture"], data["training_runs"]
        else:
            raise ValueError("Unknown neural profile format")
        if (data["signalart"] not in {"analog", "digital"}
                or type(data["samples"]) is not int or data["samples"] < 100
                or type(data["sources"]) is not int or data["sources"] < 1
                or type(data["validation_mae"]) not in (int, float)
                or not math.isfinite(data["validation_mae"])
                or not 0 <= data["validation_mae"] <= 1
                or not isinstance(data["weights"], list)
                or len(data["weights"]) != 4
                or architecture not in {"spatial", "motion5"}
                or type(runs) is not int or not 1 <= runs <= 100000):
            raise ValueError("Invalid neural profile metadata")
        weights = tuple(np.asarray(part, dtype=np.float32) for part in data["weights"])
        inputs = SPATIAL_INPUTS if architecture == "spatial" else MOTION_INPUTS
        hidden = weights[0].shape[1] if weights[0].ndim == 2 else 0
        if not 8 <= hidden <= 64 or tuple(item.shape for item in weights) != (
                (inputs, hidden), (hidden,), (hidden, OUTPUTS), (OUTPUTS,)):
            raise ValueError("Invalid neural network shape")
        if any(not np.isfinite(item).all() or np.abs(item).max() > 100 for item in weights):
            raise ValueError("Invalid neural network weights")
        return cls(data["signalart"], data["samples"], data["sources"],
                   float(data["validation_mae"]), weights, architecture, runs)

    def improve(self, frame: np.ndarray,
                context: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None) -> np.ndarray:
        if frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("Expected a three-channel video frame")
        height, width = frame.shape[:2]
        factor = min(1.0, 640 / width)
        size = (max(3, round(width * factor)), max(3, round(height * factor)))
        small = cv2.resize(frame, size, interpolation=cv2.INTER_AREA) if factor < 1 else frame
        signal = _space(small, self.signalart)
        padded = cv2.copyMakeBorder(signal, 1, 1, 1, 1, cv2.BORDER_REFLECT_101)
        h, w = signal.shape[:2]
        features = np.concatenate([
            padded[1 + dy:1 + dy + h, 1 + dx:1 + dx + w].reshape(-1, 3)
            for dy, dx in NEIGHBORS
        ], axis=1).astype(np.float32) / 255
        aligned: list[np.ndarray] = []
        if self.architecture == "motion5":
            for neighbor in context or (frame, frame, frame, frame):
                resized = cv2.resize(neighbor, size, interpolation=cv2.INTER_AREA) if factor < 1 else neighbor
                aligned.append(_space(_align_to(small, resized), self.signalart))
            features = np.concatenate([features, *[item.reshape(-1, 3).astype(np.float32) / 255
                                                     for item in aligned]], axis=1)
        estimates = np.empty((h * w, 3), dtype=np.float32)
        for start in range(0, len(features), 32768):
            end = min(start + 32768, len(features))
            estimates[start:end] = _forward(features[start:end], self.weights)[0]
        estimate = np.clip(estimates.reshape(h, w, 3) * 255, 0, 255)
        present = signal.astype(np.float32)
        if self.signalart == "analog":
            gap = np.abs(present[:, :, 1:] - estimate[:, :, 1:]).mean(axis=2)
            gate = (gap > max(5, self.validation_mae * 255 * 1.3)) & (gap < 75)
            strength = cv2.GaussianBlur(gate.astype(np.float32), (3, 3), 0.6)[:, :, None] * 0.45
            present[:, :, 1:] += (estimate[:, :, 1:] - present[:, :, 1:]) * strength
            if aligned:
                stack = np.stack([item[:, :, 1:].astype(np.float32) for item in aligned])
                consensus = np.median(stack, axis=0)
                agreement = np.median(np.abs(stack - consensus), axis=(0, 3))
                color_gap = np.abs(present[:, :, 1:] - consensus).mean(axis=2)
                brightness = np.stack([item[:, :, 0].astype(np.float32) for item in aligned])
                luma_agreement = np.median(np.abs(brightness - present[:, :, 0]), axis=0)
                reliable = (agreement < 9) & (luma_agreement < 18)
                stripe = reliable & (color_gap > 12)
                # Row-shaped disturbances receive more temporal chroma support.
                row_fraction = stripe.mean(axis=1)
                wide = cv2.GaussianBlur((row_fraction > 0.22).astype(np.float32)[:, None],
                                        (1, 7), 0)[:, 0] > 0.3
                strength = np.where(stripe, np.where(wide[:, None], 0.72, 0.5), 0.0)
                guard = max(1, h // 14)
                strength[:guard] = 0
                strength[-guard:] = 0
                present[:, :, 1:] += (consensus - present[:, :, 1:]) * strength[:, :, None]
            result = cv2.cvtColor(np.clip(present, 0, 255).astype(np.uint8), cv2.COLOR_YCrCb2BGR)
        else:
            gap = np.abs(present - estimate).mean(axis=2)
            gate = (gap > max(8, self.validation_mae * 255 * 1.4)) & (gap < 90)
            guard = max(1, h // 12)
            gate[:guard] = False
            gate[-guard:] = False
            strength = cv2.GaussianBlur(gate.astype(np.float32), (3, 3), 0.6)[:, :, None] * 0.35
            result = np.clip(present + (estimate - present) * strength, 0, 255).astype(np.uint8)
        return cv2.resize(result, (width, height), interpolation=cv2.INTER_LINEAR) if factor < 1 else result


def train_network(folder: Path, signalart: str, seconds: int,
                  announce: Callable[[int, float], None], stop: threading.Event,
                  initial_profile: NeuralProfile | None = None) -> NeuralProfile:
    if signalart not in {"analog", "digital"} or not 1 <= seconds <= 86400:
        raise ValueError("Choose Analog or HDZero and up to 24 hours")
    if initial_profile is not None and initial_profile.signalart != signalart:
        raise ValueError("Existing model does not match the selected signal type")
    clips = candidates(folder)
    if not clips:
        raise ValueError("No supported footage in the selected folder")
    rng = np.random.default_rng(7341)
    deadline = time.monotonic() + seconds
    collection_deadline = time.monotonic() + min(max(seconds * 0.35, 0.5), 30)
    batches: list[tuple[np.ndarray, np.ndarray]] = []
    touched = 0
    for clip in clips:
        if stop.is_set() or time.monotonic() >= collection_deadline:
            break
        capture = cv2.VideoCapture(str(clip))
        if not capture.isOpened():
            continue
        found = False
        try:
            total = max(1, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
            if total < 5:
                continue
            for position in np.linspace(2, total - 3, 24, dtype=int):
                if stop.is_set() or time.monotonic() >= collection_deadline:
                    break
                capture.set(cv2.CAP_PROP_POS_FRAMES, int(position) - 2)
                window = []
                for _ in range(5):
                    okay, frame = capture.read()
                    if not okay or min(frame.shape[:2]) < 3:
                        break
                    window.append(cv2.resize(frame, (min(frame.shape[1], 640),
                                                     min(frame.shape[0], 360)),
                                             interpolation=cv2.INTER_AREA))
                if len(window) != 5:
                    continue
                found = True
                h, w = window[2].shape[:2]
                points = np.column_stack((rng.integers(1, h - 1, 384),
                                          rng.integers(1, w - 1, 384)))
                batches.append(_motion_examples(window, points, signalart))
                announce(sum(len(x) for x, _ in batches), 0.0)
        finally:
            capture.release()
        touched += int(found)
    if stop.is_set():
        raise InterruptedError("Training canceled")
    if not batches or sum(len(x) for x, _ in batches) < 100:
        raise ValueError("Could not read enough training frames")
    x = np.concatenate([part[0] for part in batches])
    y = np.concatenate([part[1] for part in batches])
    order = rng.permutation(len(x))
    x, y = x[order], y[order]
    holdout = max(100, len(x) // 10)
    x_train, y_train = x[holdout:], y[holdout:]
    x_valid, y_valid = x[:holdout], y[:holdout]
    if initial_profile is None:
        weights = [rng.normal(0, 0.08, (MOTION_INPUTS, HIDDEN)).astype(np.float32),
                   np.zeros(HIDDEN, np.float32),
                   rng.normal(0, 0.08, (HIDDEN, OUTPUTS)).astype(np.float32),
                   np.zeros(OUTPUTS, np.float32)]
    else:
        weights = [part.copy() for part in initial_profile.weights]
        if initial_profile.architecture == "spatial":
            expanded = np.zeros((MOTION_INPUTS, weights[0].shape[1]), np.float32)
            expanded[:SPATIAL_INPUTS] = weights[0]
            weights[0] = expanded
    first = [np.zeros_like(part) for part in weights]
    second = [np.zeros_like(part) for part in weights]
    best = tuple(part.copy() for part in weights)
    baseline, _ = _forward(x_valid, weights)
    best_error = float(np.abs(baseline - y_valid).mean())
    step = 0
    while not stop.is_set() and (time.monotonic() < deadline or step < 20):
        indexes = rng.integers(0, len(x_train), 512)
        block, target = x_train[indexes], y_train[indexes]
        prediction, hidden = _forward(block, weights)
        error = np.clip(prediction - target, -0.2, 0.2) * (2 / len(block))
        dw2 = hidden.T @ error
        db2 = error.sum(axis=0)
        hidden_error = (error @ weights[2].T) * (hidden > 0)
        dw1 = block.T @ hidden_error
        db1 = hidden_error.sum(axis=0)
        step += 1
        for index, gradient in enumerate((dw1, db1, dw2, db2)):
            first[index] = first[index] * 0.9 + gradient * 0.1
            second[index] = second[index] * 0.999 + gradient * gradient * 0.001
            weights[index] -= 0.003 * (first[index] / (1 - 0.9 ** step)) / (
                np.sqrt(second[index] / (1 - 0.999 ** step)) + 1e-8)
        if step % 200 == 0:
            estimate, _ = _forward(x_valid, weights)
            score = float(np.abs(estimate - y_valid).mean())
            if score < best_error:
                best_error = score
                best = tuple(part.copy() for part in weights)
            announce(len(x), score)
    if stop.is_set():
        raise InterruptedError("Training canceled")
    if not math.isfinite(best_error):
        estimate, _ = _forward(x_valid, weights)
        best_error = float(np.abs(estimate - y_valid).mean())
        best = tuple(part.copy() for part in weights)
    return NeuralProfile(signalart, len(x) + (initial_profile.samples if initial_profile else 0),
                         touched + (initial_profile.sources if initial_profile else 0),
                         best_error, best, "motion5",
                         1 + (initial_profile.training_runs if initial_profile else 0))
