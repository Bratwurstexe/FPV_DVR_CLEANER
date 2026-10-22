"""Assemble the 1080p introduction video from comparison clips, diagrams and screenshots.

Usage: python tools/make_intro_video.py <comparison folder> <output.mp4>

The comparison folder is the output of tools/make_media.py. Title cards are
rendered from small HTML pages with a headless Chromium browser (Edge or
Chrome); everything else is put together with FFmpeg.
"""

from __future__ import annotations

import argparse
import html
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "src" / "flugbild" / "assets"
REPO = "github.com/Bratwurstexe/FPV_DVR_CLEANER"
SIZE = (1920, 1080)
FPS = 30

CARD_STYLE = """
body { margin:0; width:1920px; height:1080px; background:#1B2025; color:#F3F3F0;
       font-family:Inter, 'Segoe UI', Helvetica, Arial, sans-serif; display:flex; align-items:center; }
.wrap { padding:0 160px; }
.eyebrow { color:#F08A3A; font-size:30px; font-weight:700; letter-spacing:4px; text-transform:uppercase; }
h1 { font-size:104px; margin:18px 0 10px; line-height:1.05; }
h2 { font-size:64px; margin:18px 0 10px; line-height:1.1; }
p { font-size:38px; color:#C7CDD2; margin:14px 0; line-height:1.35; max-width:1500px; }
.chips span { display:inline-block; background:#A64600; color:white; font-size:30px; font-weight:600;
              padding:12px 26px; border-radius:40px; margin:24px 14px 0 0; }
.url { font-family:Consolas, Menlo, monospace; color:#FFC08E; font-size:44px; margin-top:34px; }
.bar { width:140px; height:10px; background:#A64600; border-radius:6px; margin-bottom:34px; }
"""


def browser() -> str:
    for candidate in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                      r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                      shutil.which("google-chrome") or "", shutil.which("chromium") or ""):
        if candidate and Path(candidate).exists():
            return candidate
    raise SystemExit("A Chromium-based browser is needed to render the title cards")


def render(page: str, target: Path, work: Path, width: int = SIZE[0], height: int = SIZE[1],
           scale: int = 1) -> Path:
    source = work / f"{target.stem}.html"
    source.write_text(page, encoding="utf-8")
    # A fixed scale factor keeps CSS pixels equal to video pixels, whatever the
    # Windows display scaling is.
    subprocess.run([browser(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    f"--force-device-scale-factor={scale}", f"--window-size={width},{height}",
                    f"--screenshot={target}", source.as_uri()],
                   capture_output=True, timeout=120)
    if not target.exists():
        raise SystemExit(f"Could not render {source}")
    return target


def card(eyebrow: str, title: str, lines: list[str], chips: list[str] = (), url: str = "",
         big: bool = False) -> str:
    heading = "h1" if big else "h2"
    body = "".join(f"<p>{html.escape(line)}</p>" for line in lines)
    chip_row = f"<div class='chips'>{''.join(f'<span>{html.escape(c)}</span>' for c in chips)}</div>" if chips else ""
    link = f"<div class='url'>{html.escape(url)}</div>" if url else ""
    return (f"<!doctype html><meta charset='utf-8'><style>{CARD_STYLE}</style><div class='wrap'>"
            f"<div class='bar'></div><div class='eyebrow'>{html.escape(eyebrow)}</div>"
            f"<{heading}>{html.escape(title)}</{heading}>{body}{chip_row}{link}</div>")


def framed(image: str, eyebrow: str, title: str) -> str:
    """A diagram or screenshot on the dark stage with a heading."""
    return (f"<!doctype html><meta charset='utf-8'><style>{CARD_STYLE}"
            "body{display:block} .top{padding:56px 110px 0} img{display:block;margin:34px auto 0;"
            "width:1700px;max-height:820px;object-fit:contain;border-radius:18px;box-shadow:0 20px 60px #0008}</style>"
            f"<div class='top'><div class='eyebrow'>{html.escape(eyebrow)}</div>"
            f"<h2 style='font-size:52px;margin:10px 0 0'>{html.escape(title)}</h2></div>"
            f"<img src='{Path(image).as_uri()}'>")


def text_filter(text: str, size: int, y: int, colour: str = "white") -> str:
    safe = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")
    return (f"drawtext=fontfile=Inter.ttf:text='{safe}':fontsize={size}:fontcolor={colour}:"
            f"x=(w-text_w)/2:y={y}")


def still_segment(picture: Path, seconds: float, target: Path) -> Path:
    fade = min(0.5, seconds / 4)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-t", str(seconds),
                    "-i", str(picture), "-f", "lavfi", "-t", str(seconds), "-i", "anullsrc=r=48000:cl=stereo",
                    "-vf", f"scale={SIZE[0]}:{SIZE[1]}:force_original_aspect_ratio=decrease,"
                           f"pad={SIZE[0]}:{SIZE[1]}:(ow-iw)/2:(oh-ih)/2:color=0x1B2025,fps={FPS},format=yuv420p,"
                           f"fade=in:st=0:d={fade},fade=out:st={seconds - fade}:d={fade}",
                    "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-c:a", "aac", "-shortest", str(target)],
                   check=True)
    return target


def clip_segment(clip: Path, title: str, note: str, target: Path, seconds: float | None = None) -> Path:
    length = seconds or float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
        capture_output=True, text=True, check=True).stdout.strip())
    fade = 0.5
    graph = (f"[0:v]scale=1840:880:force_original_aspect_ratio=decrease,"
             f"pad={SIZE[0]}:{SIZE[1]}:(ow-iw)/2:1080-ih-40:color=0x1B2025,"
             f"{text_filter(title, 54, 40)},{text_filter(note, 30, 112, '0xC7CDD2')},fps={FPS},format=yuv420p,"
             f"fade=in:st=0:d={fade},fade=out:st={length - fade}:d={fade}[v]")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-t", str(length), "-i", str(clip),
                    "-f", "lavfi", "-t", str(length), "-i", "anullsrc=r=48000:cl=stereo",
                    "-filter_complex", graph, "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-crf", "16",
                    "-preset", "medium", "-c:a", "aac", "-shortest", str(target)], check=True, cwd=FONT_DIR)
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("comparisons", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    clips = args.comparisons.resolve()
    media = ROOT / "docs" / "media"
    with tempfile.TemporaryDirectory(prefix="intro-video-") as scratch:
        work = Path(scratch)
        pictures = {name: render(page, work / f"{name}.png", work) for name, page in {
            "title": card("Open source · Analog & HDZero", "FPV DVR Cleaner",
                          ["Clean up your DVR recordings: rainbow stripes, colour dropouts, OSD clutter."],
                          ["Free", "MIT licence", "Runs locally"], big=True),
            "problem": card("The problem", "Weak signal, ugly DVR",
                            ["Analog: rainbow stripes and frames that drop to black and white.",
                             "Every DVR: OSD text and icons burned into the picture.",
                             "720p recordings that look soft on a big screen."]),
            "how": card("How it works", "Neighbouring frames know more",
                        ["Brightness survives a weak signal; colour does not.",
                         "Each frame keeps its own brightness and gets its colour back from aligned neighbours.",
                         "The OSD holds still while the scene moves, so it can be found and refilled."]),
            "pipeline": framed(str(work / "pipeline.png"), "Pipeline", "From DVR file to clean MP4"),
            "repair": framed(str(work / "colour-repair.png"), "Colour repair", "Keep the brightness, rebuild the colour"),
            "workflow": card("The app", "Four steps",
                             ["1  Import · drop your recordings", "2  Trim · filmstrip timeline, I / O keys",
                              "3  Repair · choose fixes, check one frame", "4  Export · live remaining time, cancel anytime"]),
            "outro": card("Free & open source", "Try it, improve it",
                          ["Windows, Linux and macOS · .NET 8 app with a Python engine.",
                           "Feedback, issues and pull requests are welcome."],
                          ["MIT licence", "No account", "No upload"], url=REPO),
        }.items() if name not in ("pipeline", "repair")}
        # Diagrams: render the SVGs first, then place them on the dark stage.
        for name, svg, height in (("pipeline", "pipeline.svg", 330), ("colour-repair", "colour-repair.svg", 360)):
            page = (f"<!doctype html><style>body{{margin:0;background:#F7F6F2}}</style>"
                    f"<img src='{(media / svg).as_uri()}' width='1200' height='{height}'>")
            render(page, work / f"{name}.png", work, 1200, height, scale=2)
        pictures["pipeline"] = render(framed(str(work / "pipeline.png"), "Pipeline", "From DVR file to clean MP4"),
                                      work / "pipeline-stage.png", work)
        pictures["repair"] = render(framed(str(work / "colour-repair.png"), "Colour repair",
                                           "Keep the brightness, rebuild the colour"), work / "repair-stage.png", work)
        screens = [render(framed(str(media / f"ui-{step}.png"), f"Step {number}", label), work / f"ui-{step}.png", work)
                   for number, (step, label) in enumerate([("import", "Import: drop recordings, build a queue"),
                                                            ("trim", "Trim: filmstrip timeline with markers"),
                                                            ("repair", "Repair: choose fixes and check a real frame"),
                                                            ("export", "Export: batch render with live remaining time")], 1)]
        parts = [
            still_segment(pictures["title"], 5, work / "s01.mp4"),
            still_segment(pictures["problem"], 6, work / "s02.mp4"),
            clip_segment(clips / "a_harburg_colour-compare.mp4", "Colour noise and rainbow streaks",
                         "original left · cleaned right", work / "s03.mp4"),
            clip_segment(clips / "b_bremerhaven_rainbow-compare.mp4", "Rainbow stripes removed",
                         "hue tears are detected and refilled from neighbouring frames", work / "s04.mp4"),
            clip_segment(clips / "c_osd_removed-compare.mp4", "OSD removal",
                         "text, icons, bars and brief warnings painted out", work / "s05.mp4"),
            clip_segment(clips / "d_upscaled-compare.mp4", "Noise reduction and upscaling to 1080p",
                         "detail crop · neighbouring frames add detail and calm noise", work / "s06.mp4"),
            still_segment(pictures["how"], 7, work / "s07.mp4"),
            still_segment(pictures["pipeline"], 7, work / "s08.mp4"),
            still_segment(pictures["repair"], 8, work / "s09.mp4"),
            still_segment(pictures["workflow"], 6, work / "s10.mp4"),
            *[still_segment(screen, 4, work / f"s11_{index}.mp4") for index, screen in enumerate(screens)],
            still_segment(pictures["outro"], 7, work / "s12.mp4"),
        ]
        listing = work / "parts.txt"
        listing.write_text("".join(f"file '{part.as_posix()}'\n" for part in parts), encoding="utf-8")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(listing), "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-c:a", "aac",
                        "-movflags", "+faststart", str(args.output)], check=True)
        # Keep the title card as a thumbnail suggestion next to the video.
        shutil.copyfile(pictures["title"], args.output.with_suffix(".thumbnail.png"))
    print(args.output)


if __name__ == "__main__":
    main()
