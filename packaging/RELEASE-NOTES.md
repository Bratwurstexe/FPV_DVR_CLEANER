Clean up analog and HDZero DVR recordings on your own computer: rainbow stripes, colour dropouts, HDZero breakups, noise and the OSD. Unpack and start; the app, the video engine and FFmpeg are included, so you don't need to install Python or FFmpeg.

## Download

| Your system | File | Start |
|---|---|---|
| **Windows** 10 / 11 (64-bit) | `FPV-DVR-Cleaner-…-windows-x64.zip` | `FpvDvrCleaner.Desktop.exe` |
| **macOS**, Apple Silicon (M1 and newer) | `FPV-DVR-Cleaner-…-macos-apple-silicon.zip` | `FPV DVR Cleaner.app` |
| **macOS**, Intel | `FPV-DVR-Cleaner-…-macos-intel.zip` | `FPV DVR Cleaner.app` |
| **Linux** (x64) | `FPV-DVR-Cleaner-…-linux-x64.tar.gz` | `./FpvDvrCleaner.Desktop` |
| **Source code** | *Source code (zip)* or *(tar.gz)* below | see the README |

`SHA256SUMS.txt` lists the checksum of every package.

## New in 0.4.1

**Important bug fixed: saving the export**
- Exports could fail with `[WinError 267] The directory name is invalid` ("Der Verzeichnisname ist ungültig"), no matter which destination folder was chosen. The video engine started FFmpeg in a folder that was missing from the packages. Exports work again, with or without a caption.

## New in 0.4.0

**CPU and GPU together**
- The heavy part of noise reduction and upscaling now runs on the graphics card: NVIDIA, AMD, Intel or Apple, through OpenCL. Optical flow, colour repair and OSD removal keep every CPU core busy at the same time.
- Nothing to set up: the app checks the GPU with a short self-test and uses it when the result matches the CPU. Without a usable GPU everything runs on the CPU as before, with the same result.
- In first tests on a laptop with an NVIDIA RTX 2000 Ada, exports with noise reduction and upscaling finished about 10–30 % sooner. How much you gain depends on your CPU and GPU.
- *Best* now also runs its sharper (bicubic) frame alignment on the GPU.

**Safer HDZero repair**
- Broken blocks are rebuilt only from frames that are free of confetti specks there, too.
- White OSD text inside a partly broken frame is kept, so the stats screen no longer flickers on a weak link.

**Tidied up**
- The old Qt prototype was removed from the source code. The app is unchanged.

## New in 0.3.0

**HDZero breakups rebuilt**
- When the digital link drops packets, parts of a frame or the whole frame turn into coloured confetti. These frames are now rebuilt from the clean frames before and after them, aligned with optical flow.
- Only real breakups are touched: on two test flights, about one frame in 750. The OSD, text that just appeared and colourful scenery are left alone.
- On by default for HDZero: choose the *HDZero* signal on the Repair page. **Block repair** sets how eagerly it repairs; 0 turns it off.
- The README shows what is detected and how it is rebuilt.

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
