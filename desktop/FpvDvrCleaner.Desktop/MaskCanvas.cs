using System.Runtime.InteropServices;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Platform;

namespace FpvDvrCleaner.Desktop;

/// <summary>
/// The picture of the OSD mask editor: one frame, large, with the automatic OSD
/// mask tinted orange. The user paints corrections on top, in source-frame
/// pixels: "remove" (always painted out) and "keep" (never painted out), with a
/// round brush or a box. Every stroke can be undone.
/// </summary>
public sealed class MaskCanvas : Control
{
    public enum Tool { BrushAdd, BrushKeep, BoxAdd, BoxKeep }

    /// <summary>Correction values per pixel, as stored by the engine (handmask.py): 255 remove, 128 keep.</summary>
    public const byte None = 0, Add = 1, KeepMark = 2;

    private int _width, _height;
    private PixelRect _view;
    private byte[] _frame = [];   // BGRA of the whole frame
    private byte[] _auto = [];    // 1 where the automatic search found OSD
    private byte[] _layer = [];   // None, Add or KeepMark
    private byte[] _shownPixels = [];
    private WriteableBitmap? _shown;
    private readonly Stack<byte[]> _undo = new(), _redo = new();
    private Point? _pointer;
    private Point? _last;
    private Point? _boxStart;
    private bool _drawing;

    public Tool Current { get; set; } = Tool.BrushAdd;

    /// <summary>Brush diameter in source-frame pixels.</summary>
    public double BrushSize { get; set; } = 14;

    public event Action? Changed;
    public event Action<double>? BrushSizeRequested;

    public bool CanUndo => _undo.Count > 0;
    public bool CanRedo => _redo.Count > 0;
    public bool HasCorrections => Array.IndexOf(_layer, Add) >= 0 || Array.IndexOf(_layer, KeepMark) >= 0;
    public bool HasPicture => _shown != null;
    public int FrameWidth => _width;
    public int FrameHeight => _height;

    public MaskCanvas()
    {
        Focusable = true;
        ClipToBounds = true;
        Cursor = new Cursor(StandardCursorType.Cross);
    }

    /// <summary>Frame and automatic mask (both of the whole frame); ``view`` is the part shown, the picture inside the bars.</summary>
    public void Load(byte[] frameBgra, byte[] automatic, byte[]? corrections, int width, int height, PixelRect view)
    {
        _width = width; _height = height;
        _view = view.Width > 0 && view.Height > 0 ? view : new PixelRect(0, 0, width, height);
        _frame = frameBgra;
        _auto = automatic;
        _layer = corrections is { } given && given.Length == width * height ? given : new byte[width * height];
        _undo.Clear(); _redo.Clear();
        _shownPixels = new byte[_view.Width * _view.Height * 4];
        _shown = new WriteableBitmap(new PixelSize(_view.Width, _view.Height), new Vector(96, 96), PixelFormat.Bgra8888, AlphaFormat.Unpremul);
        Compose(_view);
        Changed?.Invoke();
    }

    public void Unload()
    {
        _shown?.Dispose(); _shown = null;
        _frame = _auto = _layer = _shownPixels = [];
        _undo.Clear(); _redo.Clear();
        InvalidateVisual();
    }

    /// <summary>The corrections, one byte per pixel of the whole frame.</summary>
    public byte[] Corrections => _layer;

    /// <summary>Share of the shown picture removed by the automatic search alone, and with the corrections, in percent.</summary>
    public (double Automatic, double Corrected) Coverage()
    {
        if (_shown == null) return (0, 0);
        long automatic = 0, corrected = 0;
        for (var y = _view.Y; y < _view.Bottom; y++)
            for (var x = _view.X; x < _view.Right; x++)
            {
                var i = y * _width + x;
                if (_auto[i] != 0) automatic++;
                if (Removed(i)) corrected++;
            }
        double area = Math.Max(1, (long)_view.Width * _view.Height);
        return (100 * automatic / area, 100 * corrected / area);
    }

    private bool Removed(int i) => _layer[i] == Add || (_auto[i] != 0 && _layer[i] != KeepMark);

    public void Reset()
    {
        if (!HasCorrections) return;
        PushUndo();
        Array.Clear(_layer);
        Compose(_view);
        Changed?.Invoke();
    }

    public void Undo() => Step(_undo, _redo);
    public void Redo() => Step(_redo, _undo);

    private void Step(Stack<byte[]> from, Stack<byte[]> to)
    {
        if (from.Count == 0) return;
        to.Push((byte[])_layer.Clone());
        _layer = from.Pop();
        Compose(_view);
        Changed?.Invoke();
    }

    private void PushUndo()
    {
        _undo.Push((byte[])_layer.Clone());
        // Each step holds one byte per pixel; 40 steps of a 720p frame are about 37 MB.
        if (_undo.Count > 40)
        {
            var kept = _undo.Take(40).Reverse().ToList();
            _undo.Clear();
            foreach (var item in kept) _undo.Push(item);
        }
        _redo.Clear();
    }

    // ---- drawing

    private Rect Shown()
    {
        var aspect = _view.Height > 0 ? (double)_view.Width / _view.Height : 4.0 / 3;
        var width = Math.Min(Bounds.Width, Bounds.Height * aspect);
        var height = width / aspect;
        return new Rect((Bounds.Width - width) / 2, (Bounds.Height - height) / 2, width, height);
    }

    private double Scale => _view.Width > 0 ? Shown().Width / _view.Width : 1;

    private Point ToFrame(Point point)
    {
        var shown = Shown();
        return new Point(_view.X + (point.X - shown.X) / Math.Max(1e-6, shown.Width) * _view.Width,
                         _view.Y + (point.Y - shown.Y) / Math.Max(1e-6, shown.Height) * _view.Height);
    }

    public override void Render(DrawingContext context)
    {
        context.FillRectangle(new SolidColorBrush(Color.Parse("#101418")), new Rect(Bounds.Size));
        if (_shown == null) return;
        var shown = Shown();
        context.DrawImage(_shown, new Rect(0, 0, _view.Width, _view.Height), shown);
        var outline = new Pen(Brushes.White, 1.5);
        var shadow = new Pen(new SolidColorBrush(Color.Parse("#A0000000")), 3);
        if (_boxStart is { } start && _pointer is { } end && Current is Tool.BoxAdd or Tool.BoxKeep)
        {
            var box = new Rect(start, end).Normalize();
            context.DrawRectangle(null, shadow, box);
            context.DrawRectangle(null, new Pen(new SolidColorBrush(Color.Parse(Current == Tool.BoxAdd ? "#F08A3A" : "#2D9CDB")), 1.5,
                new DashStyle([4, 3], 0)), box);
        }
        else if (_pointer is { } at && Current is Tool.BrushAdd or Tool.BrushKeep && shown.Contains(at))
        {
            var radius = Math.Max(2, BrushSize * Scale / 2);
            context.DrawEllipse(null, shadow, at, radius, radius);
            context.DrawEllipse(null, outline, at, radius, radius);
        }
    }

    /// <summary>Tints the given part of the frame: orange where it is removed, blue where it is kept by hand.</summary>
    private void Compose(PixelRect area)
    {
        if (_shown == null) return;
        var clip = area.Intersect(_view);
        if (clip.Width <= 0 || clip.Height <= 0) { InvalidateVisual(); return; }
        for (var y = clip.Y; y < clip.Bottom; y++)
        {
            var row = (y - _view.Y) * _view.Width;
            for (var x = clip.X; x < clip.Right; x++)
            {
                var i = y * _width + x;
                var source = i * 4;
                var target = (row + x - _view.X) * 4;
                int b = _frame[source], g = _frame[source + 1], r = _frame[source + 2];
                if (Removed(i)) { b = (b * 35 + 27 * 65) / 100; g = (g * 35 + 119 * 65) / 100; r = (r * 35 + 239 * 65) / 100; }
                else if (_layer[i] == KeepMark) { b = (b * 60 + 219 * 40) / 100; g = (g * 60 + 156 * 40) / 100; r = (r * 60 + 45 * 40) / 100; }
                _shownPixels[target] = (byte)b; _shownPixels[target + 1] = (byte)g; _shownPixels[target + 2] = (byte)r; _shownPixels[target + 3] = 255;
            }
        }
        using (var buffer = _shown.Lock())
        {
            var rowBytes = _view.Width * 4;
            for (var y = 0; y < _view.Height; y++)
                Marshal.Copy(_shownPixels, y * rowBytes, buffer.Address + y * buffer.RowBytes, rowBytes);
        }
        InvalidateVisual();
    }

    private PixelRect Stamp(Point centre, byte value)
    {
        var radius = Math.Max(1, BrushSize / 2);
        var left = Math.Max(_view.X, (int)Math.Floor(centre.X - radius));
        var right = Math.Min(_view.Right, (int)Math.Ceiling(centre.X + radius) + 1);
        var top = Math.Max(_view.Y, (int)Math.Floor(centre.Y - radius));
        var bottom = Math.Min(_view.Bottom, (int)Math.Ceiling(centre.Y + radius) + 1);
        for (var y = top; y < bottom; y++)
            for (var x = left; x < right; x++)
            {
                var dx = x + 0.5 - centre.X; var dy = y + 0.5 - centre.Y;
                if (dx * dx + dy * dy <= radius * radius) _layer[y * _width + x] = value;
            }
        return new PixelRect(left, top, Math.Max(0, right - left), Math.Max(0, bottom - top));
    }

    private void StrokeTo(Point from, Point to, byte value)
    {
        var distance = Math.Sqrt(Math.Pow(to.X - from.X, 2) + Math.Pow(to.Y - from.Y, 2));
        var steps = Math.Max(1, (int)Math.Ceiling(distance / Math.Max(1, BrushSize / 4)));
        var dirty = default(PixelRect);
        for (var step = 1; step <= steps; step++)
        {
            var t = (double)step / steps;
            var rect = Stamp(new Point(from.X + (to.X - from.X) * t, from.Y + (to.Y - from.Y) * t), value);
            dirty = dirty.Width == 0 ? rect : dirty.Union(rect);
        }
        Compose(dirty);
    }

    private void FillBox(Point a, Point b, byte value)
    {
        var start = ToFrame(a); var end = ToFrame(b);
        var left = Math.Max(_view.X, (int)Math.Floor(Math.Min(start.X, end.X)));
        var right = Math.Min(_view.Right, (int)Math.Ceiling(Math.Max(start.X, end.X)));
        var top = Math.Max(_view.Y, (int)Math.Floor(Math.Min(start.Y, end.Y)));
        var bottom = Math.Min(_view.Bottom, (int)Math.Ceiling(Math.Max(start.Y, end.Y)));
        if (right <= left || bottom <= top) return;
        for (var y = top; y < bottom; y++)
            Array.Fill(_layer, value, y * _width + left, right - left);
        Compose(new PixelRect(left, top, right - left, bottom - top));
    }

    // ---- pointer

    protected override void OnPointerPressed(PointerPressedEventArgs e)
    {
        base.OnPointerPressed(e);
        if (_shown == null || !e.GetCurrentPoint(this).Properties.IsLeftButtonPressed) return;
        Focus();
        var at = e.GetPosition(this);
        _pointer = at;
        PushUndo();
        _drawing = true;
        e.Pointer.Capture(this);
        if (Current is Tool.BoxAdd or Tool.BoxKeep) _boxStart = at;
        else
        {
            var frame = ToFrame(at);
            _last = frame;
            Compose(Stamp(frame, Current == Tool.BrushAdd ? Add : KeepMark));
        }
        e.Handled = true;
    }

    protected override void OnPointerMoved(PointerEventArgs e)
    {
        base.OnPointerMoved(e);
        var at = e.GetPosition(this);
        _pointer = at;
        if (_drawing && _last is { } last && Current is Tool.BrushAdd or Tool.BrushKeep)
        {
            var frame = ToFrame(at);
            StrokeTo(last, frame, Current == Tool.BrushAdd ? Add : KeepMark);
            _last = frame;
        }
        else InvalidateVisual();
    }

    protected override void OnPointerReleased(PointerReleasedEventArgs e)
    {
        base.OnPointerReleased(e);
        if (!_drawing) return;
        _drawing = false;
        e.Pointer.Capture(null);
        if (_boxStart is { } start && Current is Tool.BoxAdd or Tool.BoxKeep)
            FillBox(start, e.GetPosition(this), Current == Tool.BoxAdd ? Add : KeepMark);
        _boxStart = null; _last = null;
        InvalidateVisual();
        Changed?.Invoke();
    }

    protected override void OnPointerExited(PointerEventArgs e)
    {
        base.OnPointerExited(e);
        if (!_drawing) { _pointer = null; InvalidateVisual(); }
    }

    protected override void OnPointerWheelChanged(PointerWheelEventArgs e)
    {
        base.OnPointerWheelChanged(e);
        BrushSizeRequested?.Invoke(Math.Clamp(BrushSize * (e.Delta.Y > 0 ? 1.2 : 1 / 1.2), 2, 120));
        e.Handled = true;
    }

    // ---- pixel helpers

    /// <summary>The pixels of a bitmap as BGRA, whatever format it was decoded in.</summary>
    public static byte[] Pixels(Bitmap bitmap)
    {
        var size = bitmap.PixelSize;
        using var copy = new WriteableBitmap(size, new Vector(96, 96), PixelFormat.Bgra8888, AlphaFormat.Unpremul);
        var pixels = new byte[size.Width * size.Height * 4];
        using (var buffer = copy.Lock())
        {
            bitmap.CopyPixels(buffer);
            for (var y = 0; y < size.Height; y++)
                Marshal.Copy(buffer.Address + y * buffer.RowBytes, pixels, y * size.Width * 4, size.Width * 4);
        }
        return pixels;
    }

    /// <summary>The corrections as the engine reads them: a grey PNG, 255 remove, 128 keep, 0 automatic.</summary>
    public static void SaveCorrections(byte[] layer, int width, int height, string path)
    {
        using var image = new WriteableBitmap(new PixelSize(width, height), new Vector(96, 96), PixelFormat.Bgra8888, AlphaFormat.Unpremul);
        var row = new byte[width * 4];
        using (var buffer = image.Lock())
        {
            for (var y = 0; y < height; y++)
            {
                for (var x = 0; x < width; x++)
                {
                    var value = layer[y * width + x] switch { Add => (byte)255, KeepMark => (byte)128, _ => (byte)0 };
                    row[x * 4] = row[x * 4 + 1] = row[x * 4 + 2] = value; row[x * 4 + 3] = 255;
                }
                Marshal.Copy(row, 0, buffer.Address + y * buffer.RowBytes, row.Length);
            }
        }
        var draft = path + ".tmp";
#pragma warning disable CS0618 // PNG has no quality to set; the older overload writes the same file.
        using (var stream = File.Create(draft)) image.Save(stream);
#pragma warning restore CS0618
        File.Move(draft, path, overwrite: true);
    }

    /// <summary>Corrections read back from such a PNG, or null when it does not fit the frame.</summary>
    public static byte[]? LoadCorrections(string path, int width, int height)
    {
        try
        {
            using var stream = File.OpenRead(path);
            using var bitmap = new Bitmap(stream);
            if (bitmap.PixelSize.Width != width || bitmap.PixelSize.Height != height) return null;
            var pixels = Pixels(bitmap);
            var layer = new byte[width * height];
            for (var i = 0; i < layer.Length; i++)
            {
                var value = pixels[i * 4 + 2];
                layer[i] = value >= 192 ? Add : value >= 64 ? KeepMark : None;
            }
            return layer;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentException) { return null; }
    }
}
