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

![Analog dropouts repaired: original left, cleaned right](docs/media/analog-dropouts.gif)

[Features](#features) · [Before / after](#before-and-after) · [How it works](#how-it-works) · [Workflow](#the-workflow) · [Getting started](#getting-started) · [Coming soon](#coming-soon) · [Licence](#licence)

</div>

---

## Features

| | |
|---|---|
| 🌈 **Rainbow stripes gone** | Hue tears from a disturbed colour carrier are detected in each frame and replaced with colour from neighbouring frames. |
| ⚫⚪ **Black-and-white dropouts filled** | Frames or row bands where the receiver lost colour get their colour back; the brightness is never touched. |
| 🧾 **OSD removal** | Finds text, icons and bars that stay in place while the picture moves, including warnings that appear only briefly, and refills them from frames where the scene behind them is visible. |
| 🔍 **Multi-frame upscaling** | 1.5× (720p → 1080p) or 2×. Neighbouring frames add detail and calm noise instead of just enlarging one frame. |
| ✂️ **Filmstrip trim editor** | Thumbnail timeline with zoom, start/end markers, quick play and keyboard shortcuts (`I`, `O`, `Space`, arrows). |
| 👀 **Check before you export** | Render any frame through the whole chain and compare original and result with a split slider. |
| ⏱️ **Batch export** | Every clip keeps its own cut. Live remaining time (hh:mm:ss), cancel at any moment, original audio kept. |
| 🧠 **Optional local training** | A tiny self-supervised model can learn your receiver's pixel statistics from your own footage. |
| 🔒 **Private** | No account, no upload, no telemetry. Everything runs locally. |

## Before and after

All examples are real DVR recordings. Left is the original, right is the result. Only the 4:3 picture area is shown.

| Rainbow stripes | OSD removal |
|---|---|
| ![Rainbow stripes removed](docs/media/analog-rainbow.gif) | ![OSD removed](docs/media/osd-removal.gif) |
| **Colour dropouts and noise** | **1.5× upscaling (detail crop)** |
| ![Black-and-white dropouts repaired](docs/media/analog-dropouts.gif) | ![Upscaling detail](docs/media/upscaling.gif) |

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

### 2 · Optional model / HDZero block repair

You can add a small network trained on your own recordings (*Model training*). For HDZero, broken blocks are detected and replaced from the motion-aligned previous frame.

### 3 · OSD removal

- **Finding overlays:** at the edge of a drawn overlay, the brightness gradient points the same way in every frame. At scene edges it points in all directions as the drone moves. Comparing summed gradient *vectors* with summed gradient *lengths* per colour channel marks edges that hold still. These edges are grouped into text lines and shapes, and closed outlines such as bars are filled.
- **Brief overlays:** a sliding window applies the same test to warnings that appear only for a moment, but only while the scene is moving.
- **Removal:** each region is filled from the neighbouring frame whose aligned content fits the region's rim best, with its brightness offset corrected. Motion is chained frame by frame, so even frames far apart line up. Where no frame shows the background, the region is inpainted.

### 4 · Multi-frame upscaling

The current frame and two frames on each side are enlarged with Lanczos and warped onto the same grid. They are then fused with weights that drop where they disagree. A mild, clipped detail lift follows, which avoids halos.

The output is H.264 MP4 with the original audio. Results are written to a temporary file and only renamed when the export succeeds.

## The workflow

| 1 · Import | 2 · Trim |
|---|---|
| ![Import](docs/media/ui-import.png) | ![Trim](docs/media/ui-trim.png) |
| **3 · Repair** | **4 · Export** |
| ![Repair](docs/media/ui-repair.png) | ![Export](docs/media/ui-export.png) |

1. **Import:** drop recordings onto the window or add a folder.
2. **Trim:** mark start and end on the filmstrip (`I` / `O`), or type exact times.
3. **Repair:** choose Analog or HDZero, colour repair, OSD removal and output size, then press *Render this frame* to check the result.
4. **Export:** choose a folder and start. The remaining time is shown live, and you can cancel at any moment.

## Getting started

### Requirements

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
- [ ] Ready-to-run packages that include the engine (no Python setup)
- [ ] Better HDZero repair for long breakups across several frames
- [ ] Presets per goggles and receiver
- [ ] German user interface

Ideas and bug reports are welcome in the [issues](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/issues).

## Limitations

- Frame-by-frame cleaning is CPU-bound. Expect several seconds of processing per second of video with all options enabled.
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
