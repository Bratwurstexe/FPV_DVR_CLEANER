using System.Diagnostics;
using System.Globalization;
using Avalonia;
using Avalonia.Media.Imaging;
using Avalonia.Platform;
using Avalonia.Threading;

namespace FpvDvrCleaner.Desktop;

/// <summary>
/// Smooth preview playback: FFmpeg decodes the recording into raw frames at a
/// reduced size and this class shows them in time, dropping frames when the
/// machine falls behind. Only the picture is played; DVR audio, if any, is kept
/// in the export untouched.
///
/// A pair of clips (original and cleaned) is decoded by one FFmpeg process as
/// one wide frame, so both pictures are always the same instant: two separate
/// decoders would drift against each other by a few frames.
/// </summary>
public sealed class FramePlayer : IDisposable
{
    public const int PreviewWidth = 960;

    private readonly string _ffmpeg;
    private Process? _process;
    private Thread? _reader;
    private volatile bool _stopping;
    private WriteableBitmap? _bitmap;
    private WriteableBitmap? _second;
    private double _start;
    private double _fps;

    public FramePlayer(string ffmpeg) => _ffmpeg = ffmpeg;

    /// <summary>Called on the UI thread with the bitmap and the source time of the frame just shown.</summary>
    public event Action<WriteableBitmap, double>? FrameShown;
    /// <summary>Paired playback: both bitmaps and the time of the first clip.</summary>
    public event Action<WriteableBitmap, WriteableBitmap, double>? PairShown;
    /// <summary>Called on the UI thread when the decoder reached the end of the file.</summary>
    public event Action? Ended;

    public bool IsPlaying => _process is { HasExited: false } && !_stopping;

    private static string Number(double value) => value.ToString(CultureInfo.InvariantCulture);

    private WriteableBitmap Prepare(ref WriteableBitmap? bitmap, int width, int height)
    {
        if (bitmap == null || bitmap.PixelSize.Width != width || bitmap.PixelSize.Height != height)
        {
            bitmap?.Dispose();
            bitmap = new WriteableBitmap(new PixelSize(width, height), new Vector(96, 96), PixelFormat.Bgra8888, AlphaFormat.Opaque);
        }
        return bitmap;
    }

    private static (int Width, int Height) PreviewSize(int sourceWidth, int sourceHeight)
    {
        var width = Math.Min(PreviewWidth, Math.Max(2, sourceWidth / 2 * 2));
        var height = Math.Max(2, (int)Math.Round((double)sourceHeight * width / Math.Max(1, sourceWidth) / 2) * 2);
        return (width, height);
    }

    /// <summary>FFmpeg crop filter text for a picture box, or "" for the whole frame.</summary>
    public static string CropFilter((int X, int Y, int Width, int Height)? box) =>
        box is { } b && b.Width > 0 && b.Height > 0 ? $"crop={b.Width}:{b.Height}:{b.X}:{b.Y}," : "";

    public void Play(string path, double start, double fps, int sourceWidth, int sourceHeight,
                     (int X, int Y, int Width, int Height)? box = null)
    {
        Stop();
        _stopping = false;
        _start = Math.Max(0, start);
        _fps = fps is > 1 and <= 240 ? fps : 30;
        var (width, height) = PreviewSize(box?.Width ?? sourceWidth, box?.Height ?? sourceHeight);
        var bitmap = Prepare(ref _bitmap, width, height);
        _second = null;
        Launch(["-ss", Number(_start), "-i", path, "-an", "-sn",
                "-vf", $"{CropFilter(box)}fps={Number(_fps)},scale={width}:{height}:flags=fast_bilinear"],
            bitmap, null, width, height);
    }

    /// <summary>Original and cleaned clip from the given moments, decoded as one synchronised pair.</summary>
    public void PlayPair(string first, double firstStart, string second, double secondStart, double fps,
                         int sourceWidth, int sourceHeight,
                         (int X, int Y, int Width, int Height)? firstBox = null,
                         (int X, int Y, int Width, int Height)? secondBox = null)
    {
        Stop();
        _stopping = false;
        _start = Math.Max(0, firstStart);
        _fps = fps is > 1 and <= 240 ? fps : 30;
        var (width, height) = PreviewSize(firstBox?.Width ?? sourceWidth, firstBox?.Height ?? sourceHeight);
        var bitmap = Prepare(ref _bitmap, width, height);
        var other = Prepare(ref _second, width, height);
        var scale = $"fps={Number(_fps)},scale={width}:{height}:flags=fast_bilinear";
        Launch(["-ss", Number(_start), "-i", first, "-ss", Number(Math.Max(0, secondStart)), "-i", second,
                "-an", "-sn", "-filter_complex",
                $"[0:v]{CropFilter(firstBox)}{scale}[a];[1:v]{CropFilter(secondBox)}{scale}[b];[a][b]hstack=inputs=2:shortest=1"],
            bitmap, other, width, height);
    }

    private void Launch(string[] input, WriteableBitmap bitmap, WriteableBitmap? second, int width, int height)
    {
        var info = new ProcessStartInfo(_ffmpeg)
        {
            UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
        };
        foreach (var argument in new[] { "-hide_banner", "-loglevel", "error", "-nostdin" }.Concat(input)
                     .Concat(["-f", "rawvideo", "-pix_fmt", "bgra", "-"]))
            info.ArgumentList.Add(argument);
        _process = Process.Start(info) ?? throw new InvalidOperationException("Could not start FFmpeg for playback");
        _process.StandardError.ReadToEndAsync();
        var process = _process;
        _reader = new Thread(() => ReadFrames(process, bitmap, second, width, height)) { IsBackground = true, Name = "preview playback" };
        _reader.Start();
    }

    private void ReadFrames(Process process, WriteableBitmap bitmap, WriteableBitmap? second, int width, int height)
    {
        var rowBytes = width * 4;
        var frameBytes = rowBytes * height * (second == null ? 1 : 2);
        var buffer = new byte[frameBytes];
        var stream = process.StandardOutput.BaseStream;
        var clock = Stopwatch.StartNew();
        var frame = 0L;
        var pendingShow = 0;
        try
        {
            while (!_stopping)
            {
                var read = 0;
                while (read < frameBytes)
                {
                    var chunk = stream.Read(buffer, read, frameBytes - read);
                    if (chunk <= 0) { read = -1; break; }
                    read += chunk;
                }
                if (read < 0) break;
                var due = TimeSpan.FromSeconds(frame / _fps);
                var wait = due - clock.Elapsed;
                if (wait > TimeSpan.Zero) Thread.Sleep(wait);
                frame++;
                // Behind schedule: decode on without showing this frame, so time stays right.
                if (clock.Elapsed - due > TimeSpan.FromSeconds(2 / _fps) || Volatile.Read(ref pendingShow) > 1) continue;
                if (second == null)
                {
                    using var locked = bitmap.Lock();
                    System.Runtime.InteropServices.Marshal.Copy(buffer, 0, locked.Address, frameBytes);
                }
                else
                {
                    // The wide frame holds both pictures side by side; split it row by row.
                    using var left = bitmap.Lock();
                    using var right = second.Lock();
                    for (var y = 0; y < height; y++)
                    {
                        System.Runtime.InteropServices.Marshal.Copy(buffer, y * rowBytes * 2, left.Address + y * left.RowBytes, rowBytes);
                        System.Runtime.InteropServices.Marshal.Copy(buffer, y * rowBytes * 2 + rowBytes, right.Address + y * right.RowBytes, rowBytes);
                    }
                }
                var moment = _start + (frame - 1) / _fps;
                Interlocked.Increment(ref pendingShow);
                Dispatcher.UIThread.Post(() =>
                {
                    Interlocked.Decrement(ref pendingShow);
                    if (_stopping || process != _process) return;
                    if (second == null) FrameShown?.Invoke(bitmap, moment);
                    else PairShown?.Invoke(bitmap, second, moment);
                }, DispatcherPriority.Render);
            }
            if (!_stopping) Dispatcher.UIThread.Post(() => { if (process == _process) Ended?.Invoke(); });
        }
        catch (Exception)
        {
            // The process was killed or the pipe closed; playback simply stops.
        }
    }

    public void Stop()
    {
        _stopping = true;
        var process = _process;
        _process = null;
        if (process == null) return;
        try { if (!process.HasExited) process.Kill(entireProcessTree: true); }
        catch (InvalidOperationException) { }
        try { process.Dispose(); } catch (InvalidOperationException) { }
    }

    public void Dispose()
    {
        Stop();
        _bitmap?.Dispose();
        _second?.Dispose();
        _bitmap = null;
        _second = null;
    }
}
