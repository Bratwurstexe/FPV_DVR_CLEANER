"""Small JSON-line boundary between the desktop shell and video engine."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import sys
import threading
from pathlib import Path

import cv2
import numpy as np

from .media import PROCESS_FLAGS, inspect_video, still_frame
from .look import look_active
from .neural import NeuralProfile, train_network
from .osd import detect_osd, preview
from .processor import process_one
from .recipe import DATEIFORMATE, Arbeitsrezept
from . import handmask
from .restoration import PreviewWindow, clip_mask, picture_box, process_temporal
from .segments import audio_outline, concat_files, export_segments, normalize_segments
from .share import Teilauftrag, make_share
from .assess import assess, sample_frames


def cached_mask(source: Path, recipe: Arbeitsrezept, info) -> tuple[np.ndarray, ...] | None:
    """OSD mask and guide of a cut, with the hand corrections of the mask editor merged in.

    The automatic search is kept in the temp folder so repeated previews stay
    quick. Its key is the file and the cut only; the corrections are merged after
    loading, so a changed correction file can never meet a stale cached result.
    """
    layers = handmask.read(recipe.osd_handmaske, (info.height, info.width))
    found = _cached_search(source, recipe, info)
    if found is None and (layers is None or not layers[0].any()):
        return None
    return handmask.merge(found, layers, (info.height, info.width))


def _cached_search(source: Path, recipe: Arbeitsrezept, info) -> tuple[np.ndarray, np.ndarray] | None:
    """The automatic OSD search of a cut, from the temp folder when it was done before."""
    stamp = f"{source.resolve()}|{source.stat().st_mtime_ns}|{recipe.schnittbeginn}|{recipe.schnittende}"
    name = f"flugbild-osd-{hashlib.sha1(stamp.encode()).hexdigest()[:16]}"
    store = Path(tempfile.gettempdir()) / f"{name}.png"
    guide_store = Path(tempfile.gettempdir()) / f"{name}-guide.png"
    if store.exists() and guide_store.exists():
        mask = cv2.imread(str(store), cv2.IMREAD_GRAYSCALE)
        guide = cv2.imread(str(guide_store), cv2.IMREAD_GRAYSCALE)
        if (mask is not None and guide is not None
                and mask.shape == guide.shape == (info.height, info.width)):
            return mask, guide
    duration = (min(info.duration, recipe.schnittende) if recipe.schnittende else info.duration)         - recipe.schnittbeginn
    probe = Arbeitsrezept(signalart=recipe.signalart, osd_entfernen=True,
                          schnittbeginn=recipe.schnittbeginn, schnittende=recipe.schnittende)
    try:
        overlay = clip_mask(source, probe, info, duration)
    except ValueError:
        return None
    cv2.imwrite(str(store), overlay[0])
    cv2.imwrite(str(guide_store), overlay[1])
    return overlay


def render_cut(source: Path, target: Path, recipe: Arbeitsrezept, info, mode: str, profile, report):
    """One continuous cut through the pipeline the settings call for."""
    if mode == "model":
        return process_temporal(source, target, recipe, info, profile, report, threading.Event())
    if (recipe.signalart == "analog" or recipe.osd_entfernen or recipe.vergroesserung_prozent != 100
            or recipe.rauschminderung > 0 or (recipe.signalart == "digital" and recipe.blockpflege > 0)
            or look_active(recipe)):
        # Colour and breakup repair, OSD removal, noise reduction, upscaling and the look work
        # frame by frame.
        return process_temporal(source, target, recipe, info, None, report, threading.Event())
    return process_one(source, target, recipe, info, report, threading.Event())


def _matching(profile, recipe: Arbeitsrezept):
    """The trained model, if it was made for the signal of this recipe."""
    return profile if profile is not None and profile.signalart == recipe.signalart else None


def preview_action(settings: dict) -> None:
    """One frame through the whole chain.

    * ``before`` and ``after``: the original and the result (``plain``: the
      result without the look, if one is on).
    * ``reference_recipe``: ``before`` is rendered with that recipe instead of
      being the original, to show what one setting changes on its own.
    """
    source = Path(settings["source"])
    info = inspect_video(source)
    profile = (NeuralProfile.load(Path(settings["model"]))
               if settings.get("mode") == "model" else None)
    seconds = float(settings["seconds"])
    recipe = Arbeitsrezept(**settings["recipe"])
    osd = cached_mask(source, recipe, info) if (recipe.osd_entfernen or recipe.signalart == "analog") else None
    window = PreviewWindow.decode(source, seconds, recipe, info, osd)
    plain, finished = window.render(recipe, profile)
    if settings.get("reference_recipe"):
        reference = Arbeitsrezept(**settings["reference_recipe"])
        # The reference may switch a repair off; it still shares the decoded frames and the mask.
        _, other = window.render(reference, _matching(profile, reference))
        before = window.placed(other, reference)
    else:
        before = window.frames[window.middle]
    if settings.get("before"):
        cv2.imwrite(settings["before"], before)
    cv2.imwrite(settings["after"], window.placed(finished, recipe))
    # With a look, the repaired frame without it can be written too, to compare the two.
    if settings.get("plain") and look_active(recipe):
        cv2.imwrite(settings["plain"], window.placed(plain, recipe))
        emit("result", before=settings.get("before"), after=settings["after"], plain=settings["plain"])
    else:
        emit("result", before=settings.get("before"), after=settings["after"])


def emit(kind: str, **fields: object) -> None:
    print(json.dumps({"kind": kind, **fields}, ensure_ascii=True), flush=True)


def run(settings: dict) -> None:
    action = settings["action"]
    if action == "inspect":
        info = inspect_video(Path(settings["source"]))
        emit("result", duration=info.duration, width=info.width,
             height=info.height, audio=info.has_audio, fps=info.fps)
    elif action == "frame":
        crop = settings.get("crop")
        filters = "crop={2}:{3}:{0}:{1}".format(*[int(v) for v in crop]) if crop else ""
        still_frame(Path(settings["source"]), float(settings.get("seconds", 0)),
                    Path(settings["target"]), filters)
        emit("result", target=settings["target"])
    elif action == "picture":
        # The 4:3 picture inside the black bars of a 16:9 recording.
        source = Path(settings["source"])
        info = inspect_video(source)
        start = float(settings.get("start", 0))
        end = float(settings.get("end") or info.duration)
        x, y, width, height = picture_box(source, start, max(0.2, end - start), info.width, info.height)
        emit("result", x=x, y=y, width=width, height=height)
    elif action == "osd":
        source, target = Path(settings["source"]), Path(settings["target"])
        info = inspect_video(source)
        start = float(settings.get("start", 0))
        end = float(settings.get("end") or info.duration)
        cut = Arbeitsrezept(schnittbeginn=start, schnittende=end if end < info.duration else 0.0,
                            osd_handmaske=settings.get("manual_mask") or "")
        found = _cached_search(source, cut, info)
        if found is None:
            raise ValueError("The cut is too short to find the OSD")
        # Optional, for the mask editor: the automatic mask alone (0/255, full
        # frame) and the frame without marks.
        if settings.get("mask_target"):
            cv2.imwrite(str(settings["mask_target"]), found[0])
        mask = cached_mask(source, cut, info)[0]
        still_frame(source, float(settings.get("seconds", start)), target)
        frame = cv2.imread(str(target))
        if frame is None or frame.shape[:2] != mask.shape:
            raise ValueError("Could not read the preview frame")
        if settings.get("frame_target"):
            cv2.imwrite(str(settings["frame_target"]), frame)
        cv2.imwrite(str(target), preview(frame, mask))
        emit("result", target=str(target), coverage=float((mask > 0).mean() * 100),
             found=bool(np.any(mask)), automatic=float((found[0] > 0).mean() * 100))
    elif action == "filmstrip":
        source, folder = Path(settings["source"]), Path(settings["folder"])
        folder.mkdir(parents=True, exist_ok=True)
        info = inspect_video(source)
        interval = max(info.duration / int(settings.get("count", 120)), 0.25)
        args = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
        if info.duration >= 40:
            args.extend(["-skip_frame", "nokey"])  # Keyframes decode fast and suffice here.
        args.extend(["-i", str(source), "-map", "0:v:0",
                     "-vf", f"fps=1/{interval:.6f},scale=-2:{int(settings.get('height', 72))}",
                     "-q:v", "4", str(folder / "strip_%04d.jpg")])
        subprocess.run(args, capture_output=True, timeout=900, creationflags=PROCESS_FLAGS)
        pictures = sorted(folder.glob("strip_*.jpg"))
        emit("result", interval=interval,
             tiles=[{"seconds": round(index * interval, 3), "path": str(path)}
                    for index, path in enumerate(pictures)])
    elif action == "preview":
        preview_action(settings)
    elif action == "train":
        previous = (NeuralProfile.load(Path(settings["initial_model"]))
                    if settings.get("initial_model") else None)
        profile = train_network(Path(settings["folder"]), settings["signal"],
                                int(settings["seconds"]),
                                lambda samples, error: emit("progress", samples=samples,
                                                            error=error), threading.Event(), previous)
        destination = Path(settings["target"])
        profile.save(destination)
        emit("result", target=str(destination), samples=profile.samples,
             sources=profile.sources, validation_mae=profile.validation_mae,
             training_runs=profile.training_runs)
    elif action == "waveform":
        source = Path(settings["source"])
        info = inspect_video(source)
        emit("result", levels=audio_outline(source, info, int(settings.get("buckets", 1500))))
    elif action == "export":
        source, target = Path(settings["source"]), Path(settings["target"])
        recipe = Arbeitsrezept(**settings["recipe"])
        recipe.validate()
        # FFmpeg picks the container from the file name, so it must match the format.
        if target.suffix.lower() != DATEIFORMATE[recipe.dateiformat]:
            raise ValueError(f"The output file must end in {DATEIFORMATE[recipe.dateiformat]} for this format")
        info = inspect_video(source)
        mode = settings["mode"]
        if mode not in {"manual", "model"}:
            raise ValueError("Choose Manual or Trained model")
        profile = NeuralProfile.load(Path(settings["model"])) if mode == "model" else None
        report = lambda ratio: emit("progress", fraction=ratio)
        # Either the kept segments of the trim editor or the single cut of the recipe.
        segments = normalize_segments(settings.get("segments")
                                      or [[recipe.schnittbeginn, recipe.schnittende or info.duration]],
                                      info.duration)

        def render(part_source, part_target, part_recipe, part_report):
            return render_cut(part_source, part_target, part_recipe, info, mode, profile, part_report)

        result = export_segments(source, target, recipe, info, segments, render, report, threading.Event())
        if not result.success:
            raise RuntimeError(result.message)
        emit("result", target=str(target), segments=segments)
    elif action == "assess":
        original, cleaned = Path(settings["source"]), Path(settings["target"])
        info = inspect_video(original)
        made = inspect_video(cleaned)
        segments = normalize_segments(settings.get("segments"), info.duration)
        # Frames of the original are taken from the kept parts only.
        before = np.concatenate([sample_frames(original, info, start, end, max(3, 16 // len(segments)))
                                 for start, end in segments])
        after = sample_frames(cleaned, made, 0.0, made.duration)
        emit("result", **assess(before, after, int(settings.get("quality", 18)), settings.get("signal", "analog")))
    elif action == "share":
        # The cleaned clip brought into a format to post; fields of older requests are ignored.
        job = Teilauftrag.of(settings)
        outcome = make_share(Path(settings["target"]), Path(settings["output"]), job,
                             lambda ratio: emit("progress", fraction=ratio), threading.Event())
        if not outcome.result.success:
            raise RuntimeError(outcome.result.message)
        emit("result", target=settings["output"], duration=outcome.duration, width=outcome.size[0],
             height=outcome.size[1], fps=outcome.fps, sound=outcome.sound)
    elif action == "merge":
        parts = [Path(item) for item in settings["sources"]]
        if len(parts) < 2:
            raise ValueError("Choose at least two files to merge")
        shapes = {(inspect_video(part).width, inspect_video(part).height) for part in parts}
        if len(shapes) > 1:
            raise ValueError("The clips have different sizes and cannot be merged into one file")
        result = concat_files(parts, Path(settings["target"]))
        if not result.success:
            raise RuntimeError(result.message)
        emit("result", target=settings["target"])
    else:
        raise ValueError("Unknown desktop action")


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise ValueError("Expected one JSON settings file")
        settings = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
        if not isinstance(settings, dict):
            raise ValueError("Invalid desktop request")
        run(settings)
        return 0
    except Exception as exc:
        emit("error", message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
