# Third-party notices

The application source in this repository is MIT licensed (see [LICENSE](LICENSE)).
It contains no source code, models, images or other assets taken from other
projects. It *uses* the open-source components below. Their own licences apply
to them; the MIT licence of this project does not replace them.

## Desktop application (.NET 8 / Avalonia)

Shipped inside a published desktop build.

| Component | Version | Licence | Project |
|---|---|---|---|
| Avalonia, Avalonia.Desktop, Avalonia.Themes.Fluent, Avalonia.Skia, Avalonia.Win32, Avalonia.X11, Avalonia.Native, Avalonia.FreeDesktop, Avalonia.HarfBuzz, Avalonia.Remote.Protocol | 12.1.4 | MIT | https://github.com/AvaloniaUI/Avalonia |
| Avalonia.Fonts.Inter (packages the Inter typeface) | 12.1.4 | MIT (package), SIL OFL 1.1 (font) | https://github.com/AvaloniaUI/Avalonia, https://github.com/rsms/inter |
| Avalonia.Angle.Windows.Natives (ANGLE) | 2.1 | BSD-3-Clause | https://chromium.googlesource.com/angle/angle |
| SkiaSharp (with native Skia) | 3.119 | MIT (SkiaSharp), BSD-3-Clause (Skia) | https://github.com/mono/SkiaSharp |
| HarfBuzzSharp (with native HarfBuzz) | 8.3 | MIT (HarfBuzzSharp), "Old MIT" (HarfBuzz) | https://github.com/mono/SkiaSharp, https://github.com/harfbuzz/harfbuzz |
| MicroCom.Runtime | 0.11 | MIT | https://github.com/kekekeks/MicroCom |
| Tmds.DBus.Protocol | 0.94 | MIT | https://github.com/tmds/Tmds.DBus |
| Microsoft.Extensions.*, System.IO.Pipelines, Microsoft.IO.RecyclableMemoryStream | 8.0 / 3.0 | MIT | https://github.com/dotnet/runtime, https://github.com/microsoft/Microsoft.IO.RecyclableMemoryStream |
| .NET runtime (self-contained builds only) | 8.0 | MIT | https://github.com/dotnet/runtime |

Development only, not shipped in Release builds:

| Component | Licence | Note |
|---|---|---|
| AvaloniaUI.DiagnosticsSupport | see https://avaloniaui.net | Referenced for Debug builds only (`PrivateAssets` in Release). |
| Avalonia.BuildServices | MIT | Build-time package pulled in by Avalonia. |

## Video engine (Python)

Installed by the user with `pip`; not bundled in this repository.

| Component | Licence | Project |
|---|---|---|
| Python | PSF License | https://www.python.org |
| NumPy | BSD-3-Clause | https://numpy.org |
| OpenCV (opencv-python-headless) | Apache-2.0 (OpenCV), MIT (packaging); the wheels bundle further libraries such as FFmpeg under LGPL-2.1 whose licence files ship inside the wheel | https://github.com/opencv/opencv-python |
| Inter typeface (`src/flugbild/assets/Inter.ttf`, used for burned-in captions) | SIL OFL 1.1, see [INTER-OFL.txt](src/flugbild/assets/INTER-OFL.txt) | https://github.com/rsms/inter |

## External tools

| Component | Licence | Note |
|---|---|---|
| FFmpeg / ffprobe | LGPL-2.1-or-later or GPL, depending on how the build was configured | Called as a separate program; must be installed by the user and is not distributed with this project. https://ffmpeg.org/legal.html |

## Legacy prototype

The older Qt prototype (`src/flugbild/window.py`, `cutroom.py`) uses PySide6
(LGPL-3.0 / GPL) and was packaged with PyInstaller (GPL-2.0 with bootloader
exception). Neither is needed by the .NET desktop app.

## Distributing binaries

A published desktop build contains the .NET components listed above. Include
this file and the licence texts of those packages (NuGet ships them in each
package) with any binary release. The Python engine and FFmpeg are installed
separately by the user; if a future release bundles them, add the licence files
of the exact wheels and FFmpeg build that are bundled.
