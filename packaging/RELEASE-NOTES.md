Ready-to-run builds: unpack and start. The app, the video engine and FFmpeg are included; you don't need to install Python or FFmpeg.

| System | File |
|---|---|
| Windows 10/11 (64-bit) | `FPV-DVR-Cleaner-…-windows-x64.zip` → `FpvDvrCleaner.Desktop.exe` |
| macOS, Apple Silicon | `FPV-DVR-Cleaner-…-macos-apple-silicon.zip` → `FPV DVR Cleaner.app` |
| macOS, Intel | `FPV-DVR-Cleaner-…-macos-intel.zip` → `FPV DVR Cleaner.app` |
| Linux (x64) | `FPV-DVR-Cleaner-…-linux-x64.tar.gz` → `./FpvDvrCleaner.Desktop` |

**First start**
- **Windows:** SmartScreen may warn about an unknown publisher. Choose *More info → Run anyway*.
- **macOS:** the app is not notarised. Right-click it and choose *Open*, or run `xattr -cr "FPV DVR Cleaner.app"` in Terminal.

**What it does**
- Removes rainbow stripes, colour dropouts and colour noise from analog DVR recordings.
- Reduces noise and repairs streaks using neighbouring frames.
- Finds and removes the OSD on request.
- Upscales to 1.5× (1080p) or 2×.
- Includes a filmstrip trim editor, a frame preview, and a batch export with live remaining time.

All processing stays on your computer. The application code is MIT licensed. The bundled components keep their own licences: Avalonia (MIT), NumPy (BSD), OpenCV (Apache-2.0) and FFmpeg (GPL, a separate program in the `ffmpeg` folder; source at https://ffmpeg.org/download.html). See `THIRD-PARTY-NOTICES.md` and `ffmpeg/FFMPEG-LICENSE.txt` in the package.
