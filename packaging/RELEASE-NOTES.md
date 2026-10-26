> [!IMPORTANT]
> **Want to stay up to date? Download the launcher.** It always installs the newest FPV DVR Cleaner: pick your system, and it downloads the latest release, checks it, deletes the old version and starts the app.
> **[⬇ Windows](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/latest/download/FPV-DVR-Cleaner-Launcher-windows-x64.exe)** · **[macOS Apple Silicon](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/latest/download/FPV-DVR-Cleaner-Launcher-macos-apple-silicon.zip)** · **[macOS Intel](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/latest/download/FPV-DVR-Cleaner-Launcher-macos-intel.zip)** · **[Linux](https://github.com/Bratwurstexe/FPV_DVR_CLEANER/releases/latest/download/FPV-DVR-Cleaner-Launcher-linux-x64.tar.gz)**

Clean up analog and HDZero DVR recordings on your own computer: rainbow stripes, colour dropouts, HDZero breakups, noise and the OSD. Unpack and start; the app, the video engine and FFmpeg are included, so you don't need to install Python or FFmpeg.

## Download

| Your system | File | Start |
|---|---|---|
| **Windows** 10 / 11 (64-bit) | `FPV-DVR-Cleaner-…-windows-x64.zip` | `FpvDvrCleaner.Desktop.exe` |
| **macOS**, Apple Silicon (M1 and newer) | `FPV-DVR-Cleaner-…-macos-apple-silicon.zip` | `FPV DVR Cleaner.app` |
| **macOS**, Intel | `FPV-DVR-Cleaner-…-macos-intel.zip` | `FPV DVR Cleaner.app` |
| **Linux** (x64) | `FPV-DVR-Cleaner-…-linux-x64.AppImage` | `chmod +x` it, then start it |
| **Linux** (x64), plain folder | `FPV-DVR-Cleaner-…-linux-x64.tar.gz` | `./FpvDvrCleaner.Desktop` |
| **Launcher** (recommended), Windows | `FPV-DVR-Cleaner-Launcher-windows-x64.exe` | double-click it |
| **Launcher**, macOS | `FPV-DVR-Cleaner-Launcher-macos-….zip` | `FPV DVR Cleaner Launcher.app` |
| **Launcher**, Linux | `FPV-DVR-Cleaner-Launcher-linux-x64.tar.gz` | `./FPV-DVR-Cleaner-Launcher` |
| **Source code** | *Source code (zip)* or *(tar.gz)* below | see the README |

`SHA256SUMS.txt` lists the checksum of every package.

## New in 0.7.0

**Share-ready before/after videos**
- After an export, press **Share** in the result view: one click on *Make video* turns the clip into a before/after video to post. The original and the cleaned clip are paired frame by frame, removed parts of the trim included.
- **16:9 for YouTube** (1920×1080): side by side with *ORIGINAL* and *CLEANED* labels, a wipe with an orange split line that sweeps across, or a flip that switches between the two every two seconds.
- **9:16 for Shorts, Reels and TikTok** (1080×1920): original above cleaned, or one full-screen cleaned picture under a moving wipe that follows the action.
- **Slow motion on the biggest repairs:** frames where the repair changed the most, such as an HDZero breakup or a rainbow tear, are slowed to ×0.25 and frozen for a second, with a small tag.
- **Best 15, 30 or 60 seconds**, chosen around the strongest repairs, or the whole clip. Sound is kept for 16:9 and left out for 9:16, where music is usually added on the platform; both can be changed.
- A small *Cleaned with FPV DVR Cleaner* in the corner, which you can switch off. H.264 MP4 at 30 or 60 fps, saved next to the cleaned file as `…-share.mp4` or `…-short.mp4`. Your choices are remembered.

**Cleaner buttons**
- The zoom, transport and queue buttons use drawn icons, centred exactly; disabled queue arrows fade instead of changing colour.

## New in 0.6.0

**Pick the file format**
- The Export page has a file format choice: **MP4 · H.264** (plays everywhere, as before), **MP4 · H.265** (smallest files), **MOV · ProRes 422** (for video editors), **MKV · H.264** (robust container) and **WebM · VP9 + Opus** (for the web). Each option says what it is good for, and your choice is remembered.
- File names, numbered copies, joined clips and *Auto refine* follow the chosen format. ProRes ignores the quality and size settings, so they are greyed out for it.
- WebM files can be imported, so a WebM export can be checked or refined again.

**A finished look, if you want one**
- New *Look* card on the Repair page: colour grade, glow, vignette, film grain and lens fringe, each with a slider, or one of the presets *Clean*, *Cinematic*, *Punchy* and *Retro FPV*.
- It is off by default and then changes nothing. When it is on, it is the last step after every repair, the frame preview shows it, and a second split compares the frame with and without it.
- The grain follows the frame number, so the same cut exported twice gives the same file. Saved presets and your last settings include the look.

**Sister project**
- Restoring VHS tapes or old 8 mm / 16 mm film is moving into its own app, [Analog Repair](https://github.com/Bratwurstexe/ANALOG_REPAIR).

## New: the launcher

- **One small program that keeps FPV DVR Cleaner up to date** on Windows, macOS and Linux. It detects your system and preselects it; you can still pick another one, for example to fetch the Mac version on a PC.
- It downloads the newest release, checks its SHA-256 checksum, unpacks it next to itself (or into a folder you choose and that it remembers), deletes the old version and any leftover downloads, and starts the app.
- Safe by design: the old version stays until the new one is complete; a running app, a full disk, a read-only folder or a broken connection stop the update with a clear message and nothing changed. Downloads resume after a dropped connection, and an install cut off by a crash or power loss is undone at the next start.

## New in 0.5.0

**Trim like in a video editor**
- The trim page is a real timeline: time ruler, filmstrip track, audio waveform, playhead with a grip. Split the recording at the playhead (`S`), click a part and press `Delete`: the parts before and after close up and a thin cut edge marks the join. Drag the fine `[` `]` edges to trim or to bring footage back, with snapping to cuts, bookmarks and the ruler.
- Hover over the timeline to scrub, Ctrl+wheel to zoom around the pointer, wheel to scroll, bookmarks (`B`), undo and redo (Ctrl+Z / Ctrl+Y), jump between cut edges (Page Up / Page Down).
- Playback decodes the real video now, so the preview runs smoothly instead of stepping through thumbnails. It skips the removed parts.
- The export renders every kept part with the same settings and joins them without re-encoding, so the result is one file.
- The editor grows with the window; F11 switches to full screen.

**Watch the result, and know how good it is**
- After the export, original and cleaned clip play side by side or under a wipe, in sync, with the removed parts skipped.
- Both clips come from one decoder, so they are frame-accurate to each other; nothing drifts at the wipe line.
- A measurement of grain, colour noise and streaks shows what was gained, how much improvement is still possible without any loss, and what a second pass would cost in detail. *Try other settings* re-exports the original (lossless); *Refine the cleaned file* queues the result for a second pass and warns about the re-encoding loss first.
- Both buttons pick the settings for the next round from the measurement: whatever artefact is left raises the control that fights it; a second pass also turns off what was already done (OSD removal, upscaling) and keeps the encode near lossless.
- *Auto refine* does it in one click: settings from the measurement, the original exported again as a new file (nothing is overwritten, exports are numbered).
- The split line of the comparison views is dragged in the picture itself, on the Repair page and in the finished-clip view.

**Cleaner on one screen**
- Every page fits the window without scrolling at the default size: the trim editor is a dark, edge-to-edge workspace whose preview takes all spare height, the Repair page has three compact cards, and the export settings fold away once a result is shown so the video gets the room, also in full screen (F11).
- Buttons, pills and inputs are smaller and lighter throughout.
- While the engine works (reading clips, rendering a frame, the OSD search, export, training) the page dims and blurs behind one centred progress card, with a cancel button where it applies. The progress bar has a small quad flying at its tip.
- Previews and the comparison views show the 4:3 picture only; the black side bars of 16:9 recordings are cropped away, and the wipe line runs inside the picture.

**Repair around the cut**
- Half a second of footage before and after each kept part is decoded as context, so the first and last frames of a part are repaired from real neighbours instead of from one side only.

**Quality of life**
- Presets for the repair settings, four built in; your last settings come back at the next start.
- The cut of every recording is remembered, so a file you add again keeps its parts and bookmarks.
- Export options: file name suffix, join all clips into one video, open the folder when done. The app comes back to the front when an export finishes.
- Queue: move clips up and down, add a copy of a recording with its own cut.
- The window starts centred on the screen; the square logo in the corner is gone.

**OSD removal**
- The refill of each overlay follows the brightness gradient of its surroundings (sky, vignette) instead of using one flat offset, and the seam is feathered only outside the graphic, so no edge of the text shines through.
- Where no neighbouring frame can fill a region, the spatial fill now carries the surrounding gradients into the hole (Navier-Stokes) instead of the fast-marching method, which left faint streaks that flickered on analog footage. In tests on analog footage the fill flickers about 25 % less.

## New in 0.4.2

**Export folder remembered**
- The destination folder of the export (and the recordings folder for model training) is saved when you choose it and is back the next time you start the app. The folder picker also opens there. Settings live in `settings.json` next to the trained models, under your user's application data folder.

**Export fixed for good** (issues #1 and #2)
- 0.4.1 already fixed the failing export (`WinError 267` on Windows, `Errno 2 … flugbild/assets` on Linux). The engine now also finds its bundled font in every package layout and reports a clear message instead of a path error if a file is missing. Every build checks that the files are in the package and runs a real export, with and without a caption, through the frozen engine before a release is published.

**Linux packaging** (issue #2)
- The Linux build is now also published as an **AppImage**: make it executable and start it, nothing to unpack.
- The desktop app is one executable on Linux. The hundreds of `.dll` files are gone: they were .NET assemblies, not Windows libraries, but they had no business confusing anyone. Only two native `.so` libraries remain next to it.
- *Open output folder* now works on Linux and macOS, not just on Windows.

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
