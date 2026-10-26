"""Frame-by-frame export path: analog chroma repair, HDZero breakups, trained
profiles, OSD removal, fusion and the look.

The methods use optical-flow alignment and conservative error gating. They do
not claim to reconstruct content absent from every neighboring frame.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
import subprocess
import tempfile
import threading
from typing import Callable, Iterable, Iterator

import cv2
import numpy as np

from .analog import repair_stream
from .digital import repair_stream as mend_breakups
from .learning import Signalsignatur
from .look import Look, look_frame, look_stream
from .neural import NeuralProfile
from .media import PROCESS_FLAGS, VideoKenndaten
from . import handmask
from .osd import detect_osd, remove_stream
from .processor import (Laufergebnis, caption_workspace, encoding_options,
                        diagnostic_tail, output_duration, temporary_output, video_filters)
from .recipe import Arbeitsrezept
from .upscale import fuse_stream, target_size


# OSD mask (0/255) and guide of steady edge orientations, see osd.find_osd. With
# hand corrections two more layers follow: always and never removed (handmask.merge).
Overlay = tuple[np.ndarray, ...]



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


@dataclass(frozen=True)
class _Effort:
    """How much work the frame stages spend: neighbours, flow detail, fill size."""

    colour_radius: int
    colour_flow: int
    osd_radius: int
    osd_flow: int
    osd_fill: float
    fusion_radius: int
    fusion_flow: int
    fusion_preset: int
    fusion_interpolation: int


EFFORTS = {
    "fast": _Effort(2, 240, 3, cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST, 0.5,
                    1, 320, cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST, cv2.INTER_LINEAR),
    "balanced": _Effort(3, 320, 4, cv2.DISOPTICAL_FLOW_PRESET_FAST, 0.5,
                        2, 400, cv2.DISOPTICAL_FLOW_PRESET_FAST, cv2.INTER_LINEAR),
    "best": _Effort(3, 320, 6, cv2.DISOPTICAL_FLOW_PRESET_FAST, 1.0,
                    2, 480, cv2.DISOPTICAL_FLOW_PRESET_MEDIUM, cv2.INTER_CUBIC),
}


def _stages(frames: Iterable[np.ndarray], recipe: Arbeitsrezept,
            fingerprint: Signalsignatur | NeuralProfile | None,
            osd: Overlay | None, first_index: int = 0, look: bool = True) -> Iterator[np.ndarray]:
    """Chain the frame stages in their fixed order: colour or breakups, model, OSD, fusion, look.

    ``first_index`` is the frame number of the first frame in the recording; the
    film grain of the look is seeded with it. ``look=False`` leaves the look out.
    """
    effort = EFFORTS[recipe.aufwand]
    stream: Iterable[np.ndarray] = frames
    if recipe.signalart == "analog" and recipe.farbberuhigung > 0:
        stream = repair_stream(stream, recipe.farbberuhigung, osd[0] if osd else None,
                               effort.colour_radius, effort.colour_flow)
    if recipe.signalart == "digital" and recipe.blockpflege > 0 and fingerprint is None:
        stream = mend_breakups(stream, recipe.blockpflege)
    if fingerprint is not None:
        stream = _model_stage(stream, recipe, fingerprint)
    if recipe.osd_entfernen and osd is not None:
        mask, guide, *manual = osd
        always, never = manual if manual else (None, None)
        stream = remove_stream(stream, mask, guide, effort.osd_radius, effort.osd_flow, effort.osd_fill,
                               always=always, never=never)
    if _fused(recipe):
        stream = fuse_stream(stream, recipe.vergroesserung_prozent, recipe.rauschminderung / 100,
                             recipe.kantendetail / 50, effort.fusion_radius, effort.fusion_flow,
                             effort.fusion_preset, effort.fusion_interpolation)
    finish = Look.of(recipe)
    if look and finish.active:
        stream = look_stream(stream, finish, first_index)
    return iter(stream)


def clip_mask(source: Path, recipe: Arbeitsrezept, info: VideoKenndaten,
               duration: float) -> Overlay | None:
    """OSD mask and guide for the cut: required for OSD removal, optional protection otherwise.

    Hand corrections from the mask editor (``recipe.osd_handmaske``) are merged in.
    """
    shape = (info.height, info.width)
    layers = handmask.read(recipe.osd_handmaske, shape)
    if recipe.osd_entfernen:
        try:
            found = detect_osd(source, recipe.schnittbeginn, duration, info.width, info.height)
        except ValueError:
            # A cut too short for the search can still have its OSD painted by hand.
            if layers is None or not layers[0].any():
                raise
            found = None
        return handmask.merge(found, layers, shape)
    if recipe.signalart == "analog" and recipe.farbberuhigung > 0:
        try:
            found = detect_osd(source, recipe.schnittbeginn, duration, info.width, info.height)
        except ValueError:
            found = None  # Very short cuts: repair without OSD protection.
        return handmask.merge(found, layers, shape)
    return None


def _crop(osd: Overlay, box: tuple[int, int, int, int]) -> Overlay:
    """Every layer of the overlay cut to the picture inside the bars."""
    x, y, width, height = box
    return tuple(np.ascontiguousarray(layer[y:y + height, x:x + width]) for layer in osd)


BAR_LEVEL = 24
BAR_SAMPLES = 8


def picture_box(source: Path, start: float, duration: float,
                width: int, height: int) -> tuple[int, int, int, int]:
    """(x, y, width, height) of the picture inside black side or top bars.

    Goggles record a 4:3 picture in a 16:9 file. The bars are no picture and
    need no repair, so the stages skip them: a quarter less work. A side counts
    as bars only when it stays dark in every sampled frame and adds up to at
    least 5 % of the frame; otherwise the whole frame is returned.
    """
    capture = cv2.VideoCapture(str(source))
    brightest = None
    try:
        for index in range(BAR_SAMPLES):
            capture.set(cv2.CAP_PROP_POS_MSEC, (start + duration * (index + 0.5) / BAR_SAMPLES) * 1000)
            okay, frame = capture.read()
            if not okay or frame.shape[:2] != (height, width):
                continue
            luma = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            brightest = luma if brightest is None else np.maximum(brightest, luma)
    finally:
        capture.release()
    if brightest is None:
        return 0, 0, width, height

    def span(profile: np.ndarray, size: int) -> tuple[int, int]:
        lit = np.flatnonzero(profile > BAR_LEVEL)
        if not len(lit):
            return 0, size
        first, last = int(lit[0]), int(lit[-1]) + 1
        if first + size - last < size * 0.05:
            return 0, size
        # Even edges keep the 4:2:0 chroma of the output aligned.
        first, last = first - first % 2, min(size, last + last % 2)
        return first, last - first

    # A high percentile instead of the maximum ignores single noisy pixels.
    x, inner_width = span(np.percentile(brightest, 90, axis=0), width)
    y, inner_height = span(np.percentile(brightest, 90, axis=1), height)
    return x, y, inner_width, inner_height


def _placement(box: tuple[int, int, int, int], width: int, height: int,
               percent: int) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    """Picture size, frame size and picture position in the output frame."""
    x, y, inner_width, inner_height = box
    full = target_size(width, height, percent) if percent != 100 else (width, height)
    inner = target_size(inner_width, inner_height, percent) if percent != 100 else (inner_width, inner_height)
    offset = (min(full[0] - inner[0], round(x * full[0] / width / 2) * 2),
              min(full[1] - inner[1], round(y * full[1] / height / 2) * 2))
    return inner, full, offset


def preview_frame(source: Path, seconds: float, recipe: Arbeitsrezept, info: VideoKenndaten,
                  fingerprint: Signalsignatur | NeuralProfile | None,
                  osd: Overlay | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Original and processed frame at ``seconds``, run through the full stage chain."""
    before, _, after = preview_frames(source, seconds, recipe, info, fingerprint, osd)
    return before, after


def preview_frames(source: Path, seconds: float, recipe: Arbeitsrezept, info: VideoKenndaten,
                   fingerprint: Signalsignatur | NeuralProfile | None,
                   osd: Overlay | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Original, repaired without the look, and fully processed frame at ``seconds``.

    A short run of frames around the moment is decoded so that every temporal
    stage sees real neighbours; ``osd`` can carry a mask detected earlier.
    Without a look the last two are the same picture.
    """
    window = PreviewWindow.decode(source, seconds, recipe, info, osd)
    plain, finished = window.render(recipe, fingerprint)
    return window.frames[window.middle], window.placed(plain, recipe), window.placed(finished, recipe)


def encoder_detail(picture: np.ndarray, recipe: Arbeitsrezept) -> np.ndarray:
    """The edge detail the encoder adds when no fusion stage does (FFmpeg unsharp 5x5 on luma)."""
    amount = recipe.kantendetail / 100
    if not amount or _fused(recipe):
        return picture
    planes = cv2.cvtColor(picture, cv2.COLOR_BGR2YCrCb)
    luma = planes[:, :, 0].astype(np.float32)
    planes[:, :, 0] = np.clip(luma + amount * (luma - cv2.GaussianBlur(luma, (5, 5), 0)), 0, 255).astype(np.uint8)
    return cv2.cvtColor(planes, cv2.COLOR_YCrCb2BGR)


@dataclass
class PreviewWindow:
    """Decoded frames around one moment, ready to be rendered with several recipes."""

    frames: list[np.ndarray]
    pictures: list[np.ndarray]
    middle: int
    start: float
    box: tuple[int, int, int, int]
    osd: Overlay | None
    info: VideoKenndaten

    @classmethod
    def decode(cls, source: Path, seconds: float, recipe: Arbeitsrezept, info: VideoKenndaten,
               osd: Overlay | None = None, lead: float = 0.25, span: float = 0.5) -> PreviewWindow:
        recipe.validate()
        duration = output_duration(info, recipe)
        if osd is None:
            osd = clip_mask(source, recipe, info, duration)
        box = picture_box(source, recipe.schnittbeginn, duration, info.width, info.height)
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
        x, y, inner_width, inner_height = box
        pictures = [np.ascontiguousarray(frame[y:y + inner_height, x:x + inner_width]) for frame in frames]
        return cls(frames, pictures, middle, start, box, _crop(osd, box) if osd is not None else None, info)

    def render(self, recipe: Arbeitsrezept, fingerprint: Signalsignatur | NeuralProfile | None = None,
               osd: Overlay | None = None) -> tuple[np.ndarray, np.ndarray]:
        """The picture (inside the bars) repaired without and with the look."""
        recipe.validate()
        if fingerprint is not None and fingerprint.signalart != recipe.signalart:
            raise ValueError("Signal model does not match the selected processing mode")
        # Stages are lazy: stop as soon as the frame of interest has come through.
        # The look is the last stage and needs no neighbours, so it is applied to
        # that one frame only, with the frame number an export would give it.
        overlay = self.osd if osd is None else osd
        plain = next(islice(_stages(self.pictures, recipe, fingerprint, overlay, look=False), self.middle, None))
        finished = look_frame(plain, Look.of(recipe), round(self.start * self.info.fps) + self.middle)
        return encoder_detail(plain, recipe), encoder_detail(finished, recipe)

    def placed(self, picture: np.ndarray, recipe: Arbeitsrezept) -> np.ndarray:
        """The picture back in a full frame with black bars, at the output size."""
        _, (full_width, full_height), (left, top) = _placement(self.box, self.info.width, self.info.height,
                                                               recipe.vergroesserung_prozent)
        result = np.zeros((full_height, full_width, 3), np.uint8)
        result[top:top + picture.shape[0], left:left + picture.shape[1]] = picture
        return result


# Seconds of footage decoded before and after a cut as repair context.
CONTEXT_SECONDS = 0.5


def _decoder(source: Path, start: float, seconds: float, fps: float,
             box: tuple[int, int, int, int]) -> list[str]:
    # FFmpeg's timestamp seek is reliable for damaged transport streams; OpenCV's
    # CAP_PROP_POS_MSEC can land several frames before the requested cut.
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error"]
    if start > 0:
        args.extend(["-ss", f"{start:.6f}"])
    args.extend(["-i", str(source), "-t", f"{seconds:.6f}", "-map", "0:v:0", "-an", "-sn",
                 "-vf", "crop={2}:{3}:{0}:{1}".format(*box), "-r", f"{fps:.6f}",
                 "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"])
    return args


def _encoder(size: tuple[int, int], fps: float) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{size[0]}x{size[1]}",
            "-r", f"{fps:.6f}", "-i", "pipe:0"]


def _pump(decoder_args: list[str], encoder_args: list[str], workplace: str | None, size: tuple[int, int],
          frame_limit: int, recipe: Arbeitsrezept,
          fingerprint: Signalsignatur | NeuralProfile | None, osd: Overlay | None,
          cancel: threading.Event, report: Callable[[int], None],
          skip: int = 0, decode_limit: int | None = None, first_index: int = 0) -> str:
    """Decode frames, run the stages and encode ``frame_limit`` of them.

    ``skip`` frames before the cut and the frames after it (up to
    ``decode_limit`` decoded in all) give the first and last frames of the cut
    real neighbours to be repaired from; they are not written out.
    ``first_index`` is the frame number of the first decoded frame.
    Returns an error message, or "" on success.
    """
    width, height = size
    log = tempfile.TemporaryFile(mode="w+b")
    decoder_log = tempfile.TemporaryFile(mode="w+b")
    decoder = None
    encoder = None
    try:
        decoder = subprocess.Popen(decoder_args, stdout=subprocess.PIPE,
                                   stderr=decoder_log, creationflags=PROCESS_FLAGS)
        encoder = subprocess.Popen(encoder_args, stdin=subprocess.PIPE, stderr=log,
                                   cwd=workplace, creationflags=PROCESS_FLAGS)
        sent = 0
        frame_bytes = width * height * 3
        assert decoder.stdout is not None
        assert encoder.stdin is not None

        def decoded() -> Iterator[np.ndarray]:
            for _ in range(decode_limit or frame_limit + skip):
                raw = decoder.stdout.read(frame_bytes)
                if len(raw) != frame_bytes or cancel.is_set():
                    return
                yield np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)

        passed = 0
        for enhanced in _stages(decoded(), recipe, fingerprint, osd, first_index):
            if cancel.is_set() or sent >= frame_limit:
                break
            if passed < skip:
                passed += 1
                continue
            try:
                encoder.stdin.write(enhanced.tobytes())
            except (BrokenPipeError, OSError):
                # The encoder stopped early: its own message says why.
                encoder.wait(timeout=120)
                return diagnostic_tail(log) or "The video encoder stopped unexpectedly"
            sent += 1
            report(sent)
        encoder.stdin.close()
        decoder.stdout.close()
        if decoder.poll() is None:
            decoder.terminate()
        decoder.wait(timeout=30)
        encoder.wait(timeout=120)
        if encoder.returncode or sent == 0:
            return diagnostic_tail(log) or diagnostic_tail(decoder_log) or "Temporal export failed"
        return ""
    except Exception:
        for proc in (decoder, encoder):
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()
        raise
    finally:
        if decoder is not None and decoder.stdout:
            decoder.stdout.close()
        if encoder is not None and encoder.stdin and not encoder.stdin.closed:
            encoder.stdin.close()
        log.close()
        decoder_log.close()


def process_temporal(source: Path, target: Path, recipe: Arbeitsrezept, info: VideoKenndaten,
                     fingerprint: Signalsignatur | NeuralProfile | None,
                     progress: Callable[[float], None],
                     cancel: threading.Event) -> Laufergebnis:
    """Frame-by-frame export: analog or HDZero repair, model, OSD removal, upscaling, look."""
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
    box = picture_box(source, recipe.schnittbeginn, duration, info.width, info.height)
    x, y, width, height = box
    if osd is not None:
        osd = _crop(osd, box)
    out_size, (full_width, full_height), (left, top) = _placement(box, info.width, info.height,
                                                                  recipe.vergroesserung_prozent)
    if out_size != (full_width, full_height):
        # The stages see the picture only; the bars come back as clean black.
        filters = f"pad={full_width}:{full_height}:{left}:{top}:black," + filters
    frame_limit = max(1, round(duration * fps))
    # Context around the cut: the repair of the first and last frames can then
    # draw on neighbours that lie just outside the kept part.
    lead = min(recipe.schnittbeginn, CONTEXT_SECONDS)
    tail = max(0.0, min(CONTEXT_SECONDS, info.duration - (recipe.schnittbeginn + duration)))
    lead_frames = round(lead * fps)
    decode_limit = lead_frames + frame_limit + round(tail * fps)
    workspace = None
    try:
        workspace = caption_workspace(recipe)
        pending = temporary_output(target)
    except Exception:
        if workspace:
            workspace.cleanup()
        raise
    # Only the caption filter needs a working directory (relative font and text paths).
    workplace = workspace.name if workspace else None
    try:
        encoder_args = _encoder(out_size, fps)
        if info.has_audio:
            if recipe.schnittbeginn:
                encoder_args.extend(["-ss", str(recipe.schnittbeginn)])
            encoder_args.extend(["-i", str(source)])
        encoder_args.extend(["-map", "0:v:0"])
        if info.has_audio:
            encoder_args.extend(["-map", "1:a?"])
        encoder_args.extend(["-t", str(duration), "-vf", filters])
        encoder_args.extend(options)
        if info.has_audio:
            # Recordings often carry a little less sound than picture. -shortest stopped the
            # encoder when the sound ended, while frames were still coming ("Broken pipe");
            # instead the sound is padded with silence and -t alone ends the file.
            encoder_args.extend(["-af", "apad"])
        encoder_args.append(str(pending))
        message = _pump(_decoder(source, recipe.schnittbeginn - lead_frames / fps, lead + duration + tail, fps, box),
                        encoder_args, workplace, (width, height), frame_limit, recipe, fingerprint, osd, cancel,
                        lambda sent: progress(min(1.0, sent / frame_limit)),
                        skip=lead_frames, decode_limit=decode_limit,
                        first_index=max(0, round(recipe.schnittbeginn * fps) - lead_frames))
        if cancel.is_set():
            return Laufergebnis(source, target, False, "Canceled")
        if message:
            return Laufergebnis(source, target, False, message)
        pending.replace(target)
        progress(1.0)
        return Laufergebnis(source, target, True, "Done")
    finally:
        pending.unlink(missing_ok=True)
        if workspace:
            workspace.cleanup()
