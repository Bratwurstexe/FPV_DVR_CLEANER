"""Several kept parts of one recording, and the audio outline of the trim editor.

The restoration pipeline works on one continuous cut. A recording trimmed into
several segments is rendered segment by segment with the same settings and the
parts are then joined without re-encoding, so a multi-segment export costs the
same as one long cut.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import threading
from typing import Callable

import numpy as np

from .media import PROCESS_FLAGS, VideoKenndaten
from .processor import Laufergebnis, diagnostic_tail, temporary_output
from .recipe import Arbeitsrezept

Segment = tuple[float, float]
Renderer = Callable[[Path, Path, Arbeitsrezept, Callable[[float], None]], Laufergebnis]


def normalize_segments(raw: object, duration: float) -> list[Segment]:
    """Sorted, clipped, non-overlapping segments; empty or missing means the whole clip."""
    if not raw:
        return [(0.0, duration)]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("Segments must be a list of [start, end] pairs")
    cleaned: list[Segment] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("Segments must be a list of [start, end] pairs")
        start, end = float(item[0]), float(item[1])
        start = min(max(start, 0.0), duration)
        end = min(max(end, 0.0), duration)
        if end - start >= 0.1:
            cleaned.append((start, end))
    cleaned.sort()
    merged: list[Segment] = []
    for start, end in cleaned:
        if merged and start <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    if not merged:
        raise ValueError("The selected cut contains no frames")
    return merged


def segment_recipe(recipe: Arbeitsrezept, segment: Segment, duration: float) -> Arbeitsrezept:
    start, end = segment
    return replace(recipe, schnittbeginn=start, schnittende=0.0 if end >= duration - 1e-6 else end)


def concat_files(parts: list[Path], target: Path) -> Laufergebnis:
    """Join parts that were encoded with the same settings, without re-encoding."""
    pending = temporary_output(target)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", prefix="fpv-concat-", delete=False,
                                     encoding="utf-8") as listing:
        for part in parts:
            escaped = str(part.resolve()).replace("\\", "/").replace("'", "'\\''")
            listing.write(f"file '{escaped}'\n")
        listing_path = Path(listing.name)
    log = tempfile.TemporaryFile(mode="w+b")
    try:
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
                               "-f", "concat", "-safe", "0", "-i", str(listing_path),
                               "-c", "copy", "-movflags", "+faststart", str(pending)],
                              stdout=subprocess.DEVNULL, stderr=log, creationflags=PROCESS_FLAGS)
        if proc.returncode:
            pending.unlink(missing_ok=True)
            return Laufergebnis(parts[0], target, False, diagnostic_tail(log) or "Could not join the parts")
        pending.replace(target)
        return Laufergebnis(parts[0], target, True, "Done")
    finally:
        log.close()
        listing_path.unlink(missing_ok=True)
        pending.unlink(missing_ok=True)


def export_segments(source: Path, target: Path, recipe: Arbeitsrezept, info: VideoKenndaten,
                    segments: list[Segment], render: Renderer,
                    progress: Callable[[float], None], cancel: threading.Event) -> Laufergebnis:
    """Render every kept segment with the same recipe and join them into one file."""
    if len(segments) == 1:
        return render(source, target, segment_recipe(recipe, segments[0], info.duration), progress)
    total = sum(end - start for start, end in segments)
    parts: list[Path] = []
    done = 0.0
    try:
        for index, segment in enumerate(segments):
            if cancel.is_set():
                return Laufergebnis(source, target, False, "Canceled")
            share = (segment[1] - segment[0]) / total
            part = target.with_name(f".{target.stem}-part{index + 1:02d}{target.suffix}")
            parts.append(part)
            before = done
            result = render(source, part, segment_recipe(recipe, segment, info.duration),
                            lambda fraction, before=before, share=share: progress(min(1.0, before + share * fraction)))
            if not result.success:
                return result
            done = before + share
        joined = concat_files(parts, target)
        if joined.success:
            progress(1.0)
        return joined
    finally:
        for part in parts:
            part.unlink(missing_ok=True)


def audio_outline(source: Path, info: VideoKenndaten, buckets: int = 1500) -> list[float]:
    """Peak level per time bucket, 0..1, for the waveform track; empty without audio."""
    if not info.has_audio or info.duration <= 0:
        return []
    rate = 1000
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(source),
                           "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                          capture_output=True, timeout=600, creationflags=PROCESS_FLAGS)
    if proc.returncode or not proc.stdout:
        return []
    samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    buckets = max(1, min(buckets, len(samples)))
    width = len(samples) // buckets
    peaks = np.abs(samples[:width * buckets]).reshape(buckets, width).max(axis=1)
    # Quiet DVR audio still gets a readable outline; the loudest part reaches the top.
    top = float(peaks.max()) or 1.0
    return [round(float(peak / top), 3) for peak in peaks]
