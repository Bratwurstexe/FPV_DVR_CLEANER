"""Assemble the 1080p introduction video from comparison clips, diagrams and screenshots.

Usage: python tools/make_intro_video.py <comparison folder> <hdzero-slow.mp4> <output.mp4>

The comparison folder is the output of tools/make_media.py; the slowed HDZero
clip comes from tools/make_hdzero_media.py --video. Title cards are
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


def thumbnail(compare: Path, seconds: float, target: Path, work: Path) -> Path:
    """One frame split down the middle, original left and cleaned right, with a large
    headline, as a JPEG for YouTube (under 2 MB). ``compare`` is a make_media clip:
    two panels of the same frame side by side."""
    still = work / "thumbnail-frame.png"
    # Left half of the original panel next to the right half of the cleaned one.
    # Damage often lasts a single frame: trim keeps the first frame at or after ``seconds`` exactly.
    graph = (f"[0:v]trim=start={max(0.0, seconds - 0.004):.3f},split[a][b];[a]crop=iw/4:ih:0:0[left];"
             "[b]crop=iw/4:ih:iw*3/4:0[right];[left][right]hstack,scale=1920:-2,crop=1920:1080")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(compare),
                    "-filter_complex", graph, "-frames:v", "1", str(still)], check=True)
    page = (f"<!doctype html><meta charset='utf-8'><style>{CARD_STYLE}"
            "body{display:block;position:relative;overflow:hidden}"
            "img{position:absolute;left:0;top:0;width:1920px;height:1080px}"
            ".split{position:absolute;left:957px;top:0;width:6px;height:1080px;background:white}"
            ".band{position:absolute;left:0;right:0;top:0;padding:46px 70px 120px;"
            "background:linear-gradient(#000e 55%,transparent)}"
            ".big{font-size:132px;font-weight:800;line-height:1;color:white;letter-spacing:-2px}"
            ".small{font-size:56px;color:#FFC08E;margin-top:18px;font-weight:700}"
            ".tag{position:absolute;bottom:44px;font-size:64px;font-weight:800;color:white;padding:10px 30px;"
            "border-radius:16px}.before{left:60px;background:#000b}.after{right:60px;background:#A64600}</style>"
            f"<img src='{still.as_uri()}'><div class='split'></div>"
            "<div class='band'><div class='big'>FIX YOUR FPV DVR</div>"
            "<div class='small'>Analog &amp; HDZero · free &amp; open source</div></div>"
            "<div class='tag before'>BEFORE</div><div class='tag after'>AFTER</div>")
    picture = render(page, work / "thumbnail.png", work)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(picture), "-q:v", "3",
                    str(target)], check=True)
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("comparisons", type=Path, help="Output folder of tools/make_media.py")
    parser.add_argument("hdzero", type=Path, help="hdzero-slow.mp4 from tools/make_hdzero_media.py --video")
    parser.add_argument("output", type=Path)
    parser.add_argument("--thumbnail-clip", default="b_playground-compare.mp4",
                        help="Comparison clip the thumbnail frame is taken from")
    parser.add_argument("--thumbnail-at", type=float, default=1.667, help="Second of that clip")
    args = parser.parse_args()
    clips = args.comparisons.resolve()
    media = ROOT / "docs" / "media"
    with tempfile.TemporaryDirectory(prefix="intro-video-") as scratch:
        work = Path(scratch)
        pictures = {name: render(page, work / f"{name}.png", work) for name, page in {
            "title": card("Free & open source · Analog & HDZero", "FPV DVR Cleaner",
                          ["Clean up your FPV DVR recordings, on your own computer."],
                          ["Free", "MIT licence", "Windows · macOS · Linux", "CPU + GPU"], big=True),
            "problem": card("The problem", "Weak signal, ugly DVR",
                            ["Analog: rainbow stripes, colour noise and frames that drop to black and white.",
                             "HDZero: frames that break up into coloured confetti.",
                             "Every DVR: OSD text and icons burned into the picture."]),
            "how": card("How it works", "Neighbouring frames know more",
                        ["A weak signal damages colour far more than brightness, and differently in every frame.",
                         "Each frame keeps its brightness and gets its colour back from motion-aligned neighbours.",
                         "Broken HDZero blocks are rebuilt from the clean frames before and after.",
                         "The OSD holds still while the scene moves, so it can be found and refilled."]),
            "speed": card("Speed", "CPU and GPU together",
                          ["Every processor core repairs frames side by side.",
                           "The graphics card takes the heavy pixel work: NVIDIA, AMD, Intel or Apple.",
                           "Fast, Balanced or Best: you choose time against quality.",
                           "How fast it runs depends on your processor and graphics card."]),
            "workflow": card("The app", "Four steps",
                             ["1  Import · drop your recordings", "2  Trim · filmstrip timeline, I / O keys",
                              "3  Repair · choose fixes, check one frame", "4  Export · live remaining time, cancel anytime"]),
            "outro": card("Free & open source", "Download it, try it, improve it",
                          ["Ready-to-run downloads for Windows, macOS and Linux.",
                           "Source code, issues and pull requests on GitHub:"],
                          ["MIT licence", "No account", "No upload"], url=REPO),
        }.items()}
        screens = [render(framed(str(media / f"ui-{step}.png"), f"Step {number}", label), work / f"ui-{step}.png", work)
                   for number, (step, label) in enumerate([("import", "Import: drop recordings, build a queue"),
                                                            ("trim", "Trim: filmstrip timeline with markers"),
                                                            ("repair", "Repair: choose fixes and check a real frame"),
                                                            ("export", "Export: batch render with live remaining time")], 1)]
        parts = [
            still_segment(pictures["title"], 5, work / "s01.mp4"),
            still_segment(pictures["problem"], 6, work / "s02.mp4"),
            clip_segment(clips / "a_dropouts-compare.mp4", "Colour dropouts and rainbow stripes",
                         "analog · original left · cleaned right", work / "s03.mp4"),
            clip_segment(clips / "b_playground-compare.mp4", "Black-and-white frames get their colour back",
                         "colour is rebuilt from the neighbouring frames", work / "s04.mp4"),
            clip_segment(clips / "c_noise-compare.mp4", "Heavy colour noise",
                         "grain and speckle calmed, moving edges stay sharp", work / "s05.mp4"),
            clip_segment(clips / "f_osd-compare.mp4", "OSD removal",
                         "voltage, icons and warnings painted out", work / "s07.mp4"),
            clip_segment(clips / "d_upscaled-compare.mp4", "Upscaling to 1080p",
                         "detail crop · neighbouring frames add detail and calm noise", work / "s08.mp4"),
            # At 90 fps a breakup lasts a few frames; slowed down and held it can be seen.
            clip_segment(args.hdzero.resolve(), "HDZero breakups, slowed down",
                         "frames lost to confetti are rebuilt from the frames around them", work / "s10.mp4"),
            still_segment(pictures["how"], 8, work / "s11.mp4"),
            still_segment(pictures["speed"], 7, work / "s12.mp4"),
            still_segment(pictures["workflow"], 5, work / "s13.mp4"),
            *[still_segment(screen, 3.5, work / f"s14_{index}.mp4") for index, screen in enumerate(screens)],
            still_segment(pictures["outro"], 8, work / "s15.mp4"),
        ]
        listing = work / "parts.txt"
        listing.write_text("".join(f"file '{part.as_posix()}'\n" for part in parts), encoding="utf-8")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(listing), "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-c:a", "aac",
                        "-movflags", "+faststart", str(args.output)], check=True)
        thumbnail(clips / args.thumbnail_clip, args.thumbnail_at, args.output.with_suffix(".thumbnail.jpg"), work)
    print(args.output)


if __name__ == "__main__":
    main()
