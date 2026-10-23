# Development status

## Ready

- **Desktop app (.NET 8 / Avalonia)** with a four-step workflow: Import, Trim, Repair and Export, plus optional model training.
  - Import: drag and drop.
  - Trim: filmstrip editor with zoom, markers, quick play and keyboard shortcuts.
  - Repair: frame preview with a split comparison.
  - Export: live remaining time (hh:mm:ss) and cancel; partial files are removed.
  - Files given on the command line are added to the queue.
- **Analog colour repair** (`analog.py`): detects colour dropout and transient hue tears, then estimates chroma robustly from six motion-aligned neighbours. On local test cuts, frame-to-frame chroma change fell from 7.8 to 3.0 (Harburg) and from 9.4 to 3.6 (Bremerhaven), measured as the mean absolute Cr/Cb change.
- **HDZero breakup repair** (`digital.py`): blocks whose fine colour energy jumps against both neighbouring frames are rebuilt by flow interpolation between the nearest clean frames before and after; whole frames are rebuilt when most of them is broken. On two of the author's HDZero flights (26 765 frames) 36 frames were touched (0.13 %), all of them visible breakups. Fine specks that last several frames are reduced, not always removed.
- **OSD detection and removal** (`osd.py`): gradient coherence across the cut and in a sliding window; refill per region from the best-fitting neighbour, with motion chained over consecutive frames, and inpainting as the fallback.
  - Elements of the clip mask are removed only around the frames that show them, checked against the orientation of their steady edges. The stats screen after landing no longer smears the flight footage of the same cut.
  - Brief overlays must look like a level row of text. Straight scene edges that slide along themselves, for example in corridors, are no longer taken for warnings.
- **Speed** (`pipeline.py`): frames are processed in parallel on a shared thread pool, black side bars are skipped, the median of the fusion uses min/max networks, and a *Fast / Balanced / Best* setting picks neighbours, motion detail and encoder speed.
- **Multi-frame upscaling** (`upscale.py`): 1.5× and 2×.
- Frame rate taken from the stream's base rate. Transport streams with a misleading container average no longer get duplicated frames.
- Visual Studio Code: debug configurations for the app and the engine, build, release and test tasks.
- GitHub workflow that builds the .NET app for Windows, Linux and macOS and runs the engine tests.
- 23 automated tests (engine workflow, OSD, colour repair, HDZero breakups, model training, export recovery, parallel pipeline, side bars, recipes); legacy Qt tests are skipped when PySide6 is not installed.

## Open before a public release

1. **Product name.** Another project, kept in `X:\DVR Tools`, also uses the name "FPV DVR Cleaner". The GitHub repository `Bratwurstexe/FPV_DVR_CLEANER` now holds only this project (`main`), but a distinct product name would avoid confusion.
2. **Licence holder.** `LICENSE` names "FPV DVR Cleaner contributors". Replace this with the holder you want named.
3. **Footage in docs/media.** The GIFs and stills use the owner's own recordings, chosen from scenes without people.
4. **Packaging.** The build workflow packages the app, the frozen engine and FFmpeg (GPL build with x264) for Windows, Linux and macOS, and publishes them as a GitHub release from a `v*` tag. Tag `v0.1.0` predates this workflow, so a new tag is needed for the first release with packages.
5. **Speed.** Export is CPU-bound. On the test laptop (i7-13850HX), one minute of 720p60 analog footage with every option and 1.5× takes 6.6 minutes with *Balanced* (55 minutes before the parallel pipeline). The frame preview takes 13–20 s, mostly for finding the OSD over the cut. On this laptop threads stop scaling at about 4× because of its power limit; separate processes per segment were tried and were slower. GPU work is the next step.
6. **Legacy Qt prototype.** `window.py`, `cutroom.py`, `__main__.py`, `launch_flugbild.py`, `tests/test_queue.py`, the old `dist/` and `build/` output, the `.spec` files and `docs/ui-preview.png` / `docs/signal-preview.png` are no longer needed by the .NET app.
7. **Testing on Linux and macOS.** Builds are configured but have not been run.
