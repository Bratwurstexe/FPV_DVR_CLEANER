# Development status

## Ready

- **Desktop app (.NET 8 / Avalonia)** with a four-step workflow: Import, Trim, Repair and Export, plus optional model training.
  - Import: drag and drop.
  - Trim: filmstrip editor with zoom, markers, quick play and keyboard shortcuts.
  - Repair: frame preview with a split comparison.
  - Export: live remaining time (hh:mm:ss) and cancel; partial files are removed.
  - Files given on the command line are added to the queue.
- **Analog colour repair** (`analog.py`): detects colour dropout and transient hue tears, then estimates chroma robustly from six motion-aligned neighbours. On local test cuts, frame-to-frame chroma change fell from 7.8 to 3.0 (Harburg) and from 9.4 to 3.6 (Bremerhaven), measured as the mean absolute Cr/Cb change.
- **OSD detection and removal** (`osd.py`): gradient coherence across the cut and in a sliding window; refill per region from the best-fitting neighbour, with motion chained over consecutive frames, and inpainting as the fallback.
- **Multi-frame upscaling** (`upscale.py`): 1.5× and 2×.
- Frame rate taken from the stream's base rate. Transport streams with a misleading container average no longer get duplicated frames.
- Visual Studio Code: debug configurations for the app and the engine, build, release and test tasks.
- GitHub workflow that builds the .NET app for Windows, Linux and macOS and runs the engine tests.
- 13 automated tests (engine workflow, OSD, colour repair, model training, export recovery); legacy Qt tests are skipped when PySide6 is not installed.

## Open before a public release

1. **Product name and repository.** The same name, "FPV DVR Cleaner", is used by another project. That project's local copy also points to the same GitHub repository URL (`Bratwurstexe/FPV_DVR_CLEANER`, branch `public-release`). Decide the name and the target repository before publishing.
2. **Licence holder.** `LICENSE` names "FPV DVR Cleaner contributors". Replace this with the holder you want named.
3. **Footage in docs/media.** The GIFs and screenshots use the owner's own recordings. Check that you are happy to publish them; one recording shows a person at a distance.
4. **Packaging.** Releases still need Python, NumPy, OpenCV and FFmpeg installed by the user. A bundled engine is on the roadmap.
5. **Speed.** Export is CPU-bound, at several seconds per second of video with all options on. The frame preview takes about 10 s.
6. **Legacy Qt prototype.** `window.py`, `cutroom.py`, `__main__.py`, `launch_flugbild.py`, `tests/test_queue.py`, the old `dist/` and `build/` output, the `.spec` files and `docs/ui-preview.png` / `docs/signal-preview.png` are no longer needed by the .NET app.
7. **Testing on Linux and macOS.** Builds are configured but have not been run.
