"""Frame-by-frame export path: analog chroma repair, trained profiles, OSD removal.

The methods use optical-flow alignment and conservative error gating. They do
not claim to reconstruct content absent from every neighboring frame.
"""

from __future__ import annotations

from collections import deque
from itertools import islice
from pathlib import Path
import subprocess
import tempfile
import threading
from typing import Callable, Iterable, Iterator

import cv2
import numpy as np

from .analog import repair_stream
from .learning import Signalsignatur
from .neural import NeuralProfile
from .media import PROCESS_FLAGS, VideoKenndaten
from .osd import detect_osd, remove_stream
from .processor import (Laufergebnis, caption_workspace, encoding_options,
                        diagnostic_tail, output_duration, temporary_output, video_filters)
from .recipe import Arbeitsrezept
from .upscale import fuse_stream, target_size


def _aligned_reference(current: np.ndarray, reference: np.ndarray) -> np.ndarray:
    height, width = current.shape[:2]
    factor = min(1.0, 640 / width)
    small_size = (max(32, round(width * factor)), max(32, round(height * factor)))
    current_gray = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
    reference_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    small_current = cv2.resize(current_gray, small_size, interpolation=cv2.INTER_AREA)
    small_reference = cv2.resize(reference_gray, small_size, interpolation=cv2.INTER_AREA)
    displacement = cv2.calcOpticalFlowFarneback(
        small_current, small_reference, None, 0.5, 3, 17, 3, 5, 1.2, 0,
    )
    if factor != 1:
        displacement = cv2.resize(displacement, (width, height), interpolation=cv2.INTER_LINEAR) / factor
    x_grid, y_grid = np.meshgrid(np.arange(width, dtype=np.float32),
                                 np.arange(height, dtype=np.float32))
    return cv2.remap(reference, x_grid + displacement[:, :, 0],
                     y_grid + displacement[:, :, 1], cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE)


def restore_frame(current: np.ndarray, previous: np.ndarray | None, signalart: str,
                  amount: int, fingerprint: Signalsignatur) -> np.ndarray:
    """Return a repaired frame while leaving the input arrays untouched."""
    if previous is None or amount == 0 or previous.shape != current.shape:
        return current.copy()
    aligned = _aligned_reference(current, previous)
    if signalart == "analog":
        present = cv2.cvtColor(current, cv2.COLOR_BGR2YCrCb).astype(np.float32)
        neighbor = cv2.cvtColor(aligned, cv2.COLOR_BGR2YCrCb).astype(np.float32)
        luma_gap = np.abs(present[:, :, 0] - neighbor[:, :, 0])
        chroma_gap = np.abs(present[:, :, 1:] - neighbor[:, :, 1:]).mean(axis=2)
        gate = (luma_gap < 14 + amount * 0.12) & (
            chroma_gap > max(2.0, fingerprint.farbrauschen * 1.3)
        )
        strength = (0.18 + amount * 0.005) * gate.astype(np.float32)
        present[:, :, 1:] = (present[:, :, 1:] * (1 - strength[:, :, None])
                             + neighbor[:, :, 1:] * strength[:, :, None])
        return cv2.cvtColor(np.clip(present, 0, 255).astype(np.uint8), cv2.COLOR_YCrCb2BGR)

    if signalart == "digital":
        now_gray = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY).astype(np.float32)
        old_gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY).astype(np.float32)
        height, width = now_gray.shape
        block_height, block_width = height // 8, width // 8
        if not block_height or not block_width:
            return current.copy()
        usable = (slice(0, block_height * 8), slice(0, block_width * 8))
        difference = np.abs(now_gray[usable] - old_gray[usable]).reshape(
            block_height, 8, block_width, 8).mean(axis=(1, 3))
        now_edges = np.abs(cv2.Laplacian(now_gray, cv2.CV_32F))[usable].reshape(
            block_height, 8, block_width, 8).mean(axis=(1, 3))
        old_edges = np.abs(cv2.Laplacian(old_gray, cv2.CV_32F))[usable].reshape(
            block_height, 8, block_width, 8).mean(axis=(1, 3))
        now_variance = now_gray[usable].reshape(block_height, 8, block_width, 8).var(axis=(1, 3))
        old_variance = old_gray[usable].reshape(block_height, 8, block_width, 8).var(axis=(1, 3))
        threshold = max(18.0, 37.0 - amount * 0.16 + fingerprint.blocknaht * 0.8)
        unusual_texture = (now_edges > old_edges * 1.7 + 12) | (
            now_variance < old_variance * 0.22
        )
        damaged = (difference > threshold) & unusual_texture
        mask = np.zeros((height, width), np.float32)
        mask[usable] = np.repeat(np.repeat(damaged, 8, axis=0), 8, axis=1)
        # Flight OSD commonly occupies the outer bands. Keep those pixels intact.
        guard = max(1, height // 12)
        mask[:guard] = 0
        mask[-guard:] = 0
        mask = cv2.GaussianBlur(mask, (5, 5), 0.8)[:, :, None]
        return np.clip(current.astype(np.float32) * (1 - mask)
                       + aligned.astype(np.float32) * mask, 0, 255).astype(np.uint8)
    raise ValueError("Unknown signal family")


def _model_stage(frames: Iterable[np.ndarray], recipe: Arbeitsrezept,
                 fingerprint: Signalsignatur | NeuralProfile) -> Iterator[np.ndarray]:
    """Apply a trained profile; motion models look two frames back and ahead."""
    future: deque[np.ndarray] = deque()
    past: deque[np.ndarray] = deque(maxlen=2)
    source = iter(frames)

    def fill_window() -> None:
        while len(future) < 3:
            frame = next(source, None)
            if frame is None:
                break
            future.append(frame)

    fill_window()
    while future:
        current = future.popleft()
        previous = past[-1] if past else None
        if isinstance(fingerprint, NeuralProfile) and fingerprint.architecture == "motion5":
            left_one = past[-1] if past else current
            left_two = past[-2] if len(past) > 1 else left_one
            right_one = future[0] if future else current
            right_two = future[1] if len(future) > 1 else right_one
            yield fingerprint.improve(current, (left_two, left_one, right_one, right_two))
        elif isinstance(fingerprint, NeuralProfile):
            yield fingerprint.improve(current)
        elif recipe.signalart == "analog":
            # The analog chroma repair already ran; the older fingerprint path
            # would only compare against an unrepaired previous frame.
            yield current
        else:
            yield restore_frame(current, previous, recipe.signalart, recipe.blockpflege, fingerprint)
        past.append(current)
        fill_window()


def _fused(recipe: Arbeitsrezept) -> bool:
    """Whether the multi-frame fusion stage runs (noise reduction or a new size)."""
    return recipe.vergroesserung_prozent != 100 or recipe.rauschminderung > 0


def _stages(frames: Iterable[np.ndarray], recipe: Arbeitsrezept,
            fingerprint: Signalsignatur | NeuralProfile | None,
            osd: np.ndarray | None) -> Iterator[np.ndarray]:
    """Chain the frame stages in their fixed order: colour, model, OSD, fusion."""
    stream: Iterable[np.ndarray] = frames
    if recipe.signalart == "analog" and recipe.farbberuhigung > 0:
        stream = repair_stream(stream, recipe.farbberuhigung, osd)
    if fingerprint is not None:
        stream = _model_stage(stream, recipe, fingerprint)
    if recipe.osd_entfernen and osd is not None:
        stream = remove_stream(stream, osd)
    if _fused(recipe):
        stream = fuse_stream(stream, recipe.vergroesserung_prozent, recipe.rauschminderung / 100,
                             recipe.kantendetail / 50)
    return iter(stream)


def clip_mask(source: Path, recipe: Arbeitsrezept, info: VideoKenndaten,
               duration: float) -> np.ndarray | None:
    """OSD mask for the cut: required for OSD removal, optional protection otherwise."""
    if recipe.osd_entfernen:
        return detect_osd(source, recipe.schnittbeginn, duration, info.width, info.height)
    if recipe.signalart == "analog" and recipe.farbberuhigung > 0:
        try:
            return detect_osd(source, recipe.schnittbeginn, duration, info.width, info.height)
        except ValueError:
            return None  # Very short cuts: repair without OSD protection.
    return None


def preview_frame(source: Path, seconds: float, recipe: Arbeitsrezept, info: VideoKenndaten,
                  fingerprint: Signalsignatur | NeuralProfile | None,
                  osd: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Original and processed frame at ``seconds``, run through the full stage chain.

    A short run of frames around the moment is decoded so that every temporal
    stage sees real neighbours; ``osd`` can carry a mask detected earlier.
    """
    recipe.validate()
    if osd is None:
        osd = clip_mask(source, recipe, info, output_duration(info, recipe))
    lead, span = 0.25, 0.5
    start = max(0.0, seconds - lead)
    preroll = min(3.0, start)
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "quiet", "-ss", str(start - preroll),
            "-i", str(source), "-ss", str(preroll), "-t", str(span), "-map", "0:v:0",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    raw = subprocess.run(args, capture_output=True, timeout=120, creationflags=PROCESS_FLAGS).stdout
    size = info.width * info.height * 3
    frames = [np.frombuffer(raw[index:index + size], np.uint8).reshape(info.height, info.width, 3)
              for index in range(0, len(raw) - size + 1, size)]
    if not frames:
        raise ValueError("Could not decode frames at this moment")
    middle = min(len(frames) - 1, round((seconds - start) / span * len(frames)))
    # Stages are lazy: stop as soon as the frame of interest has come through.
    processed = next(islice(_stages(frames, recipe, fingerprint, osd), middle, None))
    return frames[middle], processed


def process_temporal(source: Path, target: Path, recipe: Arbeitsrezept, info: VideoKenndaten,
                     fingerprint: Signalsignatur | NeuralProfile | None,
                     progress: Callable[[float], None],
                     cancel: threading.Event) -> Laufergebnis:
    """Frame-by-frame export: analog repair, model, OSD removal, upscaling."""
    recipe.validate()
    if fingerprint is not None and fingerprint.signalart != recipe.signalart:
        raise ValueError("Signal model does not match the selected processing mode")
    duration = output_duration(info, recipe)
    source = source.resolve()
    target = target.resolve()
    if source == target:
        raise ValueError("Source and destination must differ")
    analog = recipe.signalart == "analog" and recipe.farbberuhigung > 0
    osd = clip_mask(source, recipe, info, duration)
    upscaled = recipe.vergroesserung_prozent != 100
    # Fusion applies detail and size itself; FFmpeg must not do it again.
    filters = video_filters(recipe, restored=fingerprint is not None or analog, upscaled=_fused(recipe))
    options = encoding_options(recipe, info, duration)
    fps = info.fps
    if not fps:
        capture = cv2.VideoCapture(str(source))
        fps = capture.get(cv2.CAP_PROP_FPS) if capture.isOpened() else 0.0
        capture.release()
    if fps <= 0 or fps > 240:
        raise ValueError("Could not read video frame rate")
    width, height = info.width, info.height
    out_width, out_height = (target_size(width, height, recipe.vergroesserung_prozent)
                             if upscaled else (width, height))
    frame_limit = max(1, round(duration * fps))
    # FFmpeg's timestamp seek is reliable for damaged transport streams; OpenCV's
    # CAP_PROP_POS_MSEC can land several frames before the requested cut.
    decoder_args = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error"]
    if recipe.schnittbeginn:
        decoder_args.extend(["-ss", str(recipe.schnittbeginn)])
    decoder_args.extend(["-i", str(source), "-t", str(duration), "-map", "0:v:0",
                         "-an", "-sn", "-r", f"{fps:.6f}", "-f", "rawvideo",
                         "-pix_fmt", "bgr24", "pipe:1"])
    ffmpeg_args = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
                   "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{out_width}x{out_height}",
                   "-r", f"{fps:.6f}", "-i", "pipe:0"]
    if info.has_audio:
        if recipe.schnittbeginn:
            ffmpeg_args.extend(["-ss", str(recipe.schnittbeginn)])
        ffmpeg_args.extend(["-i", str(source)])
    ffmpeg_args.extend(["-map", "0:v:0"])
    if info.has_audio:
        ffmpeg_args.extend(["-map", "1:a?"])
    ffmpeg_args.extend(["-t", str(duration), "-vf", filters])
    ffmpeg_args.extend(options)
    workspace = None
    try:
        workspace = caption_workspace(recipe)
        pending = temporary_output(target)
    except Exception:
        if workspace:
            workspace.cleanup()
        raise
    ffmpeg_args.extend(["-shortest", str(pending)])
    log = tempfile.TemporaryFile(mode="w+b")
    decoder_log = tempfile.TemporaryFile(mode="w+b")
    workplace = Path(workspace.name) if workspace else Path(__file__).parent / "assets"
    decoder = None
    encoder = None
    try:
        decoder = subprocess.Popen(decoder_args, stdout=subprocess.PIPE,
                                   stderr=decoder_log, creationflags=PROCESS_FLAGS)
        encoder = subprocess.Popen(ffmpeg_args, stdin=subprocess.PIPE, stderr=log,
                                   cwd=workplace, creationflags=PROCESS_FLAGS)
        sent = 0
        frame_bytes = width * height * 3
        assert decoder.stdout is not None
        assert encoder.stdin is not None
        def decoded() -> Iterator[np.ndarray]:
            for _ in range(frame_limit):
                raw = decoder.stdout.read(frame_bytes)
                if len(raw) != frame_bytes or cancel.is_set():
                    return
                yield np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)

        for enhanced in _stages(decoded(), recipe, fingerprint, osd):
            if cancel.is_set() or sent >= frame_limit:
                break
            encoder.stdin.write(enhanced.tobytes())
            sent += 1
            progress(min(1.0, sent / frame_limit))
        encoder.stdin.close()
        decoder.stdout.close()
        if decoder.poll() is None:
            decoder.terminate()
        decoder.wait(timeout=30)
        encoder.wait(timeout=120)
        if cancel.is_set():
            return Laufergebnis(source, target, False, "Canceled")
        if encoder.returncode or sent == 0:
            message = diagnostic_tail(log) or diagnostic_tail(decoder_log) or "Temporal export failed"
            return Laufergebnis(source, target, False, message)
        pending.replace(target)
        progress(1.0)
        return Laufergebnis(source, target, True, "Done")
    except Exception:
        for proc in (decoder, encoder):
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()
        raise
    finally:
        pending.unlink(missing_ok=True)
        if decoder is not None and decoder.stdout:
            decoder.stdout.close()
        if encoder is not None and encoder.stdin and not encoder.stdin.closed:
            encoder.stdin.close()
        log.close()
        decoder_log.close()
        if workspace:
            workspace.cleanup()
