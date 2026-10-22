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
from .neural import NeuralProfile, train_network
from .osd import detect_osd, preview
from .processor import process_one
from .recipe import Arbeitsrezept
from .restoration import clip_mask, preview_frame, process_temporal


def cached_mask(source: Path, recipe: Arbeitsrezept, info) -> np.ndarray | None:
    """OSD mask of a cut, kept in the temp folder so repeated previews stay quick."""
    stamp = f"{source.resolve()}|{source.stat().st_mtime_ns}|{recipe.schnittbeginn}|{recipe.schnittende}"
    store = Path(tempfile.gettempdir()) / f"flugbild-osd-{hashlib.sha1(stamp.encode()).hexdigest()[:16]}.png"
    if store.exists():
        mask = cv2.imread(str(store), cv2.IMREAD_GRAYSCALE)
        if mask is not None and mask.shape == (info.height, info.width):
            return mask
    duration = (min(info.duration, recipe.schnittende) if recipe.schnittende else info.duration)         - recipe.schnittbeginn
    probe = Arbeitsrezept(signalart=recipe.signalart, osd_entfernen=True,
                          schnittbeginn=recipe.schnittbeginn, schnittende=recipe.schnittende)
    try:
        mask = clip_mask(source, probe, info, duration)
    except ValueError:
        return None
    cv2.imwrite(str(store), mask)
    return mask


def emit(kind: str, **fields: object) -> None:
    print(json.dumps({"kind": kind, **fields}, ensure_ascii=True), flush=True)


def run(settings: dict) -> None:
    action = settings["action"]
    if action == "inspect":
        info = inspect_video(Path(settings["source"]))
        emit("result", duration=info.duration, width=info.width,
             height=info.height, audio=info.has_audio)
    elif action == "frame":
        still_frame(Path(settings["source"]), float(settings.get("seconds", 0)),
                    Path(settings["target"]))
        emit("result", target=settings["target"])
    elif action == "osd":
        source, target = Path(settings["source"]), Path(settings["target"])
        info = inspect_video(source)
        start = float(settings.get("start", 0))
        end = float(settings.get("end") or info.duration)
        mask = cached_mask(source, Arbeitsrezept(schnittbeginn=start, schnittende=end
                                                  if end < info.duration else 0.0), info)
        if mask is None:
            raise ValueError("The cut is too short to find the OSD")
        still_frame(source, float(settings.get("seconds", start)), target)
        frame = cv2.imread(str(target))
        if frame is None or frame.shape[:2] != mask.shape:
            raise ValueError("Could not read the preview frame")
        cv2.imwrite(str(target), preview(frame, mask))
        emit("result", target=str(target), coverage=float((mask > 0).mean() * 100),
             found=bool(np.any(mask)))
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
        source = Path(settings["source"])
        recipe = Arbeitsrezept(**settings["recipe"])
        info = inspect_video(source)
        profile = (NeuralProfile.load(Path(settings["model"]))
                   if settings.get("mode") == "model" else None)
        osd = cached_mask(source, recipe, info) if (recipe.osd_entfernen or recipe.signalart == "analog") else None
        before, after = preview_frame(source, float(settings["seconds"]), recipe, info, profile, osd)
        cv2.imwrite(settings["before"], before)
        cv2.imwrite(settings["after"], after)
        emit("result", before=settings["before"], after=settings["after"])
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
    elif action == "export":
        source, target = Path(settings["source"]), Path(settings["target"])
        recipe = Arbeitsrezept(**settings["recipe"])
        info = inspect_video(source)
        mode = settings["mode"]
        report = lambda ratio: emit("progress", fraction=ratio)
        if mode == "manual" and (recipe.signalart == "analog" or recipe.osd_entfernen
                                 or recipe.vergroesserung_prozent != 100 or recipe.rauschminderung > 0):
            # Colour repair, OSD removal, noise reduction and upscaling work frame by frame.
            result = process_temporal(source, target, recipe, info, None,
                                      report, threading.Event())
        elif mode == "manual":
            result = process_one(source, target, recipe, info, report, threading.Event())
        elif mode == "model":
            profile = NeuralProfile.load(Path(settings["model"]))
            result = process_temporal(source, target, recipe, info, profile,
                                      report, threading.Event())
        else:
            raise ValueError("Choose Manual or Trained model")
        if not result.success:
            raise RuntimeError(result.message)
        emit("result", target=str(target))
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
