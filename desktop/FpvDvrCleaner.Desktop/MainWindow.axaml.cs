using System.Diagnostics;
using System.Globalization;
using System.Text.Json;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Platform.Storage;
using Avalonia.Threading;

namespace FpvDvrCleaner.Desktop;

public partial class MainWindow : Window
{
    private sealed class FootageItem(string path, double duration)
    {
        public string Path { get; } = path;
        public double Duration { get; } = duration;
        public double In { get; set; }
        public double Out { get; set; } = duration;
        public List<(double Seconds, Bitmap Picture)> Strip { get; } = [];
        public bool StripRequested { get; set; }
        public override string ToString() => System.IO.Path.GetFileName(Path);
    }

    private enum Drag { None, Playhead, In, Out }

    private readonly List<FootageItem> _queue = [];
    private FootageItem? _selected;
    private int _page;
    private double _head;
    private Drag _drag;
    private bool _pickingClip;
    private int _frameRequest;
    private readonly DispatcherTimer _frameDelay = new() { Interval = TimeSpan.FromMilliseconds(180) };
    private readonly DispatcherTimer _playClock = new() { Interval = TimeSpan.FromMilliseconds(80) };
    private DateTime _playStarted;
    private double _playFrom;
    private string? _reviewSource;
    private string? _reviewOutput;
    private double _reviewStart;
    private bool _loadingReview;
    private CancellationTokenSource? _trainingCancel;
    private CancellationTokenSource? _exportCancel;
    private readonly DispatcherTimer _exportClock = new() { Interval = TimeSpan.FromSeconds(1) };
    private DateTime _exportStarted;
    private double _exportDone;
    private static readonly string[] Extensions = [".mp4", ".mov", ".mkv", ".avi", ".m4v", ".ts"];
    private static readonly string[] Titles = ["Import", "Trim", "Repair", "Export", "Model training"];
    private static readonly string[] Subtitles =
    [
        "Bring in your DVR recordings.",
        "Keep only the part of each clip you want.",
        "Choose the repairs and check them on a real frame.",
        "Render every clip in the queue.",
        "Optional: learn from your own footage.",
    ];

    public MainWindow()
    {
        InitializeComponent();
        _frameDelay.Tick += async (_, _) => { _frameDelay.Stop(); await RefreshFrameAsync(); };
        _playClock.Tick += (_, _) => PlayTick();
        _exportClock.Tick += (_, _) => ShowExportTime();
        DragDrop.AddDragOverHandler(DropZone, (_, e) => e.DragEffects = DragDropEffects.Copy);
        DragDrop.AddDropHandler(DropZone, async (_, e) =>
        {
            var files = e.DataTransfer.TryGetFiles();
            if (files == null) return;
            var paths = new List<string>();
            foreach (var item in files)
            {
                var path = item.TryGetLocalPath();
                if (path == null) continue;
                if (Directory.Exists(path)) paths.AddRange(VideosIn(path));
                else if (Extensions.Contains(System.IO.Path.GetExtension(path).ToLowerInvariant())) paths.Add(path);
            }
            await AddFilesAsync(paths);
        });
        Opened += async (_, _) =>
        {
            RefreshModelChoices(); RefreshResumeChoices(); ShowPage(0); UpdateCompare();
            // Files passed on the command line ("Open with", dropped on the exe) join the queue.
            var given = Environment.GetCommandLineArgs().Skip(1).Where(File.Exists).ToList();
            if (given.Count > 0) await AddFilesAsync(given);
        };
    }

    // ---------------------------------------------------------------- engine

    private static string RepoRoot()
    {
        var current = new DirectoryInfo(AppContext.BaseDirectory);
        while (current != null)
        {
            if (File.Exists(System.IO.Path.Combine(current.FullName, "pyproject.toml")) &&
                File.Exists(System.IO.Path.Combine(current.FullName, "src", "flugbild", "bridge.py")))
                return current.FullName;
            current = current.Parent;
        }
        throw new InvalidOperationException("Python video engine was not found next to this desktop build.");
    }

    private static string ModelFolder()
    {
        var folder = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "FPV DVR Cleaner", "models");
        Directory.CreateDirectory(folder);
        return folder;
    }

    /// <summary>The engine shipped inside a release build, if present.</summary>
    private static string? BundledEngine()
    {
        var name = OperatingSystem.IsWindows() ? "flugbild-engine.exe" : "flugbild-engine";
        var path = System.IO.Path.Combine(AppContext.BaseDirectory, "engine", name);
        return File.Exists(path) ? path : null;
    }

    private static ProcessStartInfo EngineStart(string ticket)
    {
        ProcessStartInfo start;
        if (BundledEngine() is { } engine)
        {
            // Release build: frozen engine plus FFmpeg next to the app, no Python needed.
            start = new ProcessStartInfo(engine) { WorkingDirectory = AppContext.BaseDirectory };
            var tools = System.IO.Path.Combine(AppContext.BaseDirectory, "ffmpeg");
            if (Directory.Exists(tools))
                start.Environment["PATH"] = tools + System.IO.Path.PathSeparator + Environment.GetEnvironmentVariable("PATH");
        }
        else
        {
            var root = RepoRoot();
            start = new ProcessStartInfo("python") { WorkingDirectory = root };
            start.Environment["PYTHONPATH"] = System.IO.Path.Combine(root, "src");
            start.ArgumentList.Add("-m"); start.ArgumentList.Add("flugbild.bridge");
        }
        start.ArgumentList.Add(ticket);
        start.RedirectStandardOutput = true; start.RedirectStandardError = true;
        start.UseShellExecute = false; start.CreateNoWindow = true;
        start.StandardOutputEncoding = System.Text.Encoding.UTF8; start.StandardErrorEncoding = System.Text.Encoding.UTF8;
        // Without this, Windows Python writes the ANSI code page and umlauts in
        // file names or FFmpeg messages arrive as garbled characters.
        start.Environment["PYTHONIOENCODING"] = "utf-8";
        return start;
    }

    private static async Task<JsonElement> EngineAsync(object request, Action<JsonElement>? progress = null,
        CancellationToken cancellation = default)
    {
        var ticket = System.IO.Path.Combine(System.IO.Path.GetTempPath(), $"fpv-task-{Guid.NewGuid():N}.json");
        await File.WriteAllTextAsync(ticket, JsonSerializer.Serialize(request), cancellation);
        try
        {
            var start = EngineStart(ticket);
            using var process = Process.Start(start) ?? throw new InvalidOperationException("Could not start video engine");
            using var killOnCancel = cancellation.Register(() =>
            {
                try { if (!process.HasExited) process.Kill(entireProcessTree: true); }
                catch (InvalidOperationException) { }
            });
            var errorTask = process.StandardError.ReadToEndAsync();
            JsonElement result = default;
            string? fault = null;
            while (await process.StandardOutput.ReadLineAsync(cancellation) is { } line)
            {
                using var record = JsonDocument.Parse(line);
                var item = record.RootElement.Clone();
                switch (item.GetProperty("kind").GetString())
                {
                    case "progress": progress?.Invoke(item); break;
                    case "result": result = item; break;
                    case "error": fault = item.GetProperty("message").GetString(); break;
                }
            }
            await process.WaitForExitAsync(cancellation);
            if (process.ExitCode != 0 || fault != null)
                throw new InvalidOperationException(fault ?? await errorTask);
            return result;
        }
        finally { File.Delete(ticket); }
    }

    private static Bitmap LoadBitmap(string path)
    {
        using var stream = File.OpenRead(path);
        return new Bitmap(stream);
    }

    private static string TempFile(string stem, string extension = ".png") =>
        System.IO.Path.Combine(System.IO.Path.GetTempPath(), $"fpv-{stem}-{Guid.NewGuid():N}{extension}");

    private static string Clock(double seconds)
    {
        seconds = Math.Max(0, seconds);
        return string.Create(CultureInfo.InvariantCulture, $"{(int)(seconds / 60):00}:{seconds % 60:00.00}");
    }

    private static string Duration(TimeSpan span) => $"{(int)span.TotalHours:00}:{span.Minutes:00}:{span.Seconds:00}";

    private static bool TryParseClock(string? text, out double seconds)
    {
        seconds = 0;
        if (string.IsNullOrWhiteSpace(text)) return false;
        var parts = text.Trim().Replace(',', '.').Split(':');
        if (parts.Length > 3) return false;
        foreach (var part in parts)
        {
            if (!double.TryParse(part, NumberStyles.Float, CultureInfo.InvariantCulture, out var value) || value < 0) return false;
            seconds = seconds * 60 + value;
        }
        return true;
    }

    // ---------------------------------------------------------------- steps

    private void ShowPage(int index)
    {
        if (index != 1) StopPlay();
        _page = index;
        var pages = new Control[] { ImportPage, TrimPage, CleanPage, ExportPage, TrainPage };
        var steps = new[] { ImportStep, TrimStep, CleanStep, ExportStep, TrainStep };
        for (var i = 0; i < pages.Length; i++) { pages[i].IsVisible = i == index; steps[i].Classes.Set("current", i == index); }
        PageTitle.Text = Titles[index];
        PageSubtitle.Text = Subtitles[index];
        BackButton.IsVisible = index is > 0 and < 4;
        NextButton.IsVisible = index < 3;
        NextButton.Content = index switch { 0 => "Next: Trim →", 1 => "Next: Repair →", _ => "Next: Export →" };
        FooterNote.Text = index switch
        {
            0 => _queue.Count == 0 ? "Add at least one recording to continue." : "Select a clip, then trim it.",
            1 => "Every clip keeps its own start and end.",
            2 => "Settings apply to every clip in the queue.",
            _ => "",
        };
        if (index == 1 && _selected != null) { BuildStripLayout(); RequestFrame(); }
        if (index == 3) UpdateExportSummary();
        PageScroller.Offset = new Vector(0, 0);
    }
    private void ImportStep_Click(object? sender, RoutedEventArgs e) => ShowPage(0);
    private void TrimStep_Click(object? sender, RoutedEventArgs e) => ShowPage(1);
    private void CleanStep_Click(object? sender, RoutedEventArgs e) => ShowPage(2);
    private void ExportStep_Click(object? sender, RoutedEventArgs e) => ShowPage(3);
    private void TrainStep_Click(object? sender, RoutedEventArgs e) => ShowPage(4);
    private void Back_Step_Click(object? sender, RoutedEventArgs e) => ShowPage(Math.Max(0, _page - 1));
    private void Next_Step_Click(object? sender, RoutedEventArgs e)
    {
        if (_page == 0 && _queue.Count == 0) { ImportStatus.Text = "Add at least one recording first."; return; }
        ShowPage(Math.Min(3, _page + 1));
    }

    // ---------------------------------------------------------------- import

    private static IEnumerable<string> VideosIn(string folder) =>
        Directory.EnumerateFiles(folder, "*", SearchOption.AllDirectories)
            .Where(file => Extensions.Contains(System.IO.Path.GetExtension(file).ToLowerInvariant()));

    private async Task<string?> PickFolderAsync()
    {
        var folders = await StorageProvider.OpenFolderPickerAsync(new FolderPickerOpenOptions { AllowMultiple = false });
        return folders.Count > 0 ? folders[0].TryGetLocalPath() : null;
    }

    private async void Add_Click(object? sender, RoutedEventArgs e)
    {
        var files = await StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions { AllowMultiple = true, Title = "Add recordings" });
        await AddFilesAsync(files.Select(file => file.TryGetLocalPath()).OfType<string>());
    }

    private async void AddFolder_Click(object? sender, RoutedEventArgs e)
    {
        var folder = await PickFolderAsync();
        if (folder != null) await AddFilesAsync(VideosIn(folder));
    }

    private async Task AddFilesAsync(IEnumerable<string> paths)
    {
        var added = 0;
        foreach (var path in paths)
        {
            if (_queue.Any(clip => clip.Path.Equals(path, StringComparison.OrdinalIgnoreCase))) continue;
            ImportStatus.Text = $"Reading {System.IO.Path.GetFileName(path)}…";
            try
            {
                var info = await EngineAsync(new { action = "inspect", source = path });
                _queue.Add(new FootageItem(path, info.GetProperty("duration").GetDouble()));
                added++;
            }
            catch (Exception ex) { ImportStatus.Text = $"Skipped {System.IO.Path.GetFileName(path)}: {ex.Message}"; }
        }
        if (added > 0) ImportStatus.Text = $"Added {added} clip{(added == 1 ? "" : "s")}.";
        DrawQueue();
        if (_selected == null && _queue.Count > 0) Select(_queue[0]);
        if (_page == 0) ShowPage(0);
    }

    private void DrawQueue()
    {
        QueueList.Children.Clear();
        QueueEmpty.IsVisible = _queue.Count == 0;
        CountLabel.Text = $"{_queue.Count} clip{(_queue.Count == 1 ? "" : "s")}";
        ImportHint.Text = _queue.Count == 0 ? "Add recordings" : CountLabel.Text;
        foreach (var clip in _queue)
        {
            var row = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto,Auto"), Margin = new Thickness(0, 1),
                Background = new SolidColorBrush(Color.Parse(clip == _selected ? "#FFF0E6" : "#F6F6F3")) };
            var choose = new Button
            {
                Content = new StackPanel
                {
                    Children =
                    {
                        new TextBlock { Text = System.IO.Path.GetFileName(clip.Path), FontWeight = clip == _selected ? FontWeight.SemiBold : FontWeight.Normal,
                            Foreground = new SolidColorBrush(Color.Parse("#20252B")) },
                        new TextBlock { Text = $"{Clock(clip.Duration)} long · keeping {Clock(clip.In)} – {Clock(clip.Out)}",
                            FontSize = 11, Foreground = new SolidColorBrush(Color.Parse("#5F676D")) },
                    },
                },
            };
            choose.Classes.Add("row");
            choose.Click += (_, _) => Select(clip);
            var trim = new Button { Content = "Trim", Margin = new Thickness(6, 0) };
            trim.Click += (_, _) => { Select(clip); ShowPage(1); };
            var remove = new Button { Content = "×", Width = 40 };
            remove.Classes.Add("remove");
            ToolTip.SetTip(remove, "Remove from queue");
            remove.Click += (_, _) =>
            {
                _queue.Remove(clip);
                if (_selected == clip) { _selected = null; if (_queue.Count > 0) Select(_queue[0]); else ClearSelection(); }
                DrawQueue();
            };
            Grid.SetColumn(trim, 1); Grid.SetColumn(remove, 2);
            row.Children.Add(choose); row.Children.Add(trim); row.Children.Add(remove);
            QueueList.Children.Add(row);
        }
        _pickingClip = true;
        ClipPicker.ItemsSource = _queue.ToList();
        ClipPicker.SelectedItem = _selected;
        _pickingClip = false;
    }

    private void ClearSelection()
    {
        TrimPreview.Source = null; StripTiles.Children.Clear(); StripStatus.Text = "Choose a clip to build its filmstrip.";
        TrimHint.Text = "Pick the part to keep";
    }

    private void ClipPicker_Changed(object? sender, SelectionChangedEventArgs e)
    {
        if (!_pickingClip && ClipPicker.SelectedItem is FootageItem clip && clip != _selected) Select(clip);
    }

    private void Select(FootageItem clip)
    {
        StopPlay();
        _selected = clip;
        _head = clip.In;
        DrawQueue();
        UpdateCutFields();
        BuildStripLayout();
        RequestFrame();
        if (!clip.StripRequested) _ = LoadStripAsync(clip);
    }

    // ---------------------------------------------------------------- trim editor

    private async Task LoadStripAsync(FootageItem clip)
    {
        clip.StripRequested = true;
        StripStatus.Text = "Building the filmstrip…";
        var folder = System.IO.Path.Combine(System.IO.Path.GetTempPath(), $"fpv-strip-{Guid.NewGuid():N}");
        try
        {
            var outcome = await EngineAsync(new { action = "filmstrip", source = clip.Path, folder, count = 160, height = 76 });
            foreach (var tile in outcome.GetProperty("tiles").EnumerateArray())
                clip.Strip.Add((tile.GetProperty("seconds").GetDouble(), LoadBitmap(tile.GetProperty("path").GetString()!)));
            if (_selected == clip) { BuildStripLayout(); StripStatus.Text = $"{clip.Strip.Count} thumbnails · zoom in for finer cuts."; }
        }
        catch (Exception ex) { clip.StripRequested = false; StripStatus.Text = $"Filmstrip unavailable: {ex.Message}"; }
        finally { try { Directory.Delete(folder, true); } catch (IOException) { } }
    }

    private double StripWidth()
    {
        var view = StripScroller.Bounds.Width > 0 ? StripScroller.Bounds.Width : 1000;
        return Math.Max(view, view * ZoomSlider.Value);
    }

    private double XOf(double seconds) => _selected == null ? 0 : seconds / Math.Max(_selected.Duration, 0.001) * StripWidth();
    private double SecondsAt(double x) => _selected == null ? 0 : Math.Clamp(x / StripWidth() * _selected.Duration, 0, _selected.Duration);

    private void BuildStripLayout()
    {
        if (_selected == null) return;
        var width = StripWidth();
        StripCanvas.Width = width;
        StripTiles.Children.Clear();
        var clip = _selected;
        if (clip.Strip.Count > 0)
        {
            // As many tiles as fit at a readable width; zooming in reveals more of them.
            var tiles = Math.Clamp((int)(width / 110), 1, clip.Strip.Count);
            var tileWidth = width / tiles;
            for (var index = 0; index < tiles; index++)
            {
                var moment = (index + 0.5) * clip.Duration / tiles;
                var picture = clip.Strip.MinBy(tile => Math.Abs(tile.Seconds - moment)).Picture;
                StripTiles.Children.Add(new Image { Source = picture, Width = tileWidth, Height = 76, Stretch = Stretch.UniformToFill });
            }
        }
        StripRuler.Children.Clear();
        StripRuler.Width = width;
        var step = new[] { 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300 }.FirstOrDefault(s => width / clip.Duration * s >= 80, 600);
        for (var t = 0.0; t <= clip.Duration; t += step)
        {
            var x = XOf(t);
            var tick = new Border { Width = 1, Height = 6, Background = new SolidColorBrush(Color.Parse("#7E878F")) };
            Canvas.SetLeft(tick, x); Canvas.SetTop(tick, 12);
            var label = new TextBlock { Text = Clock(t)[..5], FontSize = 10, Foreground = new SolidColorBrush(Color.Parse("#AEB6BC")) };
            Canvas.SetLeft(label, x + 3); Canvas.SetTop(label, 0);
            StripRuler.Children.Add(tick); StripRuler.Children.Add(label);
        }
        PlaceMarkers();
    }

    private void PlaceMarkers()
    {
        if (_selected == null) return;
        var width = StripWidth();
        var left = XOf(_selected.In);
        var right = XOf(_selected.Out);
        Canvas.SetLeft(ShadeBefore, 0); ShadeBefore.Width = Math.Max(0, left);
        Canvas.SetLeft(ShadeAfter, right); ShadeAfter.Width = Math.Max(0, width - right);
        Canvas.SetLeft(CutFrame, left); CutFrame.Width = Math.Max(1, right - left);
        Canvas.SetLeft(InHandle, left - 12);
        Canvas.SetLeft(OutHandle, right);
        Canvas.SetLeft(Playhead, XOf(_head) - 1);
        PlayheadLabel.Text = Clock(_head);
        TrimHint.Text = $"Keeping {Clock(_selected.Out - _selected.In)}";
    }

    private void UpdateCutFields()
    {
        if (_selected == null) return;
        InBox.Text = Clock(_selected.In);
        OutBox.Text = Clock(_selected.Out);
        KeptLabel.Text = $"{Clock(_selected.Out - _selected.In)} of {Clock(_selected.Duration)}";
        CutSummary.Text = $"[ {Clock(_selected.In)}   ] {Clock(_selected.Out)}";
        PlaceMarkers();
    }

    private void SetCut(double start, double end)
    {
        if (_selected == null) return;
        end = Math.Clamp(end, 0.1, _selected.Duration);
        start = Math.Clamp(start, 0, end - 0.1);
        _selected.In = start; _selected.Out = end;
        UpdateCutFields();
        DrawQueue();
    }

    private void MoveHead(double seconds, bool fetch = true)
    {
        if (_selected == null) return;
        _head = Math.Clamp(seconds, 0, _selected.Duration);
        PlaceMarkers();
        KeepHeadVisible();
        ShowThumbnail();
        if (fetch) RequestFrame();
    }

    private void KeepHeadVisible()
    {
        var x = XOf(_head);
        var view = StripScroller.Bounds.Width;
        var offset = StripScroller.Offset.X;
        if (x < offset + 20 || x > offset + view - 20)
            StripScroller.Offset = new Vector(Math.Max(0, x - view / 2), 0);
    }

    private void ShowThumbnail()
    {
        if (_selected == null || _selected.Strip.Count == 0) return;
        var nearest = _selected.Strip.MinBy(tile => Math.Abs(tile.Seconds - _head));
        TrimPreview.Source = nearest.Picture;
    }

    private void RequestFrame()
    {
        _frameDelay.Stop();
        _frameDelay.Start();
    }

    private async Task RefreshFrameAsync()
    {
        if (_selected == null) return;
        var request = ++_frameRequest;
        var clip = _selected;
        var moment = _head;
        var target = TempFile("frame");
        TrimLoading.IsVisible = true;
        try
        {
            await EngineAsync(new { action = "frame", source = clip.Path, seconds = moment, target });
            if (request == _frameRequest && clip == _selected) TrimPreview.Source = LoadBitmap(target);
        }
        catch { /* A damaged frame keeps the thumbnail in view. */ }
        finally { File.Delete(target); if (request == _frameRequest) TrimLoading.IsVisible = false; }
    }

    private void Strip_PointerPressed(object? sender, PointerPressedEventArgs e)
    {
        if (_selected == null) return;
        StopPlay();
        var x = e.GetPosition(StripCanvas).X;
        _drag = Math.Abs(x - XOf(_selected.In)) < 14 ? Drag.In
              : Math.Abs(x - XOf(_selected.Out)) < 14 ? Drag.Out : Drag.Playhead;
        e.Pointer.Capture(StripCanvas);
        ApplyDrag(x);
    }

    private void Strip_PointerMoved(object? sender, PointerEventArgs e)
    {
        if (_drag != Drag.None) ApplyDrag(e.GetPosition(StripCanvas).X);
    }

    private void Strip_PointerReleased(object? sender, PointerReleasedEventArgs e)
    {
        _drag = Drag.None;
        e.Pointer.Capture(null);
        RequestFrame();
    }

    private void ApplyDrag(double x)
    {
        if (_selected == null) return;
        var seconds = SecondsAt(x);
        switch (_drag)
        {
            case Drag.In: SetCut(seconds, _selected.Out); MoveHead(_selected.In, false); break;
            case Drag.Out: SetCut(_selected.In, seconds); MoveHead(_selected.Out, false); break;
            case Drag.Playhead: MoveHead(seconds, false); break;
        }
    }

    private void Zoom_Changed(object? sender, Avalonia.Controls.Primitives.RangeBaseValueChangedEventArgs e)
    {
        BuildStripLayout();
        KeepHeadVisible();
    }

    private void Play_Click(object? sender, RoutedEventArgs e) => TogglePlay();

    private void TogglePlay()
    {
        if (_selected == null) return;
        if (_playClock.IsEnabled) { StopPlay(); return; }
        if (_head >= _selected.Out - 0.05) _head = _selected.In;
        _playFrom = _head; _playStarted = DateTime.UtcNow;
        PlayButton.Content = "⏸ Pause";
        _playClock.Start();
    }

    private void StopPlay()
    {
        if (!_playClock.IsEnabled) return;
        _playClock.Stop();
        PlayButton.Content = "▶ Play";
        RequestFrame();
    }

    private void PlayTick()
    {
        if (_selected == null) { StopPlay(); return; }
        var moment = _playFrom + (DateTime.UtcNow - _playStarted).TotalSeconds;
        if (moment >= _selected.Out) { MoveHead(_selected.Out, false); StopPlay(); return; }
        MoveHead(moment, false);
    }

    private void GoStart_Click(object? sender, RoutedEventArgs e) { if (_selected != null) MoveHead(_selected.In); }
    private void GoEnd_Click(object? sender, RoutedEventArgs e) { if (_selected != null) MoveHead(_selected.Out); }
    private void Back_Click(object? sender, RoutedEventArgs e) => MoveHead(_head - 1);
    private void Forward_Click(object? sender, RoutedEventArgs e) => MoveHead(_head + 1);
    private void MarkIn_Click(object? sender, RoutedEventArgs e) { if (_selected != null) SetCut(_head, _selected.Out); }
    private void MarkOut_Click(object? sender, RoutedEventArgs e) { if (_selected != null) SetCut(_selected.In, _head); }
    private void ResetCut_Click(object? sender, RoutedEventArgs e) { if (_selected != null) SetCut(0, _selected.Duration); }

    private void CutBox_KeyDown(object? sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter) { CutBox_Changed(sender, e); e.Handled = true; }
    }

    private void CutBox_Changed(object? sender, RoutedEventArgs e)
    {
        if (_selected == null) return;
        if (TryParseClock(InBox.Text, out var start) && TryParseClock(OutBox.Text, out var end) && end > start)
            SetCut(start, end);
        else UpdateCutFields();
    }

    private void Window_KeyDown(object? sender, KeyEventArgs e)
    {
        if (_page != 1 || _selected == null || FocusManager?.GetFocusedElement() is TextBox) return;
        var step = e.KeyModifiers.HasFlag(KeyModifiers.Shift) ? 5 : 1;
        switch (e.Key)
        {
            case Key.Space: TogglePlay(); break;
            case Key.Left: StopPlay(); MoveHead(_head - step); break;
            case Key.Right: StopPlay(); MoveHead(_head + step); break;
            case Key.I: SetCut(_head, _selected.Out); break;
            case Key.O: SetCut(_selected.In, _head); break;
            case Key.Home: MoveHead(_selected.In); break;
            case Key.End: MoveHead(_selected.Out); break;
            default: return;
        }
        e.Handled = true;
    }

    // ---------------------------------------------------------------- clean

    private bool IsAnalog => AnalogChoice.IsChecked == true;
    private int SizePercent => Size200.IsChecked == true ? 200 : Size150.IsChecked == true ? 150 : 100;

    private void Signal_Changed(object? sender, RoutedEventArgs e)
    {
        if (ColourCard == null || BlockCard == null) return;
        ColourCard.IsVisible = IsAnalog;
        BlockCard.IsVisible = !IsAnalog;
        SignalNote.Text = IsAnalog
            ? "Analog: rainbow stripes, black-and-white dropouts and colour noise are rebuilt from neighbouring frames."
            : "HDZero: block edges and broken macroblocks are softened or replaced from a neighbouring frame.";
        RefreshModelChoices();
        UpdateCleanHint();
    }

    private void Setting_Changed(object? sender, RoutedEventArgs e)
    {
        if (ColourValue == null || BlockValue == null || DetailValue == null || MotionValue == null || NoiseValue == null) return;
        ColourValue.Text = $"{(int)ColorSlider.Value}";
        BlockValue.Text = $"{(int)BlockSlider.Value}";
        DetailValue.Text = $"{(int)DetailSlider.Value}";
        MotionValue.Text = $"{(int)MotionSlider.Value}";
        NoiseValue.Text = $"{(int)NoiseSlider.Value}";
        UpdateCleanHint();
    }

    private void UpdateCleanHint()
    {
        if (CleanHint == null || RemoveOsd == null) return;
        var parts = new List<string> { IsAnalog ? "Analog" : "HDZero" };
        if (NoiseSlider.Value > 0) parts.Add("denoised");
        if (RemoveOsd.IsChecked == true) parts.Add("OSD removed");
        if (SizePercent != 100) parts.Add((SizePercent / 100.0).ToString("0.#", CultureInfo.InvariantCulture) + "×");
        if (Effort != "balanced") parts.Add(Effort);
        CleanHint.Text = string.Join(" · ", parts);
    }

    private string Effort => EffortFast?.IsChecked == true ? "fast" : EffortBest?.IsChecked == true ? "best" : "balanced";

    private void Effort_Changed(object? sender, RoutedEventArgs e)
    {
        if (EffortNote == null) return;
        EffortNote.Text = Effort switch
        {
            "fast" => "Quickest export. Fewer neighbouring frames and coarser motion; fine for sharing and checking a flight.",
            "best" => "Most neighbouring frames and the finest motion. Takes the longest; for the final cut.",
            _ => "Recommended. Nearly the quality of Best in clearly less time.",
        };
        UpdateCleanHint();
    }

    private void Mode_Changed(object? sender, SelectionChangedEventArgs e)
    {
        if (ModelPanel != null) ModelPanel.IsVisible = ModeChoice.SelectedIndex == 1;
    }

    private object BuildRecipe(FootageItem clip) => new
    {
        signalart = IsAnalog ? "analog" : "digital",
        farbberuhigung = (int)ColorSlider.Value,
        blockpflege = (int)BlockSlider.Value,
        kantendetail = (int)DetailSlider.Value,
        vergroesserung_prozent = SizePercent,
        bewegungsspur = (int)MotionSlider.Value,
        schnittbeginn = clip.In,
        schnittende = clip.Out,
        untertiteltext = CaptionText.Text ?? "",
        kompressionswert = (int)(QualityValue.Value ?? 18),
        dateiziel_mb = (int)(SizeCap.Value ?? 0),
        osd_entfernen = RemoveOsd.IsChecked == true,
        rauschminderung = (int)NoiseSlider.Value,
        aufwand = Effort,
    };

    private string Mode => ModeChoice.SelectedIndex == 1 ? "model" : "manual";

    private async void Preview_Click(object? sender, RoutedEventArgs e)
    {
        if (_selected == null) { PreviewStatus.Text = "Add and select a clip first."; return; }
        if (Mode == "model" && !File.Exists(ModelPath.Text)) { PreviewStatus.Text = "Choose a trained model or switch the engine to Standard."; return; }
        var clip = _selected;
        var moment = Math.Clamp(_head, clip.In, Math.Max(clip.In, clip.Out - 0.3));
        var before = TempFile("before");
        var after = TempFile("after");
        PreviewButton.IsEnabled = false;
        PreviewTitle.Text = $"{clip} at {Clock(moment)}";
        PreviewStatus.Text = "Rendering the frame through every selected step…";
        try
        {
            await EngineAsync(new { action = "preview", source = clip.Path, seconds = moment, before, after,
                recipe = BuildRecipe(clip), mode = Mode, model = ModelPath.Text });
            CompareBefore.Source = LoadBitmap(before);
            CompareAfter.Source = LoadBitmap(after);
            CompareHint.IsVisible = false;
            CompareDivider.IsVisible = true;
            PreviewStatus.Text = "Drag the slider to move the split line.";
            UpdateCompare();
        }
        catch (Exception ex) { PreviewStatus.Text = $"Preview failed: {ex.Message}"; }
        finally { File.Delete(before); File.Delete(after); PreviewButton.IsEnabled = true; }
    }

    private async void DetectOsd_Click(object? sender, RoutedEventArgs e)
    {
        if (_selected == null) { OsdStatus.Text = "Add and select a clip first."; return; }
        var clip = _selected;
        var target = TempFile("osd");
        DetectOsdButton.IsEnabled = false;
        OsdStatus.Text = $"Looking for overlays in {clip}…";
        try
        {
            var outcome = await EngineAsync(new { action = "osd", source = clip.Path, start = clip.In, end = clip.Out,
                seconds = Math.Clamp(_head, clip.In, clip.Out), target });
            var marked = LoadBitmap(target);
            CompareBefore.Source = marked; CompareAfter.Source = marked; CompareHint.IsVisible = false;
            PreviewTitle.Text = "Overlays marked in orange";
            OsdStatus.Text = outcome.GetProperty("found").GetBoolean()
                ? $"Found overlays on {outcome.GetProperty("coverage").GetDouble():0.0}% of the picture. Tick the box to remove them."
                : "No overlays found in this cut.";
        }
        catch (Exception ex) { OsdStatus.Text = $"OSD search failed: {ex.Message}"; }
        finally { File.Delete(target); DetectOsdButton.IsEnabled = true; }
    }

    private void Compare_Changed(object? sender, Avalonia.Controls.Primitives.RangeBaseValueChangedEventArgs e) => UpdateCompare();
    private void Compare_SizeChanged(object? sender, SizeChangedEventArgs e) => UpdateCompare();

    private void UpdateCompare()
    {
        if (CompareCanvas == null || CompareAfter == null || CompareDivider == null) return;
        var width = CompareCanvas.Bounds.Width;
        var edge = width * CompareSlider.Value / 100;
        CompareAfter.Clip = new RectangleGeometry(new Rect(edge, 0, Math.Max(0, width - edge), CompareCanvas.Bounds.Height));
        CompareDivider.Margin = new Thickness(edge - 1, 0, 0, 0);
    }

    // ---------------------------------------------------------------- models

    private static bool IsCompatibleModel(string path, string signal)
    {
        try
        {
            using var data = JsonDocument.Parse(File.ReadAllText(path));
            return data.RootElement.GetProperty("format").GetString() is
                       "fpv-blind-neighborhood-1" or "fpv-motion-context-2" &&
                   data.RootElement.GetProperty("signalart").GetString() == signal;
        }
        catch { return false; }
    }

    private static IEnumerable<string> ModelFiles()
    {
        var folders = new List<string> { ModelFolder() };
        try { folders.Add(System.IO.Path.Combine(RepoRoot(), ".scratch", "training", "profiles")); }
        catch (InvalidOperationException) { }
        return folders.Where(Directory.Exists)
            .SelectMany(folder => Directory.EnumerateFiles(folder, "*.json"))
            .OrderByDescending(File.GetLastWriteTimeUtc);
    }

    private void RefreshModelChoices()
    {
        if (ModelChoice == null || AnalogChoice == null) return;
        var signal = IsAnalog ? "analog" : "digital";
        var previous = ModelPath?.Text;
        ModelChoice.Items.Clear();
        foreach (var path in ModelFiles().Where(path => IsCompatibleModel(path, signal)))
            ModelChoice.Items.Add(new ComboBoxItem { Content = System.IO.Path.GetFileNameWithoutExtension(path), Tag = path });
        var selected = ModelChoice.Items.OfType<ComboBoxItem>().FirstOrDefault(item => Equals(item.Tag, previous));
        ModelChoice.SelectedItem = selected ?? ModelChoice.Items.OfType<ComboBoxItem>().FirstOrDefault();
        if (ModelChoice.Items.Count == 0 && ModelPath != null) ModelPath.Text = "";
    }

    private void RefreshResumeChoices()
    {
        if (ResumeChoice == null || TrainSignal == null) return;
        var signal = TrainSignal.SelectedIndex == 0 ? "analog" : "digital";
        ResumeChoice.Items.Clear();
        foreach (var path in ModelFiles().Where(path => IsCompatibleModel(path, signal)))
            ResumeChoice.Items.Add(new ComboBoxItem { Content = System.IO.Path.GetFileNameWithoutExtension(path), Tag = path });
        ResumeChoice.SelectedItem = ResumeChoice.Items.OfType<ComboBoxItem>().FirstOrDefault();
    }

    private void ModelChoice_Changed(object? sender, SelectionChangedEventArgs e)
    {
        if (ModelPath != null && ModelChoice?.SelectedItem is ComboBoxItem entry) ModelPath.Text = entry.Tag as string;
    }

    private async void ModelBrowse_Click(object? sender, RoutedEventArgs e)
    {
        var files = await StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions { AllowMultiple = false });
        var path = files.Count > 0 ? files[0].TryGetLocalPath() : null;
        if (path == null) return;
        if (!IsCompatibleModel(path, IsAnalog ? "analog" : "digital")) { PreviewStatus.Text = "That file is not a model for this signal type."; return; }
        ModelPath.Text = path;
    }

    private void TrainSignal_Changed(object? sender, SelectionChangedEventArgs e) => RefreshResumeChoices();
    private void TrainApproach_Changed(object? sender, SelectionChangedEventArgs e)
    {
        if (ResumePanel != null && TrainApproach != null) ResumePanel.IsVisible = TrainApproach.SelectedIndex == 1;
    }
    private void TrainUnit_Changed(object? sender, SelectionChangedEventArgs e)
    {
        if (TrainMinutes != null && TrainUnit != null) TrainMinutes.Maximum = TrainUnit.SelectedIndex == 1 ? 24 : 60;
    }
    private async void TrainFolder_Click(object? sender, RoutedEventArgs e) => TrainFolder.Text = await PickFolderAsync();

    private async void StartTrain_Click(object? sender, RoutedEventArgs e)
    {
        if (_trainingCancel != null) return;
        if (string.IsNullOrWhiteSpace(TrainFolder.Text) || !Directory.Exists(TrainFolder.Text)) { TrainStatus.Text = "Choose a folder with recordings."; return; }
        var signal = TrainSignal.SelectedIndex == 0 ? "analog" : "digital";
        var continuing = TrainApproach.SelectedIndex == 1;
        var initialModel = continuing ? (ResumeChoice.SelectedItem as ComboBoxItem)?.Tag as string : null;
        if (continuing && (initialModel == null || !IsCompatibleModel(initialModel, signal)))
        { TrainStatus.Text = "Choose one of your models for this signal type."; return; }
        var destination = System.IO.Path.Combine(ModelFolder(), $"{signal}-{DateTime.Now:yyyyMMdd-HHmmss}.json");
        TrainStatus.Text = "Sampling footage and training locally…"; TrainProgress.Value = 0;
        _trainingCancel = new CancellationTokenSource(); CancelTrainButton.IsEnabled = true;
        try
        {
            var seconds = (int)(TrainMinutes.Value ?? 5) * (TrainUnit.SelectedIndex == 1 ? 3600 : 60);
            var started = DateTime.UtcNow;
            var outcome = await EngineAsync(new { action = "train", folder = TrainFolder.Text, signal, seconds,
                initial_model = initialModel, target = destination },
                item => Dispatcher.UIThread.Post(() =>
                {
                    TrainProgress.Value = Math.Min(99, (DateTime.UtcNow - started).TotalSeconds / seconds * 100);
                    TrainStatus.Text = $"Learning from {item.GetProperty("samples").GetInt32():N0} samples…";
                }), _trainingCancel.Token);
            TrainProgress.Value = 100;
            TrainStatus.Text = $"Saved version {outcome.GetProperty("training_runs").GetInt32()} ({outcome.GetProperty("samples").GetInt32():N0} sampled pixels) to {destination}";
            RefreshModelChoices(); RefreshResumeChoices();
        }
        catch (OperationCanceledException) { TrainStatus.Text = "Training stopped."; }
        catch (Exception ex) { TrainStatus.Text = ex.Message; }
        finally { _trainingCancel.Dispose(); _trainingCancel = null; CancelTrainButton.IsEnabled = false; }
    }
    private void CancelTrain_Click(object? sender, RoutedEventArgs e) => _trainingCancel?.Cancel();

    // ---------------------------------------------------------------- export

    private async void OutputFolder_Click(object? sender, RoutedEventArgs e) => OutputFolder.Text = await PickFolderAsync();

    private void OpenOutput_Click(object? sender, RoutedEventArgs e)
    {
        if (Directory.Exists(OutputFolder.Text)) Process.Start(new ProcessStartInfo("explorer.exe", OutputFolder.Text!) { UseShellExecute = true });
    }

    private void UpdateExportSummary()
    {
        var total = _queue.Sum(clip => clip.Out - clip.In);
        if (_exportCancel == null) ExportStatus.Text = _queue.Count == 0 ? "Add clips in Import first." : "Ready to export.";
        ExportSummary.Text = _queue.Count == 0 ? "The queue is empty."
            : $"{_queue.Count} clip(s), {Clock(total)} of video · {CleanHint.Text}. Frame-by-frame cleaning takes several seconds per second of video.";
    }

    private async void Export_Clicked(object? sender, RoutedEventArgs e)
    {
        if (_exportCancel != null) return;
        if (_queue.Count == 0) { ExportStatus.Text = "Add clips in Import first."; return; }
        if (string.IsNullOrWhiteSpace(OutputFolder.Text) || !Directory.Exists(OutputFolder.Text)) { ExportStatus.Text = "Choose a destination folder."; return; }
        if (Mode == "model" && !File.Exists(ModelPath.Text)) { ExportStatus.Text = "Choose a trained model under Repair → Advanced."; return; }
        var clips = _queue.ToList();
        var folder = OutputFolder.Text!;
        var usedTargets = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var targets = new List<string>();
        foreach (var clip in clips)
        {
            var baseName = System.IO.Path.GetFileNameWithoutExtension(clip.Path);
            var target = System.IO.Path.Combine(folder, baseName + "-cleaned.mp4");
            for (var suffix = 2; !usedTargets.Add(target); suffix++)
                target = System.IO.Path.Combine(folder, $"{baseName}-cleaned-{suffix}.mp4");
            if (System.IO.Path.GetFullPath(target).Equals(System.IO.Path.GetFullPath(clip.Path), StringComparison.OrdinalIgnoreCase))
            { ExportStatus.Text = "Source and output must differ."; return; }
            targets.Add(target);
        }
        // Remaining time is weighted by cut length, so long clips count for more.
        var total = Math.Max(0.001, clips.Sum(clip => clip.Out - clip.In));
        _exportCancel = new CancellationTokenSource();
        _exportStarted = DateTime.UtcNow; _exportDone = 0;
        ExportButton.IsEnabled = false; CancelExportButton.IsEnabled = true;
        ExportProgress.Value = 0; ExportTime.Text = "Estimating remaining time…";
        _exportClock.Start();
        var finished = 0.0;
        try
        {
            for (var i = 0; i < clips.Count; i++)
            {
                var clip = clips[i];
                var target = targets[i];
                ExportStatus.Text = $"Rendering {i + 1} of {clips.Count}: {clip}";
                ExportHint.Text = $"Rendering {i + 1}/{clips.Count}";
                var before = finished;
                var share = (clip.Out - clip.In) / total;
                try
                {
                    await EngineAsync(new { action = "export", source = clip.Path, target, recipe = BuildRecipe(clip), mode = Mode, model = ModelPath.Text },
                        item =>
                        {
                            var done = before + share * item.GetProperty("fraction").GetDouble();
                            Dispatcher.UIThread.Post(() => { _exportDone = done; ExportProgress.Value = 100 * done; });
                        }, _exportCancel.Token);
                }
                catch (OperationCanceledException)
                {
                    RemovePartialOutput(target);
                    ExportStatus.Text = $"Export canceled. {i} of {clips.Count} clip(s) finished.";
                    ExportHint.Text = "Canceled";
                    return;
                }
                finished = before + share;
                _exportDone = finished;
                _reviewSource = clip.Path; _reviewOutput = target; _reviewStart = clip.In;
                ReviewSeek.Maximum = clip.Out - clip.In; ReviewSeek.Value = 0;
                ReviewPanel.IsVisible = true;
                await RefreshReviewAsync();
            }
            ExportProgress.Value = 100;
            ExportStatus.Text = $"Exported {clips.Count} clip(s) in {Duration(DateTime.UtcNow - _exportStarted)}.";
            ExportHint.Text = "Done";
        }
        catch (Exception ex) { ExportStatus.Text = $"Export failed: {ex.Message}"; ExportHint.Text = "Failed"; }
        finally
        {
            _exportClock.Stop(); ExportTime.Text = "";
            _exportCancel.Dispose(); _exportCancel = null;
            ExportButton.IsEnabled = true; CancelExportButton.IsEnabled = false;
        }
    }

    private void CancelExport_Click(object? sender, RoutedEventArgs e)
    {
        if (_exportCancel == null) return;
        CancelExportButton.IsEnabled = false;
        ExportStatus.Text = "Canceling…";
        _exportCancel.Cancel();
    }

    private void ShowExportTime()
    {
        var elapsed = DateTime.UtcNow - _exportStarted;
        // The first seconds go to OSD search and decoder start-up; wait for real
        // progress before projecting a finishing time.
        if (_exportDone < 0.005 || elapsed.TotalSeconds < 3)
        { ExportTime.Text = $"Elapsed {Duration(elapsed)} · estimating remaining time…"; return; }
        var remaining = TimeSpan.FromSeconds(elapsed.TotalSeconds * (1 - _exportDone) / _exportDone);
        ExportTime.Text = $"{100 * _exportDone:0}% · about {Duration(remaining)} left · elapsed {Duration(elapsed)}";
    }

    private static void RemovePartialOutput(string target)
    {
        // The engine renders into ".<name>-<random>.mp4" next to the target and
        // only renames it on success; a stopped engine leaves that file behind.
        var folder = System.IO.Path.GetDirectoryName(target);
        if (folder == null || !Directory.Exists(folder)) return;
        var pattern = $".{System.IO.Path.GetFileNameWithoutExtension(target)}-*{System.IO.Path.GetExtension(target)}";
        foreach (var leftover in Directory.EnumerateFiles(folder, pattern))
        {
            try { File.Delete(leftover); }
            catch (IOException) { /* Still locked by the closing encoder; harmless. */ }
        }
    }

    private async Task RefreshReviewAsync()
    {
        if (_loadingReview || _reviewSource == null || _reviewOutput == null) return;
        _loadingReview = true;
        var before = TempFile("review-before");
        var after = TempFile("review-after");
        try
        {
            var moment = ReviewSeek.Value;
            await Task.WhenAll(
                EngineAsync(new { action = "frame", source = _reviewSource, seconds = _reviewStart + moment, target = before }),
                EngineAsync(new { action = "frame", source = _reviewOutput, seconds = moment, target = after }));
            BeforeImage.Source = LoadBitmap(before);
            AfterImage.Source = LoadBitmap(after);
            ReviewTime.Text = Clock(moment);
            UpdateWipe();
        }
        catch (Exception ex) { ExportStatus.Text = $"Review frame unavailable: {ex.Message}"; }
        finally { File.Delete(before); File.Delete(after); _loadingReview = false; }
    }

    private async void ReviewSeek_Changed(object? sender, Avalonia.Controls.Primitives.RangeBaseValueChangedEventArgs e)
    { if (_reviewSource != null) await RefreshReviewAsync(); }
    private void Wipe_Changed(object? sender, Avalonia.Controls.Primitives.RangeBaseValueChangedEventArgs e) => UpdateWipe();
    private void Review_SizeChanged(object? sender, SizeChangedEventArgs e) => UpdateWipe();

    private void UpdateWipe()
    {
        if (ReviewCanvas == null || AfterImage == null || Divider == null || WipeSlider == null) return;
        var width = ReviewCanvas.Bounds.Width;
        var edge = width * WipeSlider.Value / 100;
        AfterImage.Clip = new RectangleGeometry(new Rect(edge, 0, Math.Max(0, width - edge), ReviewCanvas.Bounds.Height));
        Divider.Margin = new Thickness(edge - 1, 0, 0, 0);
    }
}
