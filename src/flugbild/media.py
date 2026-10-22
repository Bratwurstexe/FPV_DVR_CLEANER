from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess


PROCESS_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class VideoKenndaten:
    duration: float
    width: int
    height: int
    has_audio: bool
    fps: float = 0.0


def _rate(text: str | None) -> float:
    """Frame rate from an ffprobe fraction such as "50/1"; 0 if unusable."""
    try:
        rate = float(Fraction(text or "0"))
    except (ValueError, ZeroDivisionError):
        return 0.0
    return rate if 0 < rate <= 240 else 0.0


def tools_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def inspect_video(path: Path) -> VideoKenndaten:
    if not path.is_file():
        raise FileNotFoundError(path)
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=30,
        creationflags=PROCESS_FLAGS,
    )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("No video stream found")
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise ValueError("Could not read video duration")
    # DVR transport streams can report a container average that differs from
    # the real frame spacing; the stream's base rate matches the packets.
    fps = _rate(video.get("r_frame_rate")) or _rate(video.get("avg_frame_rate"))
    return VideoKenndaten(duration, int(video["width"]), int(video["height"]),
                          any(stream.get("codec_type") == "audio" for stream in streams), fps)


def still_frame(path: Path, seconds: float, destination: Path, filters: str = "") -> None:
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(max(0, seconds)),
            "-i", str(path), "-frames:v", "1"]
    if filters:
        args.extend(["-vf", filters])
    args.append(str(destination))
    subprocess.run(args, capture_output=True, text=True, check=True, timeout=90,
                   creationflags=PROCESS_FLAGS)
