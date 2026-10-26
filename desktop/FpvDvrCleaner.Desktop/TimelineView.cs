using System.Globalization;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Media;
using Avalonia.Media.Imaging;

namespace FpvDvrCleaner.Desktop;

/// <summary>
/// The trim editor's timeline. It shows the kept parts of a recording side by
/// side, the way a video editor shows clips on a track: removing a part closes
/// the gap and leaves a thin cut edge. Dragging an edge reveals or hides
/// footage again. Everything is drawn directly, so long clips stay smooth.
///
/// Times handed in and out are always source seconds; the control maps them to
/// the "output" axis (kept material only) for drawing and hit testing.
/// </summary>
public sealed class TimelineView : Control
{
    public const double RulerHeight = 26;
    public const double VideoTop = 32;
    public const double VideoHeight = 82;
    public const double AudioTop = 120;
    public const double AudioHeight = 44;
    public const double TotalHeight = 170;
    private const double HandleWidth = 7;
    private const double SnapPixels = 9;
    private const double MinPart = 0.1;

    public enum DragKind { None, Playhead, SegmentIn, SegmentOut }

    private static readonly IBrush Background = new SolidColorBrush(Color.Parse("#1B2025"));
    private static readonly IBrush TrackBrush = new SolidColorBrush(Color.Parse("#2A3138"));
    private static readonly IBrush RulerBrush = new SolidColorBrush(Color.Parse("#20262C"));
    private static readonly IBrush TickBrush = new SolidColorBrush(Color.Parse("#7E878F"));
    private static readonly IBrush MinorTickBrush = new SolidColorBrush(Color.Parse("#4A525A"));
    private static readonly IBrush LabelBrush = new SolidColorBrush(Color.Parse("#AEB6BC"));
    private static readonly IBrush AccentBrush = new SolidColorBrush(Color.Parse("#F08A3A"));
    private static readonly IBrush HandleBrush = new SolidColorBrush(Color.Parse("#D9F08A3A"));
    private static readonly IBrush SelectedHandleBrush = new SolidColorBrush(Color.Parse("#FFC08E"));
    private static readonly IBrush GripBrush = new SolidColorBrush(Color.Parse("#8020252B"));
    private static readonly IBrush CutEdgeBrush = new SolidColorBrush(Color.Parse("#E6EAED"));
    private static readonly IBrush WaveBrush = new SolidColorBrush(Color.Parse("#5FA8E8"));
    private static readonly IBrush PlayheadBrush = new SolidColorBrush(Color.Parse("#2D9CDB"));
    private static readonly IBrush HoverBrush = new SolidColorBrush(Color.Parse("#80FFFFFF"));
    private static readonly IBrush BookmarkBrush = new SolidColorBrush(Color.Parse("#FFC857"));
    private static readonly Pen EdgePen = new(AccentBrush, 2);
    private static readonly Pen SelectedPen = new(new SolidColorBrush(Color.Parse("#FFFFFF")), 1.5);
    private static readonly Pen CutEdgePen = new(CutEdgeBrush, 2);
    private static readonly Pen HoverPen = new(HoverBrush, 1);
    private static readonly Pen PlayheadPen = new(PlayheadBrush, 2);
    private static readonly Pen MajorTickPen = new(TickBrush, 1);
    private static readonly Pen MinorTickPen = new(MinorTickBrush, 1);
    private static readonly Pen BookmarkPen = new(BookmarkBrush, 1, DashStyle.Dash);
    private static readonly Typeface Face = new("Inter, Segoe UI, Helvetica, Arial");

    private double _duration = 1;
    private double _head;
    private double _hover = -1;
    private DragKind _drag;
    private int _dragIndex;
    private double _dragOrigin;
    private double _dragPressX;
    private int _selected = -1;
    private List<(double In, double Out)> _segments = [];
    private List<double> _bookmarks = [];
    private IReadOnlyList<(double Seconds, Bitmap Picture)> _thumbnails = [];
    private float[]? _waveform;
    private bool _hasAudio = true;

    public TimelineView()
    {
        Height = TotalHeight;
        ClipToBounds = true;
        Focusable = false;
    }

    // ------------------------------------------------------------ state

    public double Duration { get => _duration; set { _duration = Math.Max(value, 0.001); InvalidateVisual(); } }
    public double Head { get => _head; set { _head = Math.Clamp(value, 0, _duration); InvalidateVisual(); } }
    public bool Snap { get; set; } = true;

    public IReadOnlyList<(double In, double Out)> Segments => _segments;
    public IReadOnlyList<double> Bookmarks => _bookmarks;
    public DragKind Dragging => _drag;

    /// <summary>Seconds of kept material: the length of the exported clip.</summary>
    public double OutputDuration => Math.Max(0.001, _segments.Sum(s => s.Out - s.In));

    /// <summary>The part the user clicked last; -1 when none. Delete and the edge buttons act on it.</summary>
    public int Selected
    {
        get => _selected < _segments.Count ? _selected : -1;
        set { _selected = value < _segments.Count ? value : -1; InvalidateVisual(); }
    }

    public void SetSegments(IEnumerable<(double In, double Out)> segments)
    {
        _segments = segments.OrderBy(s => s.In).ToList();
        if (_selected >= _segments.Count) _selected = -1;
        InvalidateVisual();
    }

    public void SetBookmarks(IEnumerable<double> bookmarks)
    {
        _bookmarks = bookmarks.OrderBy(b => b).ToList();
        InvalidateVisual();
    }

    public void SetThumbnails(IReadOnlyList<(double Seconds, Bitmap Picture)> thumbnails)
    {
        _thumbnails = thumbnails;
        InvalidateVisual();
    }

    public void SetWaveform(float[]? levels, bool hasAudio)
    {
        _waveform = levels;
        _hasAudio = hasAudio;
        InvalidateVisual();
    }

    /// <summary>The playhead moved by the user (click or drag).</summary>
    public event Action<double>? HeadChanged;
    /// <summary>A part was clicked (index) or the click landed between parts (-1).</summary>
    public event Action<int>? SelectionChanged;
    /// <summary>A part edge is being dragged; the list is already updated.</summary>
    public event Action? SegmentsChanged;
    /// <summary>Raised once when an edit starts, so the window can take an undo snapshot.</summary>
    public event Action? EditStarted;
    /// <summary>A drag ended (playhead or part edge).</summary>
    public event Action<DragKind>? DragEnded;
    /// <summary>The pointer hovers over a moment (-1 when it leaves).</summary>
    public event Action<double>? HoverChanged;
    /// <summary>Ctrl + wheel: zoom by the factor around the moment under the pointer at the given x.</summary>
    public event Action<double, double, double>? ZoomRequested;
    /// <summary>Plain wheel: scroll the strip by this many pixels.</summary>
    public event Action<double>? ScrollRequested;

    // ------------------------------------------------------------ geometry

    private double Width_ => !double.IsNaN(Width) && Width > 0 ? Width : Bounds.Width > 0 ? Bounds.Width : 1000;
    public double PixelsPerSecond => Width_ / OutputDuration;

    /// <summary>Where a source moment sits on the output axis; removed footage collapses onto the cut.</summary>
    public double OutputOf(double source)
    {
        var offset = 0.0;
        foreach (var part in _segments)
        {
            if (source < part.In) return offset;
            if (source <= part.Out) return offset + source - part.In;
            offset += part.Out - part.In;
        }
        return offset;
    }

    public double SourceOf(double output)
    {
        var offset = 0.0;
        foreach (var part in _segments)
        {
            var length = part.Out - part.In;
            if (output <= offset + length) return part.In + Math.Max(0, output - offset);
            offset += length;
        }
        return _segments.Count > 0 ? _segments[^1].Out : 0;
    }

    public double XOf(double source) => OutputOf(source) * PixelsPerSecond;
    public double SecondsAt(double x) => SourceOf(Math.Clamp(x / PixelsPerSecond, 0, OutputDuration));

    /// <summary>Spacing of labelled ruler ticks, chosen so labels never crowd.</summary>
    public double RulerStep()
    {
        var pixelsPerSecond = PixelsPerSecond;
        foreach (var step in new[] { 0.04, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600 })
            if (pixelsPerSecond * step >= 90) return step;
        return 1200;
    }

    public int SegmentAt(double seconds)
    {
        for (var i = 0; i < _segments.Count; i++)
            if (seconds >= _segments[i].In - 1e-9 && seconds <= _segments[i].Out + 1e-9) return i;
        return -1;
    }

    /// <summary>Nearest part to a moment, for edits made while the playhead sits in removed footage.</summary>
    public int NearestSegment(double seconds)
    {
        if (_segments.Count == 0) return -1;
        var best = 0;
        var bestDistance = double.MaxValue;
        for (var i = 0; i < _segments.Count; i++)
        {
            var distance = seconds < _segments[i].In ? _segments[i].In - seconds
                         : seconds > _segments[i].Out ? seconds - _segments[i].Out : 0;
            if (distance < bestDistance) { bestDistance = distance; best = i; }
        }
        return best;
    }

    private IEnumerable<double> SnapTargets(DragKind kind, int index, double raw)
    {
        if (!Snap) yield break;
        for (var i = 0; i < _segments.Count; i++)
        {
            if (kind != DragKind.Playhead && i == index) continue;
            yield return _segments[i].In;
            yield return _segments[i].Out;
        }
        foreach (var bookmark in _bookmarks) yield return bookmark;
        if (kind != DragKind.Playhead) yield return _head;
        // Ruler ticks live on the output axis.
        var step = RulerStep();
        yield return SourceOf(Math.Round(OutputOf(raw) / step) * step);
        yield return 0;
        yield return _duration;
    }

    /// <summary>Snapping compares source seconds, which map 1:1 to pixels inside a part.</summary>
    private double Snapped(double seconds, DragKind kind, int index)
    {
        var best = seconds;
        var bestDistance = SnapPixels;
        foreach (var target in SnapTargets(kind, index, seconds))
        {
            var distance = Math.Abs(target - seconds) * PixelsPerSecond;
            if (distance < bestDistance) { bestDistance = distance; best = target; }
        }
        return Math.Clamp(best, 0, _duration);
    }

    // ------------------------------------------------------------ pointer

    private (DragKind Kind, int Index) HitTest(Point point)
    {
        if (point.Y >= VideoTop - 2 && point.Y <= AudioTop + AudioHeight)
        {
            for (var i = 0; i < _segments.Count; i++)
            {
                var left = XOf(_segments[i].In);
                var right = left + (_segments[i].Out - _segments[i].In) * PixelsPerSecond;
                if (point.X >= left - 3 && point.X <= left + HandleWidth + 3) return (DragKind.SegmentIn, i);
                if (point.X >= right - HandleWidth - 3 && point.X <= right + 3) return (DragKind.SegmentOut, i);
            }
        }
        return (DragKind.Playhead, -1);
    }

    protected override void OnPointerPressed(PointerPressedEventArgs e)
    {
        base.OnPointerPressed(e);
        if (!e.GetCurrentPoint(this).Properties.IsLeftButtonPressed || _duration <= 0 || _segments.Count == 0) return;
        var point = e.GetPosition(this);
        (_drag, _dragIndex) = HitTest(point);
        _dragPressX = point.X;
        if (_drag == DragKind.SegmentIn) _dragOrigin = _segments[_dragIndex].In;
        if (_drag == DragKind.SegmentOut) _dragOrigin = _segments[_dragIndex].Out;
        if (_drag != DragKind.Playhead) EditStarted?.Invoke();
        // Clicking a part in the tracks selects it, like a clip in a video editor.
        var chosen = _drag == DragKind.Playhead ? (point.Y >= VideoTop ? SegmentAt(SecondsAt(point.X)) : _selected) : _dragIndex;
        if (chosen != _selected) { _selected = chosen; SelectionChanged?.Invoke(chosen); }
        e.Pointer.Capture(this);
        ApplyDrag(point.X);
        e.Handled = true;
    }

    protected override void OnPointerMoved(PointerEventArgs e)
    {
        base.OnPointerMoved(e);
        var point = e.GetPosition(this);
        if (_drag != DragKind.None) { ApplyDrag(point.X); return; }
        if (_segments.Count == 0) return;
        var hit = HitTest(point);
        Cursor = hit.Kind == DragKind.Playhead ? Cursor.Default : new Cursor(StandardCursorType.SizeWestEast);
        var moment = SecondsAt(point.X);
        if (Math.Abs(moment - _hover) > 1e-6)
        {
            _hover = moment;
            HoverChanged?.Invoke(moment);
            InvalidateVisual();
        }
    }

    protected override void OnPointerReleased(PointerReleasedEventArgs e)
    {
        base.OnPointerReleased(e);
        if (_drag == DragKind.None) return;
        var ended = _drag;
        _drag = DragKind.None;
        e.Pointer.Capture(null);
        DragEnded?.Invoke(ended);
    }

    protected override void OnPointerExited(PointerEventArgs e)
    {
        base.OnPointerExited(e);
        if (_hover < 0) return;
        _hover = -1;
        HoverChanged?.Invoke(-1);
        InvalidateVisual();
    }

    protected override void OnPointerWheelChanged(PointerWheelEventArgs e)
    {
        base.OnPointerWheelChanged(e);
        var point = e.GetPosition(this);
        if (e.KeyModifiers.HasFlag(KeyModifiers.Control))
            ZoomRequested?.Invoke(e.Delta.Y > 0 ? 1.3 : 1 / 1.3, SecondsAt(point.X), point.X);
        else
        {
            var delta = Math.Abs(e.Delta.X) > Math.Abs(e.Delta.Y) ? e.Delta.X : e.Delta.Y;
            ScrollRequested?.Invoke(-delta * 80);
        }
        e.Handled = true;
    }

    private void ApplyDrag(double x)
    {
        switch (_drag)
        {
            case DragKind.Playhead:
                _head = Snapped(SecondsAt(x), DragKind.Playhead, -1);
                HeadChanged?.Invoke(_head);
                break;
            case DragKind.SegmentIn:
            {
                // Edges follow the pointer distance, so an edge can be pulled out into removed footage.
                var seconds = Snapped(_dragOrigin + (x - _dragPressX) / PixelsPerSecond, _drag, _dragIndex);
                var floor = _dragIndex > 0 ? _segments[_dragIndex - 1].Out + MinPart : 0;
                seconds = Math.Clamp(seconds, floor, _segments[_dragIndex].Out - MinPart);
                _segments[_dragIndex] = (seconds, _segments[_dragIndex].Out);
                _head = seconds; HeadChanged?.Invoke(_head);
                SegmentsChanged?.Invoke();
                break;
            }
            case DragKind.SegmentOut:
            {
                var seconds = Snapped(_dragOrigin + (x - _dragPressX) / PixelsPerSecond, _drag, _dragIndex);
                var ceiling = _dragIndex < _segments.Count - 1 ? _segments[_dragIndex + 1].In - MinPart : _duration;
                seconds = Math.Clamp(seconds, _segments[_dragIndex].In + MinPart, ceiling);
                _segments[_dragIndex] = (_segments[_dragIndex].In, seconds);
                _head = seconds; HeadChanged?.Invoke(_head);
                SegmentsChanged?.Invoke();
                break;
            }
        }
        InvalidateVisual();
    }

    // ------------------------------------------------------------ drawing

    private static string Label(double seconds, double step)
    {
        seconds = Math.Max(0, seconds);
        var minutes = (int)(seconds / 60);
        var rest = seconds - minutes * 60;
        return step < 1
            ? string.Create(CultureInfo.InvariantCulture, $"{minutes:00}:{rest:00.00}")
            : string.Create(CultureInfo.InvariantCulture, $"{minutes:00}:{(int)rest:00}");
    }

    private static FormattedText Text(string text, double size, IBrush brush, FontWeight weight = FontWeight.Normal) =>
        new(text, CultureInfo.InvariantCulture, FlowDirection.LeftToRight,
            new Typeface(Face.FontFamily, FontStyle.Normal, weight), size, brush);

    public override void Render(DrawingContext context)
    {
        var width = Width_;
        context.FillRectangle(Background, new Rect(0, 0, width, TotalHeight));
        context.FillRectangle(RulerBrush, new Rect(0, 0, width, RulerHeight));
        context.FillRectangle(TrackBrush, new Rect(0, VideoTop, width, VideoHeight));
        context.FillRectangle(TrackBrush, new Rect(0, AudioTop, width, AudioHeight));
        if (_duration <= 0 || _segments.Count == 0) return;

        DrawThumbnails(context);
        DrawWaveform(context, width);
        DrawRuler(context, width);
        DrawParts(context);
        DrawBookmarks(context);

        if (_hover >= 0 && _drag == DragKind.None && SegmentAt(_hover) >= 0)
        {
            var x = Math.Round(XOf(_hover)) + 0.5;
            context.DrawLine(HoverPen, new Point(x, VideoTop), new Point(x, AudioTop + AudioHeight));
        }

        // Playhead with its grip in the ruler.
        var headX = Math.Round(XOf(_head)) + 0.5;
        context.DrawLine(PlayheadPen, new Point(headX, 8), new Point(headX, TotalHeight));
        context.DrawRectangle(PlayheadBrush, null, new RoundedRect(new Rect(headX - 6, 3, 12, 12), 3));
    }

    private void DrawRuler(DrawingContext context, double width)
    {
        var step = RulerStep();
        var minor = step / 5;
        var total = OutputDuration;
        for (var t = 0.0; t <= total + 1e-9; t += minor)
        {
            var x = Math.Round(t * PixelsPerSecond) + 0.5;
            var major = Math.Abs(t / step - Math.Round(t / step)) < 1e-6;
            context.DrawLine(major ? MajorTickPen : MinorTickPen,
                new Point(x, major ? RulerHeight - 9 : RulerHeight - 4), new Point(x, RulerHeight));
            if (major && x + 44 < width)
                context.DrawText(Text(Label(t, step), 10, LabelBrush), new Point(x + 4, 2));
        }
    }

    private void DrawThumbnails(DrawingContext context)
    {
        if (_thumbnails.Count == 0)
        {
            context.DrawText(Text(UiText.BuildingStrip, 12, LabelBrush), new Point(12, VideoTop + VideoHeight / 2 - 8));
            return;
        }
        var pps = PixelsPerSecond;
        var tileWidth = VideoHeight * 4 / 3;
        foreach (var part in _segments)
        {
            var left = XOf(part.In);
            var partWidth = (part.Out - part.In) * pps;
            var tiles = Math.Max(1, (int)Math.Round(partWidth / tileWidth));
            var eachWidth = partWidth / tiles;
            for (var index = 0; index < tiles; index++)
            {
                var moment = part.In + (index + 0.5) * (part.Out - part.In) / tiles;
                var picture = _thumbnails.MinBy(tile => Math.Abs(tile.Seconds - moment)).Picture;
                var destination = new Rect(left + index * eachWidth, VideoTop, eachWidth, VideoHeight);
                // Crop the picture to fill the tile (UniformToFill).
                var scale = Math.Max(eachWidth / picture.PixelSize.Width, VideoHeight / picture.PixelSize.Height);
                var sourceWidth = eachWidth / scale;
                var sourceHeight = VideoHeight / scale;
                var source = new Rect((picture.PixelSize.Width - sourceWidth) / 2, (picture.PixelSize.Height - sourceHeight) / 2,
                    sourceWidth, sourceHeight);
                context.DrawImage(picture, source, destination);
            }
        }
    }

    private void DrawWaveform(DrawingContext context, double width)
    {
        if (!_hasAudio)
        {
            context.DrawText(Text(UiText.NoAudio, 11, LabelBrush), new Point(12, AudioTop + AudioHeight / 2 - 7));
            return;
        }
        if (_waveform == null || _waveform.Length == 0)
        {
            context.DrawText(Text(UiText.ReadingAudio, 11, LabelBrush), new Point(12, AudioTop + AudioHeight / 2 - 7));
            return;
        }
        var middle = AudioTop + AudioHeight / 2;
        var columns = Math.Max(1, (int)(width / 2));
        var columnWidth = width / columns;
        var bucketSeconds = _duration / _waveform.Length;
        for (var column = 0; column < columns; column++)
        {
            var from = SourceOf(column * columnWidth / PixelsPerSecond);
            var to = SourceOf((column + 1) * columnWidth / PixelsPerSecond);
            var first = Math.Clamp((int)(from / bucketSeconds), 0, _waveform.Length - 1);
            var last = Math.Clamp((int)(to / bucketSeconds), first, _waveform.Length - 1);
            var peak = 0f;
            for (var i = first; i <= last; i++) peak = Math.Max(peak, _waveform[i]);
            var half = Math.Max(1, peak * (AudioHeight / 2 - 3));
            context.FillRectangle(WaveBrush, new Rect(column * columnWidth, middle - half, Math.Max(1, columnWidth - 0.6), half * 2));
        }
    }

    private void DrawParts(DrawingContext context)
    {
        var pps = PixelsPerSecond;
        var trackHeight = AudioTop + AudioHeight - VideoTop;
        for (var i = 0; i < _segments.Count; i++)
        {
            var left = XOf(_segments[i].In);
            var right = left + (_segments[i].Out - _segments[i].In) * pps;
            var selected = i == _selected;
            // A thin frame around each part; the selected part gets a bright outline.
            context.DrawLine(EdgePen, new Point(left, VideoTop + 1), new Point(right, VideoTop + 1));
            context.DrawLine(EdgePen, new Point(left, AudioTop + AudioHeight - 1), new Point(right, AudioTop + AudioHeight - 1));
            if (selected)
                context.DrawRectangle(null, SelectedPen, new RoundedRect(new Rect(left + 1, VideoTop + 1, Math.Max(2, right - left - 2), trackHeight - 2), 2));
            var handle = selected ? SelectedHandleBrush : HandleBrush;
            context.DrawRectangle(handle, null, new RoundedRect(new Rect(left, VideoTop, HandleWidth, trackHeight), 2, 0, 0, 2));
            context.DrawRectangle(handle, null, new RoundedRect(new Rect(right - HandleWidth, VideoTop, HandleWidth, trackHeight), 0, 2, 2, 0));
            // Grip lines on the handles.
            foreach (var x in new[] { left + 2.75, right - HandleWidth + 2.75 })
                context.FillRectangle(GripBrush, new Rect(x, VideoTop + trackHeight / 2 - 8, 1.5, 16));
            if (i > 0)
            {
                // The cut edge where removed footage was taken out.
                var x = Math.Round(left) + 0.5;
                context.DrawLine(CutEdgePen, new Point(x, VideoTop - 3), new Point(x, AudioTop + AudioHeight + 3));
                var notch = new StreamGeometry();
                using (var pen = notch.Open())
                {
                    pen.BeginFigure(new Point(x - 4, RulerHeight - 6), true);
                    pen.LineTo(new Point(x + 4, RulerHeight - 6));
                    pen.LineTo(new Point(x, RulerHeight));
                    pen.EndFigure(true);
                }
                context.DrawGeometry(CutEdgeBrush, null, notch);
            }
            if (_segments.Count > 1 && right - left > 60)
            {
                var tag = Text($"{i + 1}", 10, Brushes.White, FontWeight.Bold);
                context.DrawRectangle(AccentBrush, null, new RoundedRect(new Rect(left + HandleWidth + 5, VideoTop + 5, tag.Width + 10, 15), 7));
                context.DrawText(tag, new Point(left + HandleWidth + 10, VideoTop + 5.5));
            }
        }
    }

    private void DrawBookmarks(DrawingContext context)
    {
        foreach (var bookmark in _bookmarks)
        {
            if (SegmentAt(bookmark) < 0) continue;  // Inside removed footage: nothing to show.
            var x = Math.Round(XOf(bookmark)) + 0.5;
            context.DrawLine(BookmarkPen, new Point(x, RulerHeight), new Point(x, AudioTop + AudioHeight));
            var flag = new StreamGeometry();
            using (var pen = flag.Open())
            {
                pen.BeginFigure(new Point(x - 5, RulerHeight - 12), true);
                pen.LineTo(new Point(x + 5, RulerHeight - 12));
                pen.LineTo(new Point(x + 5, RulerHeight - 4));
                pen.LineTo(new Point(x, RulerHeight - 7));
                pen.LineTo(new Point(x - 5, RulerHeight - 4));
                pen.EndFigure(true);
            }
            context.DrawGeometry(BookmarkBrush, null, flag);
        }
    }
}
