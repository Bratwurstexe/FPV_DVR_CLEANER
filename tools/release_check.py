"""Release gate: run realistic requests through a packaged engine and check the files.

Usage:
  python tools/release_check.py <engine> [--work DIR]

<engine> is the frozen engine (engine/flugbild-engine[.exe]) of a package, or
"source" to run src/flugbild through the current Python. FFmpeg and ffprobe
must be on PATH (in a package: its ffmpeg folder).

Every case builds a request the way the desktop app does, runs it in a fresh
engine process and then checks the output with ffprobe: picture size, length,
and whether there is sound. A run that "succeeds" but cuts the video short or
loses the sound fails here too. Exits 1 if any case fails.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height,duration:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    length = float((video or {}).get("duration") or data["format"]["duration"])
    return {"width": int(video["width"]), "height": int(video["height"]), "duration": length, "audio": audio is not None}


def make_sources(folder: Path) -> dict[str, Path]:
    """DVR-like clips: a 4:3 picture in a 720p frame with a static white 'OSD', moving content."""
    picture = ("color=c=0x3a6ea5:size=944x720:rate=60,format=yuv420p,"
               "drawbox=x='mod(t*220,800)':y=300:w=140:h=140:color=0xd08a40:t=fill,"
               "drawbox=x=380:y=640:w=180:h=26:color=white:t=fill,drawbox=x=20:y=20:w=60:h=30:color=white:t=fill,"
               "pad=1280:720:168:0:black")
    sources = {
        # The case that broke 0.7.1: sound ends before the picture.
        "short_sound": ("3.0", "2.75"),
        "long_sound": ("3.0", "3.4"),
        "no_sound": ("3.0", None),
    }
    made = {}
    for name, (video, audio) in sources.items():
        path = folder / f"{name}.mp4"
        args = ["-f", "lavfi", "-i", f"{picture},trim=duration={video}"]
        if audio:
            args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={audio}", "-c:a", "aac"]
        args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30", str(path)]
        ffmpeg(*args)
        made[name] = path
    return made


def hand_mask(path: Path) -> Path:
    """Corrections like a user's: an added box and an erased one, as the mask editor stores them."""
    import cv2
    mask = np.zeros((720, 1280), np.uint8)
    mask[20:70, 900:1000] = 255     # add something the search did not find
    mask[630:676, 370:570] = 128    # keep part of what it found
    cv2.imwrite(str(path), mask)
    return path


def engine_command(engine: str) -> list[str]:
    if engine == "source":
        return [sys.executable, "-m", "flugbild.bridge"]
    return [str(Path(engine).resolve())]


def run_engine(command: list[str], request: dict, folder: Path, name: str) -> tuple[bool, str]:
    ticket = folder / f"{name}.json"
    ticket.write_text(json.dumps(request), encoding="utf-8")
    env = dict(os.environ)
    if command[0] == sys.executable:
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    # Another working directory, like the desktop app.
    result = subprocess.run(command + [str(ticket)], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", cwd=tempfile.gettempdir(), env=env, timeout=900)
    lines = [line for line in result.stdout.splitlines() if '"progress"' not in line]
    last = lines[-1] if lines else result.stderr.strip()[-400:]
    return '"result"' in last and result.returncode == 0, last


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("engine")
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()
    folder = (args.work or Path(tempfile.mkdtemp(prefix="release-check-"))).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    command = engine_command(args.engine)
    sources = make_sources(folder)
    mask = hand_mask(folder / "hand.png")

    analog = dict(signalart="analog", farbberuhigung=60, rauschminderung=50, kantendetail=15)
    cases: list[tuple[str, dict, dict]] = []  # name, request, expectation
    for source_name in ("short_sound", "long_sound", "no_sound"):
        sound = source_name != "no_sound"
        src = str(sources[source_name])
        cases += [
            (f"{source_name}/analog whole clip", dict(recipe=analog), dict(size=(1280, 720), length=3.0, sound=sound)),
            (f"{source_name}/OSD removed + hand mask", dict(recipe=dict(analog, osd_entfernen=True, osd_handmaske=str(mask))),
             dict(size=(1280, 720), length=3.0, sound=sound)),
            (f"{source_name}/HDZero blocks", dict(recipe=dict(signalart="digital", blockpflege=35)),
             dict(size=(1280, 720), length=3.0, sound=sound)),
            (f"{source_name}/plain copy", dict(recipe=dict(signalart="digital", blockpflege=0, kantendetail=0)),
             dict(size=(1280, 720), length=3.0, sound=sound)),
        ]
        for case in cases[-4:]:
            case[1]["source"] = src
    src = str(sources["short_sound"])
    cases += [
        ("cut 0.5-2.5 s", dict(source=src, recipe=dict(analog, schnittbeginn=0.5, schnittende=2.5)),
         dict(size=(1280, 720), length=2.0, sound=True)),
        ("two kept parts", dict(source=src, recipe=analog, segments=[[0.0, 1.0], [1.5, 3.0]]),
         dict(size=(1280, 720), length=2.5, sound=True)),
        ("best quality 1.5x + OSD", dict(source=src, recipe=dict(analog, vergroesserung_prozent=150, aufwand="best",
                                                                 osd_entfernen=True, osd_handmaske=str(mask))),
         dict(size=(1920, 1080), length=3.0, sound=True)),
        ("look on", dict(source=src, recipe=dict(analog, look=True, look_farbstimmung=0.6, look_vignette=0.5,
                                                look_koernung=0.2)), dict(size=(1280, 720), length=3.0, sound=True)),
        ("caption", dict(source=src, recipe=dict(analog, untertiteltext="Release check")),
         dict(size=(1280, 720), length=3.0, sound=True)),
    ]
    for fmt, ext in (("mp4-hevc", ".mp4"), ("mov-prores", ".mov"), ("mkv-h264", ".mkv"), ("webm-vp9", ".webm")):
        cases.append((f"format {fmt}", dict(source=src, recipe=dict(analog, dateiformat=fmt)),
                      dict(size=(1280, 720), length=3.0, sound=True, ext=ext)))

    failures = 0
    cleaned_for_share = None
    for index, (name, request, expect) in enumerate(cases):
        target = folder / f"out{index:02d}{expect.get('ext', '.mp4')}"
        full = dict(action="export", mode="manual", target=str(target), **request)
        okay, message = run_engine(command, full, folder, f"case{index:02d}")
        problem = "" if okay else f"engine: {message}"
        if okay:
            info = probe(target)
            if (info["width"], info["height"]) != expect["size"]:
                problem = f"size {info['width']}x{info['height']}, expected {expect['size'][0]}x{expect['size'][1]}"
            elif abs(info["duration"] - expect["length"]) > 0.2:
                problem = f"length {info['duration']:.2f} s, expected {expect['length']:.2f} s"
            elif info["audio"] != expect["sound"]:
                problem = "sound missing" if expect["sound"] else "unexpected sound"
            if not problem and name == "short_sound/analog whole clip":
                cleaned_for_share = target
        failures += bool(problem)
        print(f"{'FAIL' if problem else 'ok  '}  {name}{'  -> ' + problem if problem else ''}", flush=True)

    # Preview and OSD actions the Repair page uses, with the hand mask.
    for name, request, outputs in (
        ("preview frame", dict(action="preview", source=str(sources["short_sound"]), seconds=1.5,
                               recipe=dict(analog, osd_entfernen=True, osd_handmaske=str(mask)),
                               before=str(folder / "before.png"), after=str(folder / "after.png")), ("before.png", "after.png")),
        ("OSD search for the mask editor", dict(action="osd", source=str(sources["short_sound"]), target=str(folder / "marked.png"),
                                                mask_target=str(folder / "auto.png"), frame_target=str(folder / "frame.png"),
                                                manual_mask=str(mask)), ("marked.png", "auto.png", "frame.png"))):
        okay, message = run_engine(command, request, folder, name.replace(" ", "_"))
        missing = [out for out in outputs if not (folder / out).is_file()]
        problem = "" if okay and not missing else (f"engine: {message}" if not okay else f"missing {missing}")
        failures += bool(problem)
        print(f"{'FAIL' if problem else 'ok  '}  {name}{'  -> ' + problem if problem else ''}", flush=True)

    # Share from a cleaned export, both formats.
    if cleaned_for_share is not None:
        for fmt, size, sound in (("landscape", (1920, 1080), True), ("vertical", (1080, 1920), False)):
            output = folder / f"share-{fmt}.mp4"
            okay, message = run_engine(command, dict(action="share", target=str(cleaned_for_share), output=str(output),
                                                     format=fmt), folder, f"share_{fmt}")
            problem = "" if okay else f"engine: {message}"
            if okay:
                info = probe(output)
                if (info["width"], info["height"]) != size:
                    problem = f"size {info['width']}x{info['height']}"
                elif abs(info["duration"] - 3.0) > 0.2:
                    problem = f"length {info['duration']:.2f} s"
                elif info["audio"] != sound:
                    problem = "sound missing" if sound else "unexpected sound"
            failures += bool(problem)
            print(f"{'FAIL' if problem else 'ok  '}  share {fmt}{'  -> ' + problem if problem else ''}", flush=True)

    print(f"\n{failures} failed" if failures else "\nall release checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
