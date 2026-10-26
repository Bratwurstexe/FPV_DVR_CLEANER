> [!IMPORTANT]
> **Recommended: the launcher.** It always installs the newest FPV DVR Cleaner: pick your system, and it downloads the latest release, checks it, deletes the old version and starts the app. Download it once; it updates the app from then on.
> **[⬇ Windows](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/latest/download/FPV-DVR-Cleaner-Launcher-windows-x64.exe)** · **[macOS Apple Silicon](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/latest/download/FPV-DVR-Cleaner-Launcher-macos-apple-silicon.zip)** · **[macOS Intel](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/latest/download/FPV-DVR-Cleaner-Launcher-macos-intel.zip)** · **[Linux](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/latest/download/FPV-DVR-Cleaner-Launcher-linux-x64.tar.gz)**

## Download {TAG}

| Your system | Download | Start |
|---|---|---|
| **Windows** 10 / 11 (64-bit) | **[⬇ Windows (.zip)](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/download/{TAG}/FPV-DVR-Cleaner-{TAG}-windows-x64.zip)** | `FpvDvrCleaner.Desktop.exe` |
| **macOS**, Apple Silicon (M1 and newer) | **[⬇ macOS Apple Silicon (.zip)](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/download/{TAG}/FPV-DVR-Cleaner-{TAG}-macos-apple-silicon.zip)** | `FPV DVR Cleaner.app` |
| **macOS**, Intel | **[⬇ macOS Intel (.zip)](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/download/{TAG}/FPV-DVR-Cleaner-{TAG}-macos-intel.zip)** | `FPV DVR Cleaner.app` |
| **Linux** (x64) | **[⬇ Linux AppImage](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/download/{TAG}/FPV-DVR-Cleaner-{TAG}-linux-x64.AppImage)** | `chmod +x` it, then start it |
| **Linux** (x64), plain folder | **[⬇ Linux folder (.tar.gz)](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/download/{TAG}/FPV-DVR-Cleaner-{TAG}-linux-x64.tar.gz)** | `./FpvDvrCleaner.Desktop` |

All packages include the app, the video engine and FFmpeg: unpack and start, nothing else to install. [`SHA256SUMS.txt`](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/download/{TAG}/SHA256SUMS.txt) lists the checksum of every file.

## New in 0.7.2

**Important fix: exports of whole recordings**
- In 0.7.1, exporting a recording without trimming it could stop with *"Export failed: [Errno 32] Broken pipe"* when its sound ends a moment before its picture, which is common for DVR files. Older versions finished such exports but cut the video to the length of the sound. Now the sound is filled with silence to the full length, and every frame is kept.
- If the encoder ever stops early, the message now says why instead of "Broken pipe".
- Every release is now checked by running the packaged app's engine on recordings with shorter, longer and no sound, with OSD removal and hand corrections, every file format, trims, preview, the mask editor and Share; lengths, sizes and sound are compared before anything is published.

**Share is a plain export now**
- **Share** in the result view brings the cleaned clip into the right format, nothing more: **16:9 for YouTube** (1920×1080, or 1280×720 for SD recordings; the picture centred on black) or **9:16 for Shorts, Reels and TikTok** (1080×1920; the picture fills the screen and pans smoothly along with the action).
- The card asks only for the format and the sound (on for 16:9, off for 9:16 unless you change it) and then *Make video*. The whole clip as you trimmed it, H.264 MP4 at up to 60 fps, saved next to the cleaned file as `…-youtube.mp4` or `…-short.mp4`.
- The original picture, side by side, wipe and flip layouts, labels, slow motion on repairs, "best N seconds" and the watermark are gone.

Earlier versions: see the [changelog](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/blob/main/CHANGELOG.md).

## First start

- **Windows:** SmartScreen may warn about an unknown publisher. Choose *More info → Run anyway*.
- **macOS:** the app is not notarised. Right-click it and choose *Open*, or run `xattr -cr "FPV DVR Cleaner.app"` in Terminal.
- **Linux:** if the file manager does not start it, run `chmod +x FpvDvrCleaner.Desktop` once.

All processing stays on your computer. The application code is MIT licensed. The bundled components keep their own licences: Avalonia (MIT), NumPy (BSD), OpenCV (Apache-2.0) and FFmpeg (GPL, a separate program in the `ffmpeg` folder; source at https://ffmpeg.org/download.html). See `THIRD-PARTY-NOTICES.md` and `ffmpeg/FFMPEG-LICENSE.txt` in the package.
