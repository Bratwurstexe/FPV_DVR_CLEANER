from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Callable

from .media import PROCESS_FLAGS, VideoKenndaten
from .learning import Signalsignatur
from .recipe import Arbeitsrezept


# x264 speed per processing effort. Slower presets mostly save file size at
# the same quality setting (CRF); the picture barely changes.
ENCODER_PRESETS = {"fast": "veryfast", "balanced": "faster", "best": "medium"}


def _font_file() -> str:
    return "Inter.ttf"


def caption_workspace(plan: Arbeitsrezept) -> tempfile.TemporaryDirectory | None:
    if not plan.untertiteltext:
        return None
    workspace = tempfile.TemporaryDirectory(prefix="fpv-caption-")
    target_dir = Path(workspace.name)
    shutil.copyfile(Path(__file__).parent / "assets" / "Inter.ttf", target_dir / "Inter.ttf")
    (target_dir / "caption.txt").write_text(plan.untertiteltext, encoding="utf-8")
    return workspace


def video_filters(recipe: Arbeitsrezept, *, preview: bool = False,
                  fingerprint: Signalsignatur | None = None,
                  restored: bool = False, upscaled: bool = False) -> str:
    recipe.validate()
    chain: list[str] = []
    if recipe.signalart == "analog" and recipe.farbberuhigung and not restored:
        learned = min(2.0, max(0.5, fingerprint.farbrauschen / 3)) if fingerprint and fingerprint.signalart == "analog" else 1.0
        strength = recipe.farbberuhigung / 25 * learned
        chain.append(f"hqdn3d=0:{strength:.2f}:0:{strength * 1.5:.2f}")
    if recipe.signalart == "digital" and recipe.blockpflege and not restored:
        learned = min(0.04, fingerprint.blocknaht / 150) if fingerprint and fingerprint.signalart == "digital" else 0.0
        threshold = 0.04 + recipe.blockpflege * 0.0012 + learned
        chain.append(f"deblock=filter=weak:block=8:alpha={threshold:.3f}:beta={threshold / 2:.3f}:gamma={threshold / 2:.3f}:delta={threshold / 2:.3f}")
    if recipe.kantendetail and not upscaled:
        chain.append(f"unsharp=5:5:{recipe.kantendetail / 100:.2f}:5:5:0")
    if recipe.bewegungsspur and not preview:
        weight = recipe.bewegungsspur / 500
        chain.append(f"tmix=frames=2:weights='{1 - weight:.3f} {weight:.3f}'")
    if recipe.vergroesserung_prozent != 100 and not preview and not upscaled:
        chain.append(f"scale=iw*{recipe.vergroesserung_prozent}/100:ih*{recipe.vergroesserung_prozent}/100:flags=lanczos")
    if recipe.untertiteltext and not preview:
        chain.append("drawtext=fontfile=" + _font_file() +
                     ":textfile=caption.txt:expansion=none:fontcolor=white:fontsize=h/30:box=1:boxcolor=black@0.55:boxborderw=8:x=(w-text_w)/2:y=h-text_h-h/20")
    chain.append("format=yuv420p" if not preview else "format=rgb24")
    return ",".join(chain)


def output_duration(info: VideoKenndaten, recipe: Arbeitsrezept) -> float:
    end = min(info.duration, recipe.schnittende) if recipe.schnittende else info.duration
    duration = end - recipe.schnittbeginn
    if duration <= 0:
        raise ValueError("The selected cut contains no frames")
    return duration


def command(source: Path, target: Path, recipe: Arbeitsrezept, info: VideoKenndaten,
            fingerprint: Signalsignatur | None = None) -> list[str]:
    recipe.validate()
    source = source.resolve()
    target = target.resolve()
    if source == target:
        raise ValueError("Source and destination must differ")
    duration = output_duration(info, recipe)
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-loglevel", "error"]
    if recipe.schnittbeginn:
        args.extend(["-ss", str(recipe.schnittbeginn)])
    args.extend(["-i", str(source), "-t", str(duration), "-map", "0:v:0", "-map", "0:a?",
                 "-vf", video_filters(recipe, fingerprint=fingerprint)])
    args.extend(encoding_options(recipe, info, duration))
    args.append(str(target))
    return args


def encoding_options(recipe: Arbeitsrezept, info: VideoKenndaten, duration: float) -> list[str]:
    args = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if recipe.dateiziel_mb:
        audio_bps = 128_000 if info.has_audio else 0
        video_bps = int((recipe.dateiziel_mb * 8_000_000 * 0.94) / duration) - audio_bps
        if video_bps < 100_000:
            raise ValueError("Size cap is too small for this clip")
        args.extend(["-b:v", str(video_bps), "-maxrate", str(video_bps), "-bufsize", str(video_bps * 2)])
    else:
        args.extend(["-crf", str(recipe.kompressionswert)])
    args.extend(["-preset", ENCODER_PRESETS[recipe.aufwand], "-c:a", "aac", "-b:a", "128k"])
    return args


@dataclass
class Laufergebnis:
    source: Path
    target: Path
    success: bool
    message: str


def temporary_output(target: Path) -> Path:
    """Reserve a same-directory output so a failed run cannot replace a good file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{target.stem}-", suffix=target.suffix,
                                     dir=target.parent, delete=False) as handle:
        return Path(handle.name)


def diagnostic_tail(log) -> str:
    """Read only the last few bytes of FFmpeg diagnostics."""
    log.seek(0, 2)
    log.seek(max(0, log.tell() - 4096))
    return log.read().decode("utf-8", errors="replace").strip()[-800:]


def process_one(source: Path, target: Path, recipe: Arbeitsrezept, info: VideoKenndaten,
                progress: Callable[[float], None], cancel: threading.Event,
                fingerprint: Signalsignatur | None = None) -> Laufergebnis:
    total = output_duration(info, recipe)
    args = command(source, target, recipe, info, fingerprint)
    workspace = caption_workspace(recipe)
    try:
        pending = temporary_output(target)
    except Exception:
        if workspace:
            workspace.cleanup()
        raise
    args[-1] = str(pending)
    workplace = Path(workspace.name) if workspace else Path(__file__).parent / "assets"
    log = tempfile.TemporaryFile(mode="w+b")
    try:
        proc = subprocess.Popen(args, cwd=workplace,
                                stdout=subprocess.PIPE, stderr=log,
                                text=True, encoding="utf-8", errors="replace",
                                creationflags=PROCESS_FLAGS)
    except Exception:
        pending.unlink(missing_ok=True)
        log.close()
        if workspace:
            workspace.cleanup()
        raise
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel.is_set():
                proc.terminate()
                break
            if line.startswith("out_time="):
                value = line.partition("=")[2].strip()
                match = re.fullmatch(r"(\d+):(\d+):(\d+(?:\.\d+)?)", value)
                if match:
                    seconds = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
                    progress(min(1.0, seconds / total))
        proc.wait(timeout=30)
        if cancel.is_set():
            return Laufergebnis(source, target, False, "Canceled")
        if proc.returncode:
            return Laufergebnis(source, target, False, diagnostic_tail(log) or "FFmpeg error")
        pending.replace(target)
        progress(1.0)
        return Laufergebnis(source, target, True, "Done")
    except Exception:
        proc.kill()
        proc.wait()
        raise
    finally:
        if proc.stdout:
            proc.stdout.close()
        pending.unlink(missing_ok=True)
        log.close()
        if workspace:
            workspace.cleanup()
