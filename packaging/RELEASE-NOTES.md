Clean up analog and HDZero DVR recordings on your own computer: rainbow stripes, colour dropouts, noise and the OSD. Unpack and start; the app, the video engine and FFmpeg are included, so you don't need to install Python or FFmpeg.

## Download

| Your system | File | Start |
|---|---|---|
| **Windows** 10 / 11 (64-bit) | `FPV-DVR-Cleaner-…-windows-x64.zip` | `FpvDvrCleaner.Desktop.exe` |
| **macOS**, Apple Silicon (M1 and newer) | `FPV-DVR-Cleaner-…-macos-apple-silicon.zip` | `FPV DVR Cleaner.app` |
| **macOS**, Intel | `FPV-DVR-Cleaner-…-macos-intel.zip` | `FPV DVR Cleaner.app` |
| **Linux** (x64) | `FPV-DVR-Cleaner-…-linux-x64.tar.gz` | `./FpvDvrCleaner.Desktop` |
| **Source code** | *Source code (zip)* or *(tar.gz)* below | see the README |

`SHA256SUMS.txt` lists the checksum of every package.

## New in 0.2.0

**Much faster**
- Frames are repaired on all CPU cores at once.
- The black side bars of 4:3 goggle recordings are skipped: about a quarter less work, for analog and HDZero.
- New **Processing speed** setting on the Repair page: *Fast*, *Balanced* (default) or *Best*.
- One minute of 720p60 analog footage with every option on, upscaled to 1080p: 6.6 minutes with *Balanced* (55 minutes in 0.1.0), 4.8 minutes with *Fast*. Measured on a laptop; see the chart in the README.

**Cleaner OSD removal**
- The stats screen after landing, or *ARMED* at the start, is now removed only while it is on screen. Before, it was painted out of the whole flight and left smeared patches.
- Straight edges in corridors and window frames are no longer mistaken for warnings. Real warnings such as *LOW BATTERY* or *CRASH FLIP* are still removed.

**Packages**
- Windows and Linux now ship an FFmpeg build with the H.264 encoder the export needs.

## First start

- **Windows:** SmartScreen may warn about an unknown publisher. Choose *More info → Run anyway*.
- **macOS:** the app is not notarised. Right-click it and choose *Open*, or run `xattr -cr "FPV DVR Cleaner.app"` in Terminal.
- **Linux:** if the file manager does not start it, run `chmod +x FpvDvrCleaner.Desktop` once.

All processing stays on your computer. The application code is MIT licensed. The bundled components keep their own licences: Avalonia (MIT), NumPy (BSD), OpenCV (Apache-2.0) and FFmpeg (GPL, a separate program in the `ffmpeg` folder; source at https://ffmpeg.org/download.html). See `THIRD-PARTY-NOTICES.md` and `ffmpeg/FFMPEG-LICENSE.txt` in the package.
