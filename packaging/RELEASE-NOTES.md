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

## New in 0.7.1

**Share-ready before/after videos**
- After an export, press **Share** in the result view: one click on *Make video* turns the clip into a before/after video to post. The original and the cleaned clip are paired frame by frame, removed parts of the trim included.
- **16:9 for YouTube** (1920×1080): side by side with *ORIGINAL* and *CLEANED* labels, a wipe with an orange split line that sweeps across, or a flip that switches between the two every two seconds.
- **9:16 for Shorts, Reels and TikTok** (1080×1920): original above cleaned, or one full-screen cleaned picture under a moving wipe that follows the action.
- **Slow motion on the biggest repairs:** frames where the repair changed the most, such as an HDZero breakup or a rainbow tear, are slowed to ×0.25 and frozen for a second, with a small tag.
- **Best 15, 30 or 60 seconds**, chosen around the strongest repairs, or the whole clip. Sound is kept for 16:9 and left out for 9:16, where music is usually added on the platform; both can be changed.
- A small *Cleaned with FPV DVR Cleaner* in the corner, which you can switch off. H.264 MP4 at 30 or 60 fps, saved next to the cleaned file as `…-share.mp4` or `…-short.mp4`. Your choices are remembered.

**Cleaner buttons**
- The zoom, transport and queue buttons use drawn icons, centred exactly; disabled queue arrows fade instead of changing colour.

**Repair in two simple questions**
- The Repair page asks *Signal* (Analog or HDZero, preselected from the recording when it is clear) and *How bad is it?* (Light, Normal, Strong), with one plain line about what that does. *Best quality* switches on 1080p upscaling and the Best speed in one go.
- **OSD: Keep or Remove.** With Remove, **Fix by hand…** opens a mask editor over the page: the frame large with the found OSD in orange. Paint or box in what the search missed (small icons, REC) and keep what it took by mistake; undo, redo and *Reset to automatic*. The corrections are kept per recording and used in the preview and the export.
- **My presets** is a short list of your own settings with Rename, Update with current settings and Delete; presets from earlier versions are in it.
- Everything else (output size, speed, each repair as a slider, the trained model) is under **Advanced**, with a *Show* button per repair that renders the frame without and with only that effect.
- The big before/after frame updates on its own shortly after a change. The page fits the window without scrolling, down to 1080×700.
- One slim slider style in the whole app.

**Export by purpose**
- Choose what the video is for: *Share & play anywhere* (MP4 H.264), *Smallest file* (MP4 H.265), *For video editing* (MOV ProRes 422) or *For the web* (WebM). Quality, size limit, caption, file name, joining and MKV are under *More options*.
- Without a chosen folder the files go to a *Cleaned* folder next to the first clip. Progress, remaining time and Cancel sit right under the big Export button.

Earlier versions: see the [changelog](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/blob/main/CHANGELOG.md).

## First start

- **Windows:** SmartScreen may warn about an unknown publisher. Choose *More info → Run anyway*.
- **macOS:** the app is not notarised. Right-click it and choose *Open*, or run `xattr -cr "FPV DVR Cleaner.app"` in Terminal.
- **Linux:** if the file manager does not start it, run `chmod +x FpvDvrCleaner.Desktop` once.

All processing stays on your computer. The application code is MIT licensed. The bundled components keep their own licences: Avalonia (MIT), NumPy (BSD), OpenCV (Apache-2.0) and FFmpeg (GPL, a separate program in the `ffmpeg` folder; source at https://ffmpeg.org/download.html). See `THIRD-PARTY-NOTICES.md` and `ffmpeg/FFMPEG-LICENSE.txt` in the package.
