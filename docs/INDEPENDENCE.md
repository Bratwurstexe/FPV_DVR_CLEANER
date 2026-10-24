# Implementation provenance

This project is a Python video engine (`src/flugbild/`) with a .NET 8 /
Avalonia desktop application (`desktop/`). An older Qt prototype
(`src/flugbild/window.py`, `cutroom.py`) is still in the tree.

## Background

Before this rebuild, the project owner examined an earlier, separately
developed DVR application that addresses the same problem. Its examination
included reverse engineering, and similarity concerns were discussed with its
developer. Because of that prior exposure this is **not a strict clean-room
project**. Independence therefore rests on documented design decisions and on
comparisons such as the one below, not on a clean-room process.

## Comparison of 22 October 2026

The repository was compared with a local copy of the reference application, a
Rust program. The comparison covered:

- source files, tool scripts and model metadata
- identifiers, string literals and numeric constants, intersected across both trees
- JSON keys and data structures
- processing approaches
- interface texts, layout, colour palette and icons
- binary assets, by checksum

**Result:**

- No copied or translated code was found, and no shared assets, models or images.
- Shared tokens were generic words, FFmpeg arguments and library names.
- Data structures differ: German-named dataclasses (`Arbeitsrezept`, `Signalsignatur`, `VideoKenndaten`), a JSON-line bridge and a NumPy MLP profile format, compared with the reference configuration and model schemas.

**Three detectors were structurally close** to reference functions, even
though their code and constants differed. They were redesigned around
different signal properties:

| Area | Earlier form (removed) | Current approach |
|---|---|---|
| OSD detection | Temporal median, stability, Sobel threshold, white/black glyph map | Gradient coherence: per channel, the length of the summed gradient vectors divided by the summed gradient lengths. Brief overlays use the same statistic in a sliding window, gated on scene motion. Grouping is done by line dilation and closed-contour filling. |
| OSD refill | Per pixel: median, inliers, mean, minimum count | Per region: the neighbour with the best rim fit is pasted with its brightness offset corrected. Motion is chained over consecutive frame pairs. |
| Colour dropout | Saturation relative to an upper percentile of the window | Absolute per-row chroma level; no window statistic |
| Rainbow stripes | Saturation "ridge" against rows above and below | Vertical hue jumps in saturated chroma, counted only when they are transient across aligned neighbours |

Interface wording that resembled the reference ("drag the … handles",
"Before / after", "Add videos", a step called "Clean & export") was replaced.
The workflow steps are Import, Trim, Repair and Export. The palette
(orange #A64600 on charcoal) and the icon differ from the reference.

**Similarities at the level of the problem remain and are expected.** Analog
chroma is repaired from motion-aligned neighbouring frames, clips are trimmed
on a thumbnail timeline, and results are compared with a split view. These
follow from the task and from common video-editing conventions; they are not
taken from the reference implementation.

## Changes of 24 October 2026

Own work, written from scratch for 0.5.0, with no reference to the other
application:

- Trim editor: kept parts of a recording as a sorted list of source-time
  segments, drawn on an output-time axis (`TimelineView.cs`); the export
  renders each part with the existing pipeline and joins the parts with the
  FFmpeg concat demuxer (`segments.py`).
- Smooth preview playback by decoding raw frames through FFmpeg into a
  `WriteableBitmap` (`FramePlayer.cs`), one process for a synchronised pair.
- Result assessment: median artefact levels (luma high-pass, chroma high-pass,
  row-spike ratio, block-seam ratio) of sampled frames, compared with a
  median-filtered floor (`assess.py`). Heuristic re-encode loss from CRF.
- OSD refill: plane-fitted brightness offset on the region rim, feather
  outside the graphic only, Navier-Stokes spatial fallback (`osd.py`).
- Temporal pipeline decodes half a second of context around each cut
  (`restoration.py`).

## Rules for further work

- Develop from this project's own requirements, its own footage, public
  library documentation and measured results.
- Do not bring in reference code, pseudocode, constants, assets, model files
  or reverse-engineering notes.
- Before publishing, repeat the comparison whenever detection or repair logic
  changes substantially.

## Licence

The application source is MIT licensed. Bundled and used third-party
components keep their own licences; see
[THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md).

This record is not legal advice. Information obtained by decompilation is
subject to statutory limits; see the German Copyright Act,
[§ 69a](https://www.gesetze-im-internet.de/urhg/__69a.html) and
[§ 69e](https://www.gesetze-im-internet.de/urhg/__69e.html). The product name
also needs a decision before publication; see the open points in
[STATUS.md](STATUS.md).
