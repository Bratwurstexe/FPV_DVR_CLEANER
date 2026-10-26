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

## New in 0.8.0

**In your language**
- FPV DVR Cleaner now speaks **15 languages**: English, Deutsch, Français, Español, Italiano, Português (Brasil), Nederlands, Polski, Čeština, Русский, Українська, Türkçe, 日本語, 简体中文 and 한국어.
- It follows the language of your system; when yours is not among them, it starts in English. The menu at the bottom of the step rail switches the language at any time: the app restarts in the new language and keeps the clips of your queue.
- Every page, button, hint, tooltip and preset name is translated, and so are the error messages of the video engine. Chinese, Japanese and Korean text is drawn with your system's fonts for those scripts, and every page was checked in every language so that nothing is cut off or overlaps.

**Tidier pages**
- Long labels on the Repair page wrap onto a second line instead of being cut off; small grey hints that do not fit end in "…".
- On the releases page only the newest release keeps its notes open; older releases fold their "New in …" and downloads away. The README shows the pictures first, the technical details fold out below.

Earlier versions: see the [changelog](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/blob/main/CHANGELOG.md).

## First start

- **Windows:** SmartScreen may warn about an unknown publisher. Choose *More info → Run anyway*.
- **macOS:** the app is not notarised. Right-click it and choose *Open*, or run `xattr -cr "FPV DVR Cleaner.app"` in Terminal.
- **Linux:** if the file manager does not start it, run `chmod +x FpvDvrCleaner.Desktop` once.

All processing stays on your computer. The application code is MIT licensed. The bundled components keep their own licences: Avalonia (MIT), NumPy (BSD), OpenCV (Apache-2.0) and FFmpeg (GPL, a separate program in the `ffmpeg` folder; source at https://ffmpeg.org/download.html). See `THIRD-PARTY-NOTICES.md` and `ffmpeg/FFMPEG-LICENSE.txt` in the package.
