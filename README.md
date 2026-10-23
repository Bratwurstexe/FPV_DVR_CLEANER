<div align="center">

# FPV DVR Cleaner

**Open-source restoration for analog and HDZero DVR recordings.**
Removes rainbow stripes and black-and-white dropouts, paints out the OSD and upscales the result, all on your own computer.

[![License: MIT](https://img.shields.io/badge/license-MIT-A64600)](LICENSE)
[![Open source](https://img.shields.io/badge/open%20source-100%25-2ea44f)](https://github.com/Bratwurstexe/FPV_DVR_CLEANER)
[![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-5f676d)](#getting-started)
[![.NET 8](https://img.shields.io/badge/.NET-8-512BD4)](https://dotnet.microsoft.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB)](https://www.python.org)
[![Status](https://img.shields.io/badge/status-first%20release-F08A3A)](docs/STATUS.md)
[![Stars](https://img.shields.io/github/stars/Bratwurstexe/FPV_DVR_CLEANER?style=flat&color=A64600)](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/stargazers)

[![Download the latest release](https://img.shields.io/github/v/release/Bratwurstexe/FPV_DVR_CLEANER?label=Download&style=for-the-badge&color=A64600)](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/latest)

**[⬇ Download for Windows, macOS and Linux](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/latest)**: ready to run, no Python or FFmpeg setup.

![One frame, left half original, right half cleaned](docs/media/hero-split.jpg)

[Download](#download) · [Features](#features) · [Before / after](#before-and-after) · [Speed](#speed) · [How it works](#how-it-works) · [Workflow](#the-workflow) · [Getting started](#getting-started) · [Coming soon](#coming-soon) · [Licence](#licence)

</div>

---

## Features

- **Rainbow stripes removed.** Hue tears from a disturbed colour carrier are detected in each frame and replaced with colour from neighbouring frames.
- **Black-and-white dropouts filled.** Frames or row bands where the receiver lost colour get their colour back; the brightness is never touched.
- **Noise reduction and streak repair.** Five motion-aligned frames are fused around a robust median. Grain calms down, bright or dark streaks disappear, and moving edges stay sharp.
- **OSD removal.** Text, icons and bars that stay in place while the picture moves are found, including warnings that appear only briefly. They are refilled from frames where the scene behind them is visible. Screens that are shown only part of the time, such as the stats after landing, are removed only while they are on screen.
- **Multi-frame upscaling.** 1.5× (720p to 1080p) or 2×. Neighbouring frames add detail instead of just enlarging a single frame.
- **Filmstrip trim editor.** A thumbnail timeline with zoom, start and end markers, quick play and keyboard shortcuts (`I`, `O`, `Space`, arrow keys).
- **Check before you export.** Render any frame through the whole chain and compare original and result with a split slider.
- **Batch export.** Every clip keeps its own cut. The remaining time is shown live, you can cancel at any moment, and the original audio is kept.
- **Fast on every core.** Frames are repaired in parallel, black side bars are skipped, and a *Fast / Balanced / Best* setting trades time for quality. See [Speed](#speed).
- **Optional local training.** A small self-supervised model can learn your receiver's pixel statistics from your own footage.
- **Private.** No account, no upload, no telemetry. Everything runs locally.

## Before and after

All examples are real analog DVR recordings. Each image shows the full 4:3 picture and, below it, the marked area enlarged 2x.

![Colour noise, rainbow streaks and grain](docs/media/harburg-colour-before-after.jpg)

![Rainbow stripes from a disturbed colour carrier](docs/media/bremerhaven-rainbow-before-after.jpg)

![OSD removal](docs/media/osd-removed-before-after.jpg)

![Multi-frame upscaling to 1080p](docs/media/upscaled-before-after.jpg)

### In motion

| Colour noise and streaks | Rainbow stripes |
|---|---|
| ![Colour noise repaired](docs/media/analog-dropouts.gif) | ![Rainbow stripes removed](docs/media/analog-rainbow.gif) |
| **OSD removal** | **1.5x upscaling (detail)** |
| ![OSD removed](docs/media/osd-removal.gif) | ![Upscaling detail](docs/media/upscaling.gif) |

## Speed

How long an export takes, in minutes of processing for one minute of footage. Pick the speed on the *Repair* page: **Fast** for a quick look at a flight, **Balanced** (default) for most exports, **Best** for the final cut.

![Processing time per minute of analog footage for Fast, Balanced and Best](docs/media/speed.svg)

| Repair steps | Fast | Balanced | Best | Previous release |
|---|---:|---:|---:|---:|
| Colour repair | 1.4 min | 1.9 min | 2.1 min | 8.2 min |
| Colour repair + OSD removal + noise reduction | 3.8 min | 4.8 min | 6.6 min | 36 min |
| Everything + 1.5× upscaling to 1080p | 4.8 min | 6.6 min | 8.9 min | 55 min |

Measured on 20 seconds of a real 720p60 analog recording (4:3 picture in a 16:9 file) with an Intel Core i7-13850HX laptop on mains power. A laptop is held back by its power limit, so desktop processors with more cores usually finish sooner. Recordings at 90 or 180 fps take proportionally longer, because every frame is repaired. To measure your own machine, run [tools/make_speed_chart.py](tools/make_speed_chart.py) on your own timings.

## How it works

![Processing pipeline](docs/media/pipeline.svg)

The desktop app (.NET 8, Avalonia) is the front end: import, trimming, settings, preview and the export queue. All video work happens in a Python engine built on NumPy, OpenCV and FFmpeg. The two talk through small JSON requests and progress lines, so the engine can also be used and debugged on its own.

Each stage streams frames and looks at a short window of neighbours around the current frame:

### 1 · Analog colour repair

![Colour repair principle](docs/media/colour-repair.svg)

A weak analog signal damages colour far more than brightness, and the damage changes from frame to frame. The repair keeps the brightness of every frame and re-estimates only its colour:

- **Colour dropout:** a row with practically no colour signal is recognised by an absolute chroma level. A truly grey scene simply stays grey.
- **Hue tears (rainbow stripes):** saturated colour whose hue jumps from one row to the next. Only *transient* jumps count. An edge that shows the same jump in the neighbouring frames, such as a red roof against the sky or a coloured OSD bar, is part of the picture.
- **Estimate:** three earlier and three later frames are aligned with optical flow and weighted by how well their brightness matches. The colour is then estimated with a robust (Cauchy) average. If no neighbour has usable colour, the result fades to neutral grey instead of copying a stripe.
- **Lasting bands:** a stripe that returns at the same place in several frames is caught by its edges: a colour edge without a matching brightness edge. There the colour is refilled from a local brightness-to-colour model.
- **Settle:** finally the colour is smoothed with a filter guided by the brightness, so it follows the contours that survived the transmission instead of speckling.

### 2 · Optional model / HDZero block repair

You can add a small network trained on your own recordings (*Model training*). For HDZero, broken blocks are detected and replaced from the motion-aligned previous frame.

### 3 · OSD removal

- **Finding overlays:** at the edge of a drawn overlay, the brightness gradient points the same way in every frame. At scene edges it points in all directions as the drone moves. Comparing summed gradient *vectors* with summed gradient *lengths* per colour channel marks edges that hold still. These edges are grouped into text lines and shapes, and closed outlines such as bars are filled.
- **Brief overlays:** a sliding window applies the same test to warnings that appear only for a moment, but only while the scene is moving. A straight scene edge that slides along itself also keeps its direction, so only flat, level rows full of upright strokes count: lines of text such as *LOW BATTERY*.
- **Only while shown:** the clip-wide search also finds screens that are visible only part of the time, such as the stats after landing. For each element the edges that held still are remembered with their direction; a frame shows the element when enough of them are found there again. Changing digits have no steady edges, so the icon, point and unit decide. An element stays removed a moment longer after it was last seen, so blinking warnings do not flicker.
- **Removal:** each region is filled from the neighbouring frame whose aligned content fits the region's rim best, with its brightness offset corrected. Motion is chained frame by frame, so even frames far apart line up. Where no frame shows the background, the region is inpainted.

### 4 · Multi-frame fusion: noise reduction and upscaling

The current frame and two frames on each side are aligned onto the same grid. If upscaling is on, they are enlarged with Lanczos first.

- The per-pixel **median** of the five values is the reference. A bright or dark streak in one frame is an outlier against it and gets replaced, and random grain averages out.
- Every value is weighted by its distance to that reference. Where the aligned frames disagree among themselves, for example because of occlusion or a wrong motion estimate, the original pixel is kept, so nothing ghosts.
- A mild, clipped detail lift follows, which avoids halos.

The output is H.264 MP4 with the original audio. Results are written to a temporary file and only renamed when the export succeeds.

### 5 · How it stays fast

- **Every core works.** Each stage builds a frame only from its input neighbours, never from its own earlier output, so frames are processed side by side on a shared thread pool while the order is kept.
- **Only the picture.** Goggles record the 4:3 picture inside a 16:9 file. The black side bars are found once per cut, skipped by every stage and added back as clean black: about a quarter less work.
- **Fast / Balanced / Best** sets how many neighbouring frames are used, how finely motion is followed and how fast the encoder runs. *Best* is the full-quality setting; *Balanced* is close to it in clearly less time.

## The workflow

| 1 · Import | 2 · Trim |
|---|---|
| ![Import](docs/media/ui-import.png) | ![Trim](docs/media/ui-trim.png) |
| **3 · Repair** | **4 · Export** |
| ![Repair](docs/media/ui-repair.png) | ![Export](docs/media/ui-export.png) |

1. **Import:** drop recordings onto the window or add a folder.
2. **Trim:** mark start and end on the filmstrip (`I` / `O`), or type exact times.
3. **Repair:** choose Analog or HDZero, colour repair, OSD removal, output size and processing speed, then press *Render this frame* to check the result.
4. **Export:** choose a folder and start. The remaining time is shown live, and you can cancel at any moment.

## Getting started

### Download

Get the package for your system from the **[latest release](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/latest)**. It contains the app, the video engine and FFmpeg. Unpack it and start it; nothing else has to be installed.

| System | File | Start |
|---|---|---|
| Windows 10/11 (64-bit) | `FPV-DVR-Cleaner-…-windows-x64.zip` | `FpvDvrCleaner.Desktop.exe` |
| macOS, Apple Silicon | `FPV-DVR-Cleaner-…-macos-apple-silicon.zip` | `FPV DVR Cleaner.app` |
| macOS, Intel | `FPV-DVR-Cleaner-…-macos-intel.zip` | `FPV DVR Cleaner.app` |
| Linux (x64) | `FPV-DVR-Cleaner-…-linux-x64.tar.gz` | `./FpvDvrCleaner.Desktop` |

On Windows, SmartScreen may warn about an unknown publisher: choose *More info → Run anyway*. On macOS the app is not notarised: right-click it and choose *Open* the first time.

### Requirements (run from source)

- Windows 10/11, Linux or macOS
- [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0) (to build the app)
- Python 3.10 or newer with `pip install numpy opencv-python-headless`
- [FFmpeg](https://ffmpeg.org/download.html) and `ffprobe` on your `PATH`

### Run from source

```powershell
git clone https://github.com/Bratwurstexe/FPV_DVR_CLEANER.git
cd FPV_DVR_CLEANER
python -m pip install -e .
dotnet run --project desktop/FpvDvrCleaner.Desktop
```

### Visual Studio Code

Open the folder in VS Code and install the recommended extensions (C# Dev Kit, Python).

| Action | How |
|---|---|
| Debug the app | `F5` → *Desktop app* (builds first) |
| Debug with a clip loaded | `F5` → *Desktop app with a clip* |
| Debug the engine | *Engine: unit tests* or *Engine: run one bridge request* |
| Build | `Ctrl+Shift+B` (Debug) |
| Release build | *Terminal → Run Task → build: desktop (Release, Windows x64)*, output in `dist/desktop-win-x64` |
| Tests | *Terminal → Run Task → test: engine* |

The [build workflow](.github/workflows/build.yml) produces packages for Windows, Linux and macOS from a version tag.

## Coming soon

- [ ] **AI HD upscaling:** a neural super-resolution model trained for FPV footage, as an option next to multi-frame upscaling
- [ ] GPU acceleration for faster exports
- [x] Ready-to-run packages that include the engine (no Python setup)
- [ ] Better HDZero repair for long breakups across several frames
- [ ] Presets per goggles and receiver
- [ ] German user interface

Ideas and bug reports are welcome in the [issues](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/issues).

## Limitations

- Frame-by-frame cleaning is CPU-bound. With all options enabled, expect a few minutes of processing per minute of footage (see [Speed](#speed)); on laptops the power limit sets the pace.
- Picture content that is missing in *every* nearby frame cannot be recovered. Long breakups and torn brightness stay visible.
- OSD removal needs camera movement. In a hover, the area behind the OSD may never be visible and is inpainted instead.

## Licence

The application code is released under the [MIT licence](LICENSE). It uses open-source components with their own licences (Avalonia, SkiaSharp, NumPy, OpenCV, FFmpeg, the Inter typeface and others); see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). The algorithms, data structures and interface were written for this project; see [docs/INDEPENDENCE.md](docs/INDEPENDENCE.md).

---

<details>
<summary><b>Deutsch</b></summary>

**FPV DVR Cleaner** ist ein kostenloses Open-Source-Programm, das analoge und HDZero-DVR-Aufnahmen aufbereitet:

- entfernt Regenbogenstreifen und Schwarzweiß-Ausfälle
- blendet auf Wunsch das OSD aus
- skaliert auf 1080p hoch

Alles läuft lokal auf deinem Rechner. Der Ablauf hat vier Schritte: **Import → Schneiden (Filmstreifen-Zeitleiste) → Reparieren (mit Vorschau) → Export (mit Restzeit, abbrechbar)**.

Voraussetzungen: .NET 8, Python 3.10+ mit `numpy` und `opencv-python-headless`, FFmpeg.

</details>
