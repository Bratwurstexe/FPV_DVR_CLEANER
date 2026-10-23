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
- **GPU** (`gpu.py`): the full-resolution part of the fusion (warping, median, weighting, detail, colour conversion) runs through OpenCV's OpenCL path on any GPU that passes a self-test; flow stays on the CPU threads, at most two frames share the GPU. Results match the CPU path to one grey level. OpenCL's buffer pool is switched on: without it, allocating device memory cost 17× more than the arithmetic and serialised the threads. OpenCV's OpenCL path has no bicubic remap; for *Best* it is built from nine bilinear lookups (the two middle bicubic taps per axis share a sign and merge into one), within two grey levels of the CPU result.
  - Tried and dropped: hardware video encoders. On the test laptop Intel QSV made exports 10–25 % slower (shared power budget with the CPU, plus a 3 s encoder test per run); NVENC needs a newer NVIDIA driver than the bundled FFmpeg expects on many machines.
- **Multi-frame upscaling** (`upscale.py`): 1.5× and 2×.
- Frame rate taken from the stream's base rate. Transport streams with a misleading container average no longer get duplicated frames.
- Visual Studio Code: debug configurations for the app and the engine, build, release and test tasks.
- GitHub workflow that builds the .NET app for Windows, Linux and macOS and runs the engine tests.
- 23 automated tests (engine workflow, OSD, colour repair, HDZero breakups, model training, export recovery, parallel pipeline, side bars, recipes, GPU against CPU); the GPU comparison is skipped on machines without an OpenCL GPU.

## Decided

- **Name and licence:** the product stays "FPV DVR Cleaner"; `LICENSE` keeps "FPV DVR Cleaner contributors".
- **No code signing.** Windows SmartScreen and macOS Gatekeeper warn on first start; the release notes explain how to open the app anyway.
- **Footage in docs/media:** the owner's own recordings, chosen from scenes without people.
- **Packaging:** the build workflow packages the app, the frozen engine and FFmpeg (GPL build with x264) for Windows, Linux and macOS, and publishes them as a GitHub release from a `v*` tag.

## Open

1. **Speed.** On the test laptop (i7-13850HX), one minute of 720p60 analog footage with every option and 1.5× takes 6.6 minutes with *Balanced* on the CPU alone (55 minutes before the parallel pipeline); the GPU fusion saved about 10–30 % in first runs, but timings on this laptop vary by up to a third between runs with its temperature, so a clean chart needs interleaved, repeated runs. The frame preview takes 13–20 s, mostly for finding the OSD over the cut. On this laptop threads stop scaling at about 4× because of its power limit; separate processes per segment were tried and were slower.
2. **Testing on Linux and macOS, and on AMD, Intel and Apple GPUs.** Builds are configured but have not been run.
