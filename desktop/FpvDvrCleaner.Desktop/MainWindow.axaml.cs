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
    private sealed class FootageItem(string path, double duration, bool hasAudio, int width, int height, double fps)
    {
        public string Path { get; } = path;
        public double Duration { get; } = duration;
        public bool HasAudio { get; } = hasAudio;
        public int Width { get; } = width;
        public int Height { get; } = height;
        public double Fps { get; } = fps;
        /// <summary>Hand corrections of the OSD mask for this recording (a PNG in the settings folder), or null.</summary>
        public string? ManualMask { get; set; }
        /// <summary>The picture inside black side bars; null until measured or when there are none.</summary>
        public (int X, int Y, int Width, int Height)? Box { get; set; }
        public bool BoxRequested { get; set; }
        public bool BoxMeasured { get; set; }
        /// <summary>The kept parts, sorted and non-overlapping; at least one.</summary>
        public List<(double In, double Out)> Segments { get; set; } = [(0, duration)];
        public List<double> Bookmarks { get; set; } = [];
        public double In => Segments.Count > 0 ? Segments[0].In : 0;
        public double Out => Segments.Count > 0 ? Segments[^1].Out : Duration;
        public double Kept => Segments.Sum(s => s.Out - s.In);
        public List<(double Seconds, Bitmap Picture)> Strip { get; } = [];
        public bool StripRequested { get; set; }
        public float[]? Waveform { get; set; }
        public bool WaveformRequested { get; set; }
        public Stack<(List<(double In, double Out)> Segments, List<double> Bookmarks)> Undo { get; } = new();
        public Stack<(List<(double In, double Out)> Segments, List<double> Bookmarks)> Redo { get; } = new();
        public override string ToString() => System.IO.Path.GetFileName(Path);

        public FootageItem Copy()
        {
            var copy = new FootageItem(Path, Duration, HasAudio, Width, Height, Fps) { Box = Box, BoxRequested = BoxRequested, BoxMeasured = BoxMeasured, Segments = Segments.ToList(), Bookmarks = Bookmarks.ToList(),
                Waveform = Waveform, WaveformRequested = WaveformRequested, StripRequested = StripRequested, ManualMask = ManualMask };
            copy.Strip.AddRange(Strip);
            return copy;
        }
    }

    private readonly List<FootageItem> _queue = [];
    private readonly UserSettings _settings = UserSettings.Load();
    private FootageItem? _selected;
    private int _page;
    private double _head;
    private bool _pickingClip;
    private bool _applyingPreset;
    private bool _hoverPaused;
    private int _frameRequest;
    private readonly DispatcherTimer _frameDelay = new() { Interval = TimeSpan.FromMilliseconds(180) };
    private readonly FramePlayer _player = new(FfmpegPath());
    private bool _playing;
    private string? _reviewSource;
    private string? _reviewOutput;
    private List<(double In, double Out)> _reviewSegments = [];
    private FootageItem? _reviewClip;
    private (int Width, int Height, double Fps) _reviewOutInfo;
    private readonly FramePlayer _reviewPair = new(FfmpegPath());
    private bool _reviewPlaying;
    private bool _reviewSeeking;
    private int _reviewSegmentPlaying = -1;
    private double _assessedHeadroom = -1, _assessedLoss, _assessedGain;
    private bool _secondPassWorth;
    private bool _loadingReview;
    private CancellationTokenSource? _trainingCancel;
    private CancellationTokenSource? _exportCancel;
    private readonly DispatcherTimer _exportClock = new() { Interval = TimeSpan.FromSeconds(1) };
    private DateTime _exportStarted;
    private double _exportDone;
    private static readonly string[] Extensions = [".mp4", ".mov", ".mkv", ".avi", ".m4v", ".ts", ".webm"];
    /// <summary>Output formats of the Export page: engine name, file extension, label and one-line hint.</summary>
    private static readonly (string Id, string Extension, string Title, string Hint)[] Formats =
    [
        ("mp4-h264", ".mp4", "MP4 · H.264", UiText.FormatHintH264),
        ("mp4-hevc", ".mp4", "MP4 · H.265 / HEVC", UiText.FormatHintHevc),
        ("mov-prores", ".mov", "MOV · ProRes 422", UiText.FormatHintProres),
        ("mkv-h264", ".mkv", "MKV · H.264", UiText.FormatHintMkv),
        ("webm-vp9", ".webm", "WebM · VP9 + Opus", UiText.FormatHintWebm),
    ];
    private static readonly string[] Titles = [UiText.TitleImport, UiText.TitleTrim, UiText.TitleRepair, UiText.TitleExport, UiText.TitleTraining];
    private static readonly string[] Subtitles =
    [
        UiText.SubtitleImport,
        UiText.SubtitleTrim,
        UiText.SubtitleRepair,
        UiText.SubtitleExport,
        UiText.SubtitleTraining,
    ];

    public MainWindow()
    {
        InitializeComponent();
        _frameDelay.Tick += async (_, _) => { _frameDelay.Stop(); await RefreshFrameAsync(); };
        _player.FrameShown += OnPlaybackFrame;
        _player.Ended += () => { if (_playing) ContinueOrStop(); };
        _reviewPair.PairShown += OnReviewPair;
        _reviewPair.Ended += () => StopReviewPlayback();
        _exportClock.Tick += (_, _) => ShowExportTime();
        HookTimeline();
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
            RestoreSettings();
            FillLanguageChoices();
            SetUpRepairPage();
            RefreshModelChoices(); RefreshResumeChoices(); ShowPage(0); UpdateCompare();
            // Files passed on the command line ("Open with", dropped on the exe) join the queue.
            var given = Environment.GetCommandLineArgs().Skip(1).Where(File.Exists).ToList();
            if (given.Count > 0) await AddFilesAsync(given);
        };
        Closing += (_, _) => { _previewCancel?.Cancel(); _player.Dispose(); _reviewPair.Dispose(); RememberSettings(); };
    }

    // ---------------------------------------------------------------- busy overlay

    private Action? _busyCancel;

    /// <summary>Only the progress card stays sharp; the page behind it is dimmed and blurred.</summary>
    private void ShowBusy(string title, string text = "", Action? cancel = null)
    {
        _busyCancel = cancel;
        BusyTitle.Text = title;
        BusyText.Text = text;
        BusyTime.Text = "";
        BusyBar.IsIndeterminate = true;
        BusyCancel.IsVisible = cancel != null;
        BusyCancel.IsEnabled = true;
        BusyOverlay.IsVisible = true;
        PageScroller.Effect = new BlurEffect { Radius = 10 };
    }

    private void UpdateBusy(double? fraction, string? text = null, string? time = null)
    {
        if (!BusyOverlay.IsVisible) return;
        if (fraction is { } value) { BusyBar.IsIndeterminate = false; BusyBar.Value = Math.Clamp(value * 100, 0, 100); }
        if (text != null) BusyText.Text = text;
        if (time != null) BusyTime.Text = time;
    }

    private void HideBusy()
    {
        BusyOverlay.IsVisible = false;
        PageScroller.Effect = null;
        _busyCancel = null;
    }

    private void BusyCancel_Click(object? sender, RoutedEventArgs e)
    {
        BusyCancel.IsEnabled = false;
        BusyText.Text = UiText.Canceling;
        _busyCancel?.Invoke();
    }

    // ---------------------------------------------------------------- settings

    /// <summary>Folders chosen last time come back, so nobody has to type them again.</summary>
    private void RestoreSettings()
    {
        if (Directory.Exists(_settings.OutputFolder)) _outputFolder = _settings.OutputFolder;
        ShowOutputFolder();
        if (Directory.Exists(_settings.TrainFolder)) TrainFolder.Text = _settings.TrainFolder;
        SuffixBox.Text = string.IsNullOrWhiteSpace(_settings.OutputSuffix) ? "-cleaned" : _settings.OutputSuffix;
        FillFormatChoices(_settings.OutputFormat);
        JoinClips.IsChecked = _settings.JoinClips;
        OpenAfterExport.IsChecked = _settings.OpenFolderAfterExport;
        SnapToggle.IsChecked = _settings.Snap;
        Timeline.Snap = _settings.Snap;
        if (_settings.LastRepair != null) ApplyRepair(_settings.LastRepair);
        else ShowLook();
        ReviewSideBySide.IsChecked = true; ReviewWipeMode.IsChecked = false;
        ReviewMode_Changed(null, new RoutedEventArgs());
        TrainFolder.LostFocus += (_, _) => RememberSettings();
    }

    private void RememberSettings()
    {
        // The default folder next to the first clip is not remembered: it follows the clips.
        _settings.OutputFolder = _outputFolder;
        _settings.TrainFolder = TrainFolder.Text?.Trim();
        _settings.OutputSuffix = SuffixBox.Text?.Trim() ?? "";
        _settings.OutputFormat = OutputFormat.Id;
        _settings.JoinClips = JoinClips.IsChecked == true;
        _settings.OpenFolderAfterExport = OpenAfterExport.IsChecked == true;
        _settings.Snap = SnapToggle.IsChecked == true;
        _settings.LastRepair = CurrentRepair();
        _settings.LastPreset = _basePreset is { } basis ? PresetKey(basis) : null;
        foreach (var clip in _queue) _settings.RememberCut(clip.Path, clip.Segments, clip.Bookmarks, clip.ManualMask);
        _settings.Save();
    }

    // ---- language

    private bool _fillingLanguages;

    /// <summary>"System language (…)" first, then every language in its own name.</summary>
    private void FillLanguageChoices()
    {
        _fillingLanguages = true;
        LanguageChoice.Items.Clear();
        var system = Loc.Languages.First(language => language.Code == Loc.Detect(CultureInfo.CurrentUICulture));
        LanguageChoice.Items.Add(new ComboBoxItem { Content = UiText.F(UiText.LanguageAuto, system.Name), Tag = "auto" });
        foreach (var language in Loc.Languages)
            LanguageChoice.Items.Add(new ComboBoxItem { Content = language.Name, Tag = language.Code });
        LanguageChoice.SelectedItem = LanguageChoice.Items.OfType<ComboBoxItem>().FirstOrDefault(item => Equals(item.Tag, _settings.Language))
                                      ?? LanguageChoice.Items[0];
        _fillingLanguages = false;
    }

    /// <summary>Every text is read once at start, so a new language restarts the app with the clips of the queue.</summary>
    private void Language_Changed(object? sender, SelectionChangedEventArgs e)
    {
        if (_fillingLanguages || LanguageChoice.SelectedItem is not ComboBoxItem { Tag: string code } || code == _settings.Language) return;
        _settings.Language = code;
        RememberSettings();
        if (_exportCancel != null || _trainingCancel != null)
        {
            LanguageNote.Text = UiText.LanguageWaitBusy;
            LanguageNote.IsVisible = true;
            return;
        }
        if (Environment.ProcessPath is not { } executable) return;
        var restart = new ProcessStartInfo(executable) { UseShellExecute = false, WorkingDirectory = AppContext.BaseDirectory };
        foreach (var path in _queue.Select(clip => clip.Path).Distinct()) restart.ArgumentList.Add(path);
        try { Process.Start(restart); Close(); }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException) { }
    }

    private void ExportOption_Changed(object? sender, RoutedEventArgs e)
    {
        if (SuffixBox == null || JoinClips == null) return;
        RememberSettings();
    }

    // ---- output file format

    private bool _fillingFormats;

    /// <summary>One line per format: the name, then a short hint in grey.</summary>
    private void FillFormatChoices(string chosen)
    {
        _fillingFormats = true;
        FormatChoice.Items.Clear();
        foreach (var format in Formats)
        {
            var line = new StackPanel
            {
                Orientation = Avalonia.Layout.Orientation.Horizontal, Spacing = 8,
                Children =
                {
                    new TextBlock { Text = format.Title, FontWeight = FontWeight.SemiBold },
                    new TextBlock { Text = format.Hint, FontSize = 12, VerticalAlignment = Avalonia.Layout.VerticalAlignment.Center,
                        Foreground = new SolidColorBrush(Color.Parse("#5F676D")) },
                },
            };
            FormatChoice.Items.Add(new ComboBoxItem { Content = line, Tag = format.Id });
        }
        // An unknown or missing name (older settings) falls back to MP4 · H.264.
        FormatChoice.SelectedIndex = Math.Max(0, Array.FindIndex(Formats, format => format.Id == chosen));
        _fillingFormats = false;
        ShowFormatNote();
    }

    private (string Id, string Extension, string Title, string Hint) OutputFormat =>
        FormatChoice?.SelectedItem is ComboBoxItem { Tag: string id } && Formats.Any(format => format.Id == id)
            ? Formats.First(format => format.Id == id) : Formats[0];

    private bool FormatUsesQuality => OutputFormat.Id != "mov-prores";

    private void Format_Changed(object? sender, SelectionChangedEventArgs e)
    {
        if (_fillingFormats || FormatNote == null) return;
        ShowFormatNote();
        RememberSettings();
    }

    /// <summary>A purpose card picks the format; the full list stays under More options.</summary>
    private void Purpose_Changed(object? sender, RoutedEventArgs e)
    {
        if (_fillingFormats || sender is not RadioButton { IsChecked: true, Tag: string id }) return;
        var index = Array.FindIndex(Formats, format => format.Id == id);
        if (index >= 0 && FormatChoice.SelectedIndex != index) FormatChoice.SelectedIndex = index;
    }

    private RadioButton[] PurposeButtons => [PurposeShare, PurposeSmall, PurposeEdit, PurposeWeb];

    private void ShowFormatNote()
    {
        // ProRes stores a fixed data rate: quality and size limit have nothing to set.
        QualityValue.IsEnabled = SizeCap.IsEnabled = FormatUsesQuality;
        _fillingFormats = true;
        foreach (var card in PurposeButtons) card.IsChecked = card.Tag as string == OutputFormat.Id;
        _fillingFormats = false;
        if (PurposeButtons.All(card => card.IsChecked != true))
        {
            FormatNote.Text = string.Format(CultureInfo.CurrentCulture, UiText.PurposeOther, OutputFormat.Title) + " " + OutputFormat.Hint + ".";
            return;
        }
        FormatNote.Text = OutputFormat.Id switch
        {
            "mp4-hevc" => UiText.FormatNoteHevc,
            "mov-prores" => UiText.FormatNoteProres,
            "mkv-h264" => UiText.FormatNoteMkv,
            "webm-vp9" => UiText.FormatNoteWebm,
            _ => UiText.FormatNoteH264,
        };
    }

    private static void OpenInFileManager(string folder)
    {
        if (OperatingSystem.IsWindows()) Process.Start(new ProcessStartInfo("explorer.exe", folder) { UseShellExecute = true });
        else if (OperatingSystem.IsMacOS()) Process.Start("open", [folder]);
        else Process.Start("xdg-open", [folder]);
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
        throw new InvalidOperationException(UiText.EngineMissing);
    }

    private static string ModelFolder()
    {
        var folder = System.IO.Path.Combine(UserSettings.Folder(), "models");
        Directory.CreateDirectory(folder);
        return folder;
    }

    private static string FfmpegPath()
    {
        var name = OperatingSystem.IsWindows() ? "ffmpeg.exe" : "ffmpeg";
        var bundled = System.IO.Path.Combine(AppContext.BaseDirectory, "ffmpeg", name);
        return File.Exists(bundled) ? bundled : "ffmpeg";
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
            using var process = Process.Start(start) ?? throw new InvalidOperationException(UiText.EngineNoStart);
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
                throw new InvalidOperationException(Loc.Engine(fault ?? await errorTask));
            return result;
        }
        finally { DeleteTemp(ticket); }
    }

    private static Bitmap LoadBitmap(string path)
    {
        using var stream = File.OpenRead(path);
        return new Bitmap(stream);
    }

    /// <summary>A small vector icon for a button: text glyphs such as ▲ or × sit off-centre in most fonts.</summary>
    private static Avalonia.Controls.Shapes.Path VectorIcon(string data, string colour, bool filled)
    {
        var brush = new SolidColorBrush(Color.Parse(colour));
        return new Avalonia.Controls.Shapes.Path
        {
            Data = Geometry.Parse(data), Width = 10, Height = 10, Stretch = Stretch.None,
            Fill = filled ? brush : null, Stroke = filled ? null : brush, StrokeThickness = 1.8, StrokeLineCap = PenLineCap.Round, StrokeJoin = PenLineJoin.Round,
            HorizontalAlignment = Avalonia.Layout.HorizontalAlignment.Center, VerticalAlignment = Avalonia.Layout.VerticalAlignment.Center,
        };
    }

    private static string TempFile(string stem, string extension = ".png") =>
        System.IO.Path.Combine(System.IO.Path.GetTempPath(), $"fpv-{stem}-{Guid.NewGuid():N}{extension}");

    /// <summary>Removes temporary files; one still held by a just-cancelled engine run stays behind in the temp folder instead of crashing the app.</summary>
    private static void DeleteTemp(params string[] paths)
    {
        foreach (var path in paths)
            try { File.Delete(path); }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
    }

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
        if (index != 3) StopReviewPlayback();
        _page = index;
        var pages = new Control[] { ImportPage, TrimPage, CleanPage, ExportPage, TrainPage };
        var steps = new[] { ImportStep, TrimStep, CleanStep, ExportStep, TrainStep };
        for (var i = 0; i < pages.Length; i++) { pages[i].IsVisible = i == index; steps[i].Classes.Set("current", i == index); }
        PageTitle.Text = Titles[index];
        PageSubtitle.Text = Subtitles[index];
        BackButton.IsVisible = index is > 0 and < 4;
        NextButton.IsVisible = index < 3;
        NextButton.Content = index switch { 0 => UiText.NextTrim, 1 => UiText.NextRepair, _ => UiText.NextExport };
        FooterNote.Text = index switch
        {
            0 => _queue.Count == 0 ? UiText.FooterAddFirst : UiText.FooterSelectClip,
            1 => UiText.FooterOwnCut,
            2 => UiText.FooterAllClips,
            _ => "",
        };
        // Trim, Repair and Export fill the window exactly; their own parts scroll if they must.
        PageScroller.VerticalScrollBarVisibility = index is >= 1 and <= 3
            ? Avalonia.Controls.Primitives.ScrollBarVisibility.Disabled : Avalonia.Controls.Primitives.ScrollBarVisibility.Auto;
        FitTrimPage();
        if (index == 1 && _selected != null) { LayoutStrip(); RequestFrame(); }
        if (index == 2) { ShowPresetState(); RequestAutoRender(); }
        else { _autoRender.Stop(); _previewCancel?.Cancel(); }
        if (index == 3) { ShowOutputFolder(); UpdateExportSummary(); }
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
        if (_page == 0 && _queue.Count == 0) { ImportStatus.Text = UiText.ImportAddFirst; return; }
        ShowPage(Math.Min(3, _page + 1));
    }

    // ---------------------------------------------------------------- import

    private static IEnumerable<string> VideosIn(string folder) =>
        Directory.EnumerateFiles(folder, "*", SearchOption.AllDirectories)
            .Where(file => Extensions.Contains(System.IO.Path.GetExtension(file).ToLowerInvariant()));

    private async Task<string?> PickFolderAsync(string? startIn = null)
    {
        var options = new FolderPickerOpenOptions { AllowMultiple = false };
        if (Directory.Exists(startIn))
            options.SuggestedStartLocation = await StorageProvider.TryGetFolderFromPathAsync(startIn);
        var folders = await StorageProvider.OpenFolderPickerAsync(options);
        return folders.Count > 0 ? folders[0].TryGetLocalPath() : null;
    }

    private async void Add_Click(object? sender, RoutedEventArgs e)
    {
        var files = await StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions { AllowMultiple = true, Title = UiText.HintImport });
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
        var list = paths.ToList();
        if (list.Count > 0) ShowBusy(UiText.ReadingRecordings, "");
        try
        {
        foreach (var path in list)
        {
            if (_queue.Any(clip => clip.Path.Equals(path, StringComparison.OrdinalIgnoreCase))) continue;
            ImportStatus.Text = UiText.F(UiText.ReadingFile, System.IO.Path.GetFileName(path));
            UpdateBusy(null, ImportStatus.Text);
            try
            {
                var info = await EngineAsync(new { action = "inspect", source = path });
                var clip = new FootageItem(path, info.GetProperty("duration").GetDouble(),
                    info.TryGetProperty("audio", out var audio) && audio.GetBoolean(),
                    info.GetProperty("width").GetInt32(), info.GetProperty("height").GetInt32(),
                    info.TryGetProperty("fps", out var fps) ? fps.GetDouble() : 0);
                if (_settings.RememberedCut(path) is { } memory)
                {
                    var segments = memory.Segments.Where(s => s.Length == 2 && s[1] - s[0] >= 0.1)
                        .Select(s => (Math.Clamp(s[0], 0, clip.Duration), Math.Clamp(s[1], 0, clip.Duration)))
                        .Where(s => s.Item2 - s.Item1 >= 0.1).OrderBy(s => s.Item1).ToList();
                    if (segments.Count > 0) clip.Segments = segments;
                    clip.Bookmarks = memory.Bookmarks.Where(b => b >= 0 && b <= clip.Duration).ToList();
                    clip.ManualMask = UserSettings.RememberedMask(memory);
                }
                _queue.Add(clip);
                _ = MeasureBoxAsync(clip);
                added++;
            }
            catch (Exception ex) { ImportStatus.Text = UiText.F(UiText.SkippedFile, System.IO.Path.GetFileName(path), Loc.Engine(ex.Message)); }
        }
        }
        finally { HideBusy(); }
        if (added > 0) ImportStatus.Text = (added == 1 ? UiText.AddedOne : UiText.F(UiText.AddedMany, added));
        DrawQueue();
        ShowOutputFolder();
        if (_selected == null && _queue.Count > 0) Select(_queue[0]);
        if (_page == 0) ShowPage(0);
    }

    private void DrawQueue()
    {
        QueueList.Children.Clear();
        QueueEmpty.IsVisible = _queue.Count == 0;
        CountLabel.Text = (_queue.Count == 1 ? UiText.CountOne : UiText.F(UiText.CountMany, _queue.Count));
        ImportHint.Text = _queue.Count == 0 ? UiText.HintImport : CountLabel.Text;
        foreach (var clip in _queue)
        {
            var row = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto,Auto,Auto,Auto,Auto"), Margin = new Thickness(0, 1),
                Background = new SolidColorBrush(Color.Parse(clip == _selected ? "#FFF0E6" : "#F6F6F3")) };
            var choose = new Button
            {
                Content = new StackPanel
                {
                    Children =
                    {
                        new TextBlock { Text = System.IO.Path.GetFileName(clip.Path), FontWeight = clip == _selected ? FontWeight.SemiBold : FontWeight.Normal,
                            Foreground = new SolidColorBrush(Color.Parse("#20252B")) },
                        new TextBlock { Text = QueueSummary(clip),
                            FontSize = 11, Foreground = new SolidColorBrush(Color.Parse("#5F676D")) },
                    },
                },
            };
            choose.Classes.Add("row");
            choose.Click += (_, _) => Select(clip);
            var index = _queue.IndexOf(clip);
            // Square icon buttons as tall as Copy and Trim; a disabled one keeps its background and fades its icon.
            Button Square(string data, string colour, bool filled, string tip)
            {
                var button = new Button { Content = VectorIcon(data, colour, filled), VerticalAlignment = Avalonia.Layout.VerticalAlignment.Center };
                button.Classes.Add("square");
                ToolTip.SetTip(button, tip);
                return button;
            }
            var up = Square("M1,7 L5,3 L9,7", "#20252B", false, UiText.MoveUp);
            up.IsEnabled = index > 0;
            up.Margin = new Thickness(6, 0, 0, 0);
            up.Click += (_, _) => { _queue.RemoveAt(index); _queue.Insert(index - 1, clip); DrawQueue(); };
            var down = Square("M1,3 L5,7 L9,3", "#20252B", false, UiText.MoveDown);
            down.IsEnabled = index < _queue.Count - 1;
            down.Margin = new Thickness(4, 0, 0, 0);
            down.Click += (_, _) => { _queue.RemoveAt(index); _queue.Insert(index + 1, clip); DrawQueue(); };
            var copy = new Button { Content = UiText.CopyClip, Height = 30, Margin = new Thickness(6, 0, 0, 0), VerticalAlignment = Avalonia.Layout.VerticalAlignment.Center, VerticalContentAlignment = Avalonia.Layout.VerticalAlignment.Center };
            ToolTip.SetTip(copy, UiText.CopyClipTip);
            copy.Click += (_, _) => { _queue.Insert(index + 1, clip.Copy()); DrawQueue(); };
            var trim = new Button { Content = UiText.TitleTrim, Height = 30, Margin = new Thickness(6, 0, 0, 0), VerticalAlignment = Avalonia.Layout.VerticalAlignment.Center, VerticalContentAlignment = Avalonia.Layout.VerticalAlignment.Center };
            trim.Click += (_, _) => { Select(clip); ShowPage(1); };
            var remove = Square("M1.5,1.5 L8.5,8.5 M8.5,1.5 L1.5,8.5", "#9C3F1F", false, UiText.RemoveFromQueue);
            remove.Margin = new Thickness(4, 0, 0, 0);
            remove.Classes.Add("remove");
            remove.Click += (_, _) =>
            {
                _queue.Remove(clip);
                if (_selected == clip) { _selected = null; if (_queue.Count > 0) Select(_queue[0]); else ClearSelection(); }
                DrawQueue();
            };
            Grid.SetColumn(up, 1); Grid.SetColumn(down, 2); Grid.SetColumn(copy, 3); Grid.SetColumn(trim, 4); Grid.SetColumn(remove, 5);
            row.Children.Add(choose); row.Children.Add(up); row.Children.Add(down); row.Children.Add(copy); row.Children.Add(trim); row.Children.Add(remove);
            QueueList.Children.Add(row);
        }
        _pickingClip = true;
        ClipPicker.ItemsSource = _queue.ToList();
        ClipPicker.SelectedItem = _selected;
        _pickingClip = false;
    }

    /// <summary>Finds the 4:3 picture inside the bars once per clip, for cropped previews.</summary>
    private async Task MeasureBoxAsync(FootageItem clip)
    {
        if (clip.BoxRequested) return;
        clip.BoxRequested = true;
        try
        {
            var box = await EngineAsync(new { action = "picture", source = clip.Path });
            var found = (box.GetProperty("x").GetInt32(), box.GetProperty("y").GetInt32(),
                box.GetProperty("width").GetInt32(), box.GetProperty("height").GetInt32());
            clip.Box = found.Item3 < clip.Width || found.Item4 < clip.Height ? found : null;
        }
        catch { clip.Box = null; }
        clip.BoxMeasured = true;
        SettleSignal(clip);
    }

    /// <summary>A box scaled to another frame size (the cleaned file may be upscaled).</summary>
    private static (int X, int Y, int Width, int Height)? ScaledBox((int X, int Y, int Width, int Height)? box, int fromWidth, int toWidth)
    {
        if (box is not { } b || fromWidth <= 0 || toWidth == fromWidth) return box;
        var factor = (double)toWidth / fromWidth;
        return ((int)Math.Round(b.X * factor), (int)Math.Round(b.Y * factor), (int)Math.Round(b.Width * factor), (int)Math.Round(b.Height * factor));
    }

    /// <summary>The still shown without its black bars.</summary>
    private static IImage Cropped(Bitmap bitmap, (int X, int Y, int Width, int Height)? box, int sourceWidth)
    {
        var scaled = ScaledBox(box, sourceWidth, bitmap.PixelSize.Width);
        if (scaled is not { } b || b.Width <= 0 || b.Height <= 0) return bitmap;
        var rect = new PixelRect(Math.Clamp(b.X, 0, bitmap.PixelSize.Width - 1), Math.Clamp(b.Y, 0, bitmap.PixelSize.Height - 1),
            Math.Min(b.Width, bitmap.PixelSize.Width - Math.Clamp(b.X, 0, bitmap.PixelSize.Width - 1)),
            Math.Min(b.Height, bitmap.PixelSize.Height - Math.Clamp(b.Y, 0, bitmap.PixelSize.Height - 1)));
        return new CroppedBitmap(bitmap, rect);
    }

    /// <summary>Where a picture of the given aspect sits inside a canvas (Stretch=Uniform, centred).</summary>
    private static Rect ImageRect(Control canvas, double aspect)
    {
        var width = canvas.Bounds.Width;
        var height = canvas.Bounds.Height;
        if (width <= 0 || height <= 0 || aspect <= 0) return new Rect(0, 0, Math.Max(1, width), Math.Max(1, height));
        var shownWidth = Math.Min(width, height * aspect);
        var shownHeight = shownWidth / aspect;
        return new Rect((width - shownWidth) / 2, (height - shownHeight) / 2, shownWidth, shownHeight);
    }

    private static string QueueSummary(FootageItem clip)
    {
        var kept = clip.Segments.Count == 1 && clip.Kept >= clip.Duration - 0.05 ? UiText.KeepingEverything
            : clip.Segments.Count == 1 ? UiText.F(UiText.KeepingRange, Clock(clip.In), Clock(clip.Out))
            : UiText.F(UiText.KeepingParts, Clock(clip.Kept), clip.Segments.Count);
        return UiText.F(UiText.ClipLong, Clock(clip.Duration), kept);
    }

    private void ClearSelection()
    {
        TrimPreview.Source = null;
        Timeline.Duration = 1; Timeline.SetSegments([]); Timeline.SetBookmarks([]); Timeline.SetThumbnails([]); Timeline.SetWaveform(null, false);
        StripStatus.Text = UiText.StripChoose;
        TrimHint.Text = UiText.HintTrim;
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
        Timeline.Duration = clip.Duration;
        Timeline.SetSegments(clip.Segments);
        Timeline.SetBookmarks(clip.Bookmarks);
        Timeline.SetThumbnails(clip.Strip);
        Timeline.SetWaveform(clip.Waveform, clip.HasAudio);
        DrawQueue();
        UpdateCutFields();
        UpdateUndoButtons();
        LayoutStrip();
        RequestFrame();
        if (!clip.StripRequested) _ = LoadStripAsync(clip);
        if (!clip.WaveformRequested && clip.HasAudio) _ = LoadWaveformAsync(clip);
        ShowOsdState();
        SettleSignal(clip);
        RequestAutoRender();
    }

    // ---------------------------------------------------------------- trim editor

    private async Task LoadStripAsync(FootageItem clip)
    {
        clip.StripRequested = true;
        StripStatus.Text = UiText.BuildingStrip;
        var folder = System.IO.Path.Combine(System.IO.Path.GetTempPath(), $"fpv-strip-{Guid.NewGuid():N}");
        try
        {
            var outcome = await EngineAsync(new { action = "filmstrip", source = clip.Path, folder, count = 240, height = 82 });
            foreach (var tile in outcome.GetProperty("tiles").EnumerateArray())
                clip.Strip.Add((tile.GetProperty("seconds").GetDouble(), LoadBitmap(tile.GetProperty("path").GetString()!)));
            if (_selected == clip)
            {
                Timeline.SetThumbnails(clip.Strip);
                StripStatus.Text = UiText.F(UiText.StripReady, clip.Strip.Count);
                ShowThumbnail(_head);
            }
        }
        catch (Exception ex) { clip.StripRequested = false; StripStatus.Text = UiText.F(UiText.StripFailed, Loc.Engine(ex.Message)); }
        finally { try { Directory.Delete(folder, true); } catch (IOException) { } }
    }

    private async Task LoadWaveformAsync(FootageItem clip)
    {
        clip.WaveformRequested = true;
        try
        {
            var outcome = await EngineAsync(new { action = "waveform", source = clip.Path, buckets = 2000 });
            clip.Waveform = outcome.GetProperty("levels").EnumerateArray().Select(level => (float)level.GetDouble()).ToArray();
            if (_selected == clip) Timeline.SetWaveform(clip.Waveform, clip.HasAudio);
        }
        catch { clip.Waveform = []; if (_selected == clip) Timeline.SetWaveform(clip.Waveform, false); }
    }

    private void HookTimeline()
    {
        Timeline.HeadChanged += seconds => { StopPlay(); _head = seconds; PlaceMarkers(); };
        Timeline.SelectionChanged += index =>
        {
            if (_selected == null) return;
            UpdateCutFields();
            StripStatus.Text = index >= 0 && _selected.Segments.Count > 1
                ? UiText.F(UiText.TrimPartSelected, index + 1)
                : index >= 0 ? UiText.TrimSplitHint
                : UiText.TrimRemovedHint;
        };
        Timeline.EditStarted += PushUndo;
        Timeline.SegmentsChanged += () =>
        {
            if (_selected == null) return;
            _selected.Segments = Timeline.Segments.ToList();
            UpdateCutFields();
        };
        Timeline.DragEnded += kind =>
        {
            _hoverPaused = true;
            HoverBadge.IsVisible = false;
            ShowThumbnail(_head);
            RequestFrame();
            if (kind != TimelineView.DragKind.Playhead) DrawQueue();
        };
        Timeline.HoverChanged += seconds =>
        {
            if (_selected == null || _playing) return;
            if (seconds < 0) { _hoverPaused = false; HoverBadge.IsVisible = false; ShowThumbnail(_head); RequestFrame(); return; }
            if (_hoverPaused && Math.Abs(seconds - _head) * Timeline.PixelsPerSecond < 6) return;
            _hoverPaused = false;
            HoverBadge.IsVisible = true;
            HoverLabel.Text = Clock(seconds);
            _frameDelay.Stop();
            ShowThumbnail(seconds);
        };
        Timeline.ZoomRequested += (factor, anchor, pointerX) => ZoomTo(ZoomSlider.Value * factor, anchor, pointerX - StripScroller.Offset.X);
        Timeline.ScrollRequested += pixels =>
            StripScroller.Offset = new Vector(Math.Clamp(StripScroller.Offset.X + pixels, 0, Math.Max(0, Timeline.Width - StripScroller.Bounds.Width)), 0);
    }

    private double StripWidth()
    {
        var view = StripScroller.Bounds.Width > 0 ? StripScroller.Bounds.Width : 1000;
        return Math.Max(view, view * ZoomSlider.Value);
    }

    private double XOf(double seconds) => _selected == null ? 0 : Timeline.XOf(seconds);

    private void LayoutStrip()
    {
        Timeline.Width = StripWidth();
        PlaceMarkers();
    }

    private void Strip_SizeChanged(object? sender, SizeChangedEventArgs e) => LayoutStrip();

    private void PlaceMarkers()
    {
        if (_selected == null) return;
        Timeline.Head = _head;
        PlayheadLabel.Text = Clock(_head);
        TrimHint.Text = _selected.Segments.Count == 1 ? UiText.F(UiText.HintKeeping, Clock(_selected.Kept)) : UiText.F(UiText.HintKeepingParts, Clock(_selected.Kept), _selected.Segments.Count);
    }

    /// <summary>The part edits apply to: the selected one, else the one under or nearest to the playhead.</summary>
    private int CurrentSegment()
    {
        if (_selected == null || _selected.Segments.Count == 0) return -1;
        if (Timeline.Selected >= 0) return Timeline.Selected;
        var at = Timeline.SegmentAt(_head);
        return at >= 0 ? at : Timeline.NearestSegment(_head);
    }

    private void UpdateCutFields()
    {
        if (_selected == null) return;
        var index = CurrentSegment();
        if (index >= 0)
        {
            var segment = _selected.Segments[index];
            SegmentLabel.Text = _selected.Segments.Count > 1 ? UiText.F(UiText.SegPartStart, index + 1, _selected.Segments.Count) : UiText.SegStart;
            InBox.Text = Clock(segment.In);
            OutBox.Text = Clock(segment.Out);
        }
        KeptLabel.Text = UiText.F(UiText.KeptOf, Clock(_selected.Kept), Clock(_selected.Duration));
        PartsLabel.Text = _selected.Segments.Count == 1 ? "" : UiText.F(UiText.PartsCount, _selected.Segments.Count);
        CutSummary.Text = _selected.Segments.Count == 1
            ? $"[ {Clock(_selected.In)}   ] {Clock(_selected.Out)}"
            : string.Join("   ", _selected.Segments.Select((part, i) => $"{i + 1}: {Clock(part.In)}–{Clock(part.Out)}"));
        PlaceMarkers();
    }

    // ---- edits with undo

    private void PushUndo()
    {
        if (_selected == null) return;
        _selected.Undo.Push((_selected.Segments.ToList(), _selected.Bookmarks.ToList()));
        if (_selected.Undo.Count > 100) _selected.Undo.TryPop(out _);
        _selected.Redo.Clear();
        UpdateUndoButtons();
    }

    private void UpdateUndoButtons()
    {
        UndoButton.IsEnabled = _selected?.Undo.Count > 0;
        RedoButton.IsEnabled = _selected?.Redo.Count > 0;
    }

    /// <summary>Apply an edit to the selected clip with an undo step, then refresh everything.</summary>
    private void Edit(Action<FootageItem> change)
    {
        if (_selected == null) return;
        StopPlay();
        PushUndo();
        change(_selected);
        _selected.Segments = _selected.Segments.OrderBy(part => part.In).ToList();
        Timeline.SetSegments(_selected.Segments);
        Timeline.SetBookmarks(_selected.Bookmarks);
        UpdateCutFields();
        DrawQueue();
    }

    private void Undo_Click(object? sender, RoutedEventArgs e) => UndoEdit();
    private void Redo_Click(object? sender, RoutedEventArgs e) => RedoEdit();

    private void UndoEdit()
    {
        if (_selected == null || !_selected.Undo.TryPop(out var state)) return;
        _selected.Redo.Push((_selected.Segments.ToList(), _selected.Bookmarks.ToList()));
        _selected.Segments = state.Segments; _selected.Bookmarks = state.Bookmarks;
        Timeline.SetSegments(_selected.Segments); Timeline.SetBookmarks(_selected.Bookmarks);
        UpdateCutFields(); DrawQueue(); UpdateUndoButtons();
    }

    private void RedoEdit()
    {
        if (_selected == null || !_selected.Redo.TryPop(out var state)) return;
        _selected.Undo.Push((_selected.Segments.ToList(), _selected.Bookmarks.ToList()));
        _selected.Segments = state.Segments; _selected.Bookmarks = state.Bookmarks;
        Timeline.SetSegments(_selected.Segments); Timeline.SetBookmarks(_selected.Bookmarks);
        UpdateCutFields(); DrawQueue(); UpdateUndoButtons();
    }

    private void Split_Click(object? sender, RoutedEventArgs e) => SplitAtHead();

    private void SplitAtHead()
    {
        if (_selected == null) return;
        var index = Timeline.SegmentAt(_head);
        if (index < 0) { StripStatus.Text = UiText.SplitOutside; return; }
        var part = _selected.Segments[index];
        if (_head - part.In < 0.1 || part.Out - _head < 0.1) { StripStatus.Text = UiText.SplitTooClose; return; }
        Edit(clip =>
        {
            clip.Segments.RemoveAt(index);
            clip.Segments.Insert(index, (_head, part.Out));
            clip.Segments.Insert(index, (part.In, _head));
        });
        Timeline.Selected = index + 1;
        UpdateCutFields();
        StripStatus.Text = UiText.F(UiText.SplitDone, _selected.Segments.Count);
    }

    private void DeleteSegment_Click(object? sender, RoutedEventArgs e) => DeleteSegmentAtHead();

    private void DeleteSegmentAtHead()
    {
        if (_selected == null) return;
        var index = Timeline.Selected >= 0 ? Timeline.Selected : Timeline.SegmentAt(_head);
        if (index < 0) { StripStatus.Text = UiText.RemoveNoPart; return; }
        if (_selected.Segments.Count == 1) { StripStatus.Text = UiText.RemoveLastPart; return; }
        Edit(clip => clip.Segments.RemoveAt(index));
        Timeline.Selected = -1;
        MoveHead(Timeline.SourceOf(Timeline.OutputOf(_head)));
        UpdateCutFields();
        StripStatus.Text = UiText.F(UiText.PartRemoved, Clock(_selected.Kept), _selected.Segments.Count);
    }

    private void TrimBefore_Click(object? sender, RoutedEventArgs e) => MarkIn();
    private void TrimAfter_Click(object? sender, RoutedEventArgs e) => MarkOut();
    private void MarkIn_Click(object? sender, RoutedEventArgs e) => MarkIn();
    private void MarkOut_Click(object? sender, RoutedEventArgs e) => MarkOut();

    /// <summary>Start of the part under (or nearest to) the playhead moves to the playhead.</summary>
    private void MarkIn()
    {
        var index = CurrentSegment();
        if (index < 0 || _selected == null) return;
        var part = _selected.Segments[index];
        var floor = index > 0 ? _selected.Segments[index - 1].Out + 0.1 : 0;
        var start = Math.Clamp(_head, floor, part.Out - 0.1);
        Edit(clip => clip.Segments[index] = (start, part.Out));
    }

    private void MarkOut()
    {
        var index = CurrentSegment();
        if (index < 0 || _selected == null) return;
        var part = _selected.Segments[index];
        var ceiling = index < _selected.Segments.Count - 1 ? _selected.Segments[index + 1].In - 0.1 : _selected.Duration;
        var end = Math.Clamp(_head, part.In + 0.1, ceiling);
        Edit(clip => clip.Segments[index] = (part.In, end));
    }

    private void ResetCut_Click(object? sender, RoutedEventArgs e)
    {
        if (_selected == null) return;
        Edit(clip => clip.Segments = [(0, clip.Duration)]);
    }

    private void Bookmark_Click(object? sender, RoutedEventArgs e) => ToggleBookmark();

    private void ToggleBookmark()
    {
        if (_selected == null) return;
        var existing = _selected.Bookmarks.FindIndex(b => Math.Abs(b - _head) < 0.15);
        Edit(clip =>
        {
            if (existing >= 0) clip.Bookmarks.RemoveAt(existing);
            else { clip.Bookmarks.Add(_head); clip.Bookmarks.Sort(); }
        });
    }

    private IEnumerable<double> Edges()
    {
        if (_selected == null) yield break;
        foreach (var part in _selected.Segments) { yield return part.In; yield return part.Out; }
        foreach (var bookmark in _selected.Bookmarks) yield return bookmark;
    }

    private void PrevEdge_Click(object? sender, RoutedEventArgs e) => JumpEdge(-1);
    private void NextEdge_Click(object? sender, RoutedEventArgs e) => JumpEdge(1);

    private void JumpEdge(int direction)
    {
        var edges = direction < 0 ? Edges().Where(t => t < _head - 0.02).DefaultIfEmpty(0).Max()
                                  : Edges().Where(t => t > _head + 0.02).DefaultIfEmpty(_selected?.Duration ?? 0).Min();
        MoveHead(edges);
    }

    private void Snap_Changed(object? sender, RoutedEventArgs e)
    {
        if (Timeline == null || SnapToggle == null) return;
        Timeline.Snap = SnapToggle.IsChecked == true;
        RememberSettings();
    }

    // ---- playhead and preview

    private void MoveHead(double seconds, bool fetch = true)
    {
        if (_selected == null) return;
        _head = Math.Clamp(seconds, 0, _selected.Duration);
        PlaceMarkers();
        UpdateCutFields();
        KeepHeadVisible();
        ShowThumbnail(_head);
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

    private void ShowThumbnail(double seconds)
    {
        if (_selected == null || _selected.Strip.Count == 0) return;
        var nearest = _selected.Strip.MinBy(tile => Math.Abs(tile.Seconds - seconds));
        TrimPreview.Source = Cropped(nearest.Picture, _selected.Box, _selected.Width);
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
            await EngineAsync(new { action = "frame", source = clip.Path, seconds = moment, target,
                crop = clip.Box is { } box ? new[] { box.X, box.Y, box.Width, box.Height } : null });
            if (request == _frameRequest && clip == _selected && !HoverBadge.IsVisible) TrimPreview.Source = LoadBitmap(target);
        }
        catch { /* A damaged frame keeps the thumbnail in view. */ }
        finally { DeleteTemp(target); if (request == _frameRequest) TrimLoading.IsVisible = false; }
    }

    // ---- zoom

    private void Zoom_Changed(object? sender, Avalonia.Controls.Primitives.RangeBaseValueChangedEventArgs e)
    {
        if (Timeline == null) return;
        LayoutStrip();
        KeepHeadVisible();
    }

    /// <summary>Zoom so that the given moment stays under the pointer (or centred when none).</summary>
    private void ZoomTo(double zoom, double anchor, double viewportX)
    {
        if (_selected == null) return;
        zoom = Math.Clamp(zoom, ZoomSlider.Minimum, ZoomSlider.Maximum);
        ZoomSlider.Value = zoom;
        Timeline.Width = StripWidth();
        var offset = XOf(anchor) - viewportX;
        StripScroller.Offset = new Vector(Math.Clamp(offset, 0, Math.Max(0, Timeline.Width - StripScroller.Bounds.Width)), 0);
    }

    private void ZoomIn_Click(object? sender, RoutedEventArgs e) => ZoomTo(ZoomSlider.Value * 1.5, _head, StripScroller.Bounds.Width / 2);
    private void ZoomOut_Click(object? sender, RoutedEventArgs e) => ZoomTo(ZoomSlider.Value / 1.5, _head, StripScroller.Bounds.Width / 2);
    private void ZoomFit_Click(object? sender, RoutedEventArgs e) => ZoomTo(1, 0, 0);

    // ---- play

    private void Play_Click(object? sender, RoutedEventArgs e) => TogglePlay();

    private void TogglePlay()
    {
        if (_selected == null) return;
        if (_playing) { StopPlay(); return; }
        var from = _head;
        if (from >= _selected.Out - 0.05) from = _selected.In;
        if (Timeline.SegmentAt(from) < 0)
        {
            // Start at the next kept part instead of playing a removed gap.
            var next = _selected.Segments.FirstOrDefault(part => part.In > from);
            from = next == default ? _selected.In : next.In;
        }
        StartPlayback(from);
    }

    private void StartPlayback(double from)
    {
        if (_selected == null) return;
        HoverBadge.IsVisible = false;
        _frameDelay.Stop();
        _playing = true;
        PlayButton.Content = UiText.Pause;
        try { _player.Play(_selected.Path, from, _selected.Fps, _selected.Width, _selected.Height, _selected.Box); }
        catch (Exception ex) { _playing = false; PlayButton.Content = UiText.Play; StripStatus.Text = UiText.F(UiText.PlaybackFailed, Loc.Engine(ex.Message)); }
    }

    private void StopPlay()
    {
        if (!_playing) return;
        _playing = false;
        _player.Stop();
        PlayButton.Content = UiText.Play;
        RequestFrame();
    }

    private void OnPlaybackFrame(WriteableBitmap bitmap, double moment)
    {
        if (!_playing || _selected == null) return;
        var index = Timeline.SegmentAt(moment);
        if (index < 0 || moment >= _selected.Segments[index].Out - 1e-6)
        {
            // Reached the end of a kept part: continue with the next one or stop.
            var nextIndex = _selected.Segments.FindIndex(part => part.In > moment + 1e-6);
            if (nextIndex >= 0) { StartPlayback(_selected.Segments[nextIndex].In); _head = _selected.Segments[nextIndex].In; }
            else { _head = Math.Min(moment, _selected.Out); StopPlay(); }
            PlaceMarkers(); KeepHeadVisible();
            return;
        }
        _head = moment;
        if (!ReferenceEquals(TrimPreview.Source, bitmap)) TrimPreview.Source = bitmap;
        TrimPreview.InvalidateVisual();
        PlaceMarkers();
        KeepHeadVisible();
    }

    private void ContinueOrStop()
    {
        if (_selected == null) { StopPlay(); return; }
        var nextIndex = _selected.Segments.FindIndex(part => part.In > _head + 0.05);
        if (nextIndex >= 0) StartPlayback(_selected.Segments[nextIndex].In);
        else StopPlay();
    }

    private void GoStart_Click(object? sender, RoutedEventArgs e) { if (_selected != null) MoveHead(_selected.In); }
    private void GoEnd_Click(object? sender, RoutedEventArgs e) { if (_selected != null) MoveHead(_selected.Out); }
    private void Back_Click(object? sender, RoutedEventArgs e) => MoveHead(_head - 1);
    private void Forward_Click(object? sender, RoutedEventArgs e) => MoveHead(_head + 1);

    private void CutBox_KeyDown(object? sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter) { CutBox_Changed(sender, e); e.Handled = true; }
    }

    private void CutBox_Changed(object? sender, RoutedEventArgs e)
    {
        if (_selected == null) return;
        var index = CurrentSegment();
        if (index >= 0 && TryParseClock(InBox.Text, out var start) && TryParseClock(OutBox.Text, out var end) && end > start)
        {
            var floor = index > 0 ? _selected.Segments[index - 1].Out + 0.1 : 0;
            var ceiling = index < _selected.Segments.Count - 1 ? _selected.Segments[index + 1].In - 0.1 : _selected.Duration;
            end = Math.Clamp(end, floor + 0.1, ceiling);
            start = Math.Clamp(start, floor, end - 0.1);
            Edit(clip => clip.Segments[index] = (start, end));
        }
        else UpdateCutFields();
    }

    private void Window_SizeChanged(object? sender, SizeChangedEventArgs e)
    {
        if (PageContent == null || PreviewHost == null) return;
        // Wide windows get a wider page; tall windows a taller preview, so the editor fills the screen.
        PageContent.MaxWidth = Math.Max(1180, e.NewSize.Width - 232 - 56 - 20);
        FitTrimPage();
        if (_page == 1) LayoutStrip();
    }

    /// <summary>The trim workspace fills the page exactly; nothing scrolls, the preview takes the rest.</summary>
    private void FitTrimPage()
    {
        if (TrimPage == null || PageScroller == null) return;
        var dark = _page == 1;
        PageScroller.Background = dark ? new SolidColorBrush(Color.Parse("#1B2025")) : Brushes.Transparent;
        PageContent.Margin = dark ? new Thickness(0) : _page is 2 or 3 ? new Thickness(20, 14) : new Thickness(28, 22);
        PageContent.MaxWidth = dark ? double.PositiveInfinity : Math.Max(1180, Bounds.Width - 232 - 56 - 20);
        if (dark) TrimPage.Height = Math.Max(420, PageScroller.Bounds.Height);
    }

    private void ToggleFullScreen() =>
        WindowState = WindowState == WindowState.FullScreen ? WindowState.Normal : WindowState.FullScreen;

    private void Window_KeyDown(object? sender, KeyEventArgs e)
    {
        if (e.Key == Key.F11) { ToggleFullScreen(); e.Handled = true; return; }
        if (e.Key == Key.Escape && ShareOverlay.IsVisible && !BusyOverlay.IsVisible) { CloseShare(); e.Handled = true; return; }
        if (MaskOverlay.IsVisible) { if (!BusyOverlay.IsVisible && MaskKey(e)) e.Handled = true; return; }
        if (e.Key == Key.Escape && WindowState == WindowState.FullScreen) { WindowState = WindowState.Normal; e.Handled = true; return; }
        if (_page != 1 || _selected == null || FocusManager?.GetFocusedElement() is TextBox) return;
        var step = e.KeyModifiers.HasFlag(KeyModifiers.Shift) ? 5 : 1;
        var control = e.KeyModifiers.HasFlag(KeyModifiers.Control) || e.KeyModifiers.HasFlag(KeyModifiers.Meta);
        switch (e.Key)
        {
            case Key.Space: TogglePlay(); break;
            case Key.Left: StopPlay(); MoveHead(_head - step); break;
            case Key.Right: StopPlay(); MoveHead(_head + step); break;
            case Key.I when !control: MarkIn(); break;
            case Key.O when !control: MarkOut(); break;
            case Key.S when !control: SplitAtHead(); break;
            case Key.B when !control: ToggleBookmark(); break;
            case Key.Delete or Key.Back: DeleteSegmentAtHead(); break;
            case Key.Z when control && e.KeyModifiers.HasFlag(KeyModifiers.Shift): RedoEdit(); break;
            case Key.Z when control: UndoEdit(); break;
            case Key.Y when control: RedoEdit(); break;
            case Key.OemPlus or Key.Add: ZoomIn_Click(null, e); break;
            case Key.OemMinus or Key.Subtract: ZoomOut_Click(null, e); break;
            case Key.Home: MoveHead(_selected.In); break;
            case Key.End: MoveHead(_selected.Out); break;
            case Key.PageUp: JumpEdge(-1); break;
            case Key.PageDown: JumpEdge(1); break;
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
        SignalNote.Text = IsAnalog ? UiText.SignalNoteAnalog : UiText.SignalNoteHdzero;
        RefreshModelChoices();
        UpdateCleanHint();
    }

    private void Setting_Changed(object? sender, RoutedEventArgs e)
    {
        if (_applyingPreset) return;
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
        var parts = new List<string> { IsAnalog ? UiText.SignalAnalog : UiText.SignalHdzero };
        if (NoiseSlider.Value > 0) parts.Add(UiText.HintDenoised);
        if (RemoveOsd.IsChecked == true) parts.Add(UiText.OsdRemovedSummary);
        if (SizePercent != 100) parts.Add((SizePercent / 100.0).ToString("0.#", CultureInfo.InvariantCulture) + "×");
        if (Effort != "balanced") parts.Add(Effort == "fast" ? UiText.SpeedFast : UiText.SpeedBest);
        if (LookOn) parts.Add(UiText.LookLabel);
        CleanHint.Text = string.Join(" · ", parts);
        if (!_applyingPreset) RepairChanged();
    }

    private string Effort => EffortFast?.IsChecked == true ? "fast" : EffortBest?.IsChecked == true ? "best" : "balanced";

    private void Effort_Changed(object? sender, RoutedEventArgs e)
    {
        if (EffortNote == null) return;
        EffortNote.Text = Effort switch
        {
            "fast" => UiText.SpeedNoteFast,
            "best" => UiText.SpeedNoteBest,
            _ => UiText.SpeedNoteBalanced,
        };
        UpdateCleanHint();
    }

    private void Mode_Changed(object? sender, SelectionChangedEventArgs e)
    {
        if (ModelPanel != null) ModelPanel.IsVisible = ModeChoice.SelectedIndex == 1;
    }

    // ---------------------------------------------------------------- look

    /// <summary>Look presets: strengths of grade, glow, vignette, grain and lens fringe, 0..100.</summary>
    private static readonly (string Id, string Title, (int Grade, int Glow, int Vignette, int Grain, int Fringe) Values)[] Looks =
    [
        ("clean", UiText.LookClean, (30, 20, 25, 10, 0)),
        ("cinematic", UiText.LookCinematic, (60, 45, 50, 25, 35)),
        ("punchy", UiText.LookPunchy, (100, 70, 60, 15, 50)),
        ("retro-fpv", UiText.LookRetro, (50, 80, 85, 90, 100)),
    ];

    private bool _settingLook;

    private bool LookOn => LookToggle?.IsChecked == true;

    private static int LookValue(Slider slider) => (int)Math.Round(slider.Value);

    private (int Grade, int Glow, int Vignette, int Grain, int Fringe) LookValues =>
        (LookValue(LookGradeSlider), LookValue(LookGlowSlider), LookValue(LookVignetteSlider), LookValue(LookGrainSlider), LookValue(LookFringeSlider));

    private void SetLook(bool on, (int Grade, int Glow, int Vignette, int Grain, int Fringe) values)
    {
        _settingLook = true;
        try
        {
            LookToggle.IsChecked = on;
            LookGradeSlider.Value = Math.Clamp(values.Grade, 0, 100);
            LookGlowSlider.Value = Math.Clamp(values.Glow, 0, 100);
            LookVignetteSlider.Value = Math.Clamp(values.Vignette, 0, 100);
            LookGrainSlider.Value = Math.Clamp(values.Grain, 0, 100);
            LookFringeSlider.Value = Math.Clamp(values.Fringe, 0, 100);
        }
        finally { _settingLook = false; }
        ShowLook();
    }

    private void LookPreset_Click(object? sender, RoutedEventArgs e)
    {
        if (sender is not RadioButton { Tag: string id }) return;
        var look = Looks.FirstOrDefault(item => item.Id == id);
        // Off keeps the slider values, so switching back on returns to them.
        if (look.Id == null) SetLook(false, LookValues);
        else SetLook(true, look.Values);
    }

    private void LookToggle_Changed(object? sender, RoutedEventArgs e)
    {
        if (_settingLook || LookFringeSlider == null) return;
        // Switched on with every slider at 0: start from the Clean preset.
        if (LookOn && LookValues == (0, 0, 0, 0, 0)) SetLook(true, Looks[0].Values);
        else ShowLook();
    }

    private void LookSlider_Changed(object? sender, RoutedEventArgs e)
    {
        if (_settingLook || LookFringeSlider == null) return;
        ShowLook();
    }

    /// <summary>Values, enabled state, and which preset chip matches; any other values are Custom.</summary>
    private void ShowLook()
    {
        if (LookFringeSlider == null || LookPresets == null) return;
        var values = LookValues;
        LookGradeValue.Text = $"{values.Grade}";
        LookGlowValue.Text = $"{values.Glow}";
        LookVignetteValue.Text = $"{values.Vignette}";
        LookGrainValue.Text = $"{values.Grain}";
        LookFringeValue.Text = $"{values.Fringe}";
        LookSliders.IsEnabled = LookOn;
        var match = Looks.FirstOrDefault(item => item.Values == values);
        var chosen = !LookOn ? "off" : match.Id;
        foreach (var chip in LookPresets.Children.OfType<RadioButton>())
            chip.IsChecked = chosen != null && chip.Tag as string == chosen;
        LookCustom.IsVisible = LookOn && match.Id == null;
        ToolTip.SetTip(LookToggle, !LookOn ? UiText.LookOff
            : match.Id != null ? string.Format(CultureInfo.CurrentCulture, UiText.LookPresetOn, match.Title)
            : UiText.LookCustomOn);
        UpdateCleanHint();
    }

    private void LookAdjust_Click(object? sender, RoutedEventArgs e)
    {
        LookSliders.IsVisible = !LookSliders.IsVisible;
        LookAdjustButton.Content = LookSliders.IsVisible ? UiText.Hide : UiText.Adjust;
        ToolTip.SetTip(LookAdjustButton, LookSliders.IsVisible ? UiText.LookFoldTip : UiText.LookAdjustTip);
    }

    // The last rendered frame: original and, with a look, the repaired frame without it.
    private IImage? _compareOriginal, _comparePlain;

    private void LookCompare_Changed(object? sender, RoutedEventArgs e) => ShowCompareLeft();

    private void ShowCompareLeft()
    {
        if (CompareLeftLabel == null || LookCompare == null) return;
        var plain = LookCompare.IsChecked == true && _comparePlain != null;
        if (_compareOriginal != null) CompareBefore.Source = plain ? _comparePlain : _compareOriginal;
        CompareLeftLabel.Text = plain ? UiText.LabelWithoutLook : UiText.LabelOriginal;
        CompareRightLabel.Text = UiText.LabelCleaned;
    }

    // ---------------------------------------------------------------- presets

    private RepairPreset CurrentRepair() => new()
    {
        Signal = IsAnalog ? "analog" : "digital",
        Colour = (int)ColorSlider.Value, Noise = (int)NoiseSlider.Value, Block = (int)BlockSlider.Value,
        Detail = (int)DetailSlider.Value, Motion = (int)MotionSlider.Value, Size = SizePercent,
        Osd = RemoveOsd.IsChecked == true, Effort = Effort,
        Quality = (int)(QualityValue.Value ?? 18), SizeCapMb = (int)(SizeCap.Value ?? 0),
        Look = LookOn, LookGrade = LookValue(LookGradeSlider), LookGlow = LookValue(LookGlowSlider),
        LookVignette = LookValue(LookVignetteSlider), LookGrain = LookValue(LookGrainSlider), LookFringe = LookValue(LookFringeSlider),
    };

    private void ApplyRepair(RepairPreset preset)
    {
        _applyingPreset = true;
        try
        {
            if (preset.Signal == "digital") DigitalChoice.IsChecked = true; else AnalogChoice.IsChecked = true;
            ColorSlider.Value = Math.Clamp(preset.Colour, 0, 100);
            NoiseSlider.Value = Math.Clamp(preset.Noise, 0, 100);
            BlockSlider.Value = Math.Clamp(preset.Block, 0, 100);
            DetailSlider.Value = Math.Clamp(preset.Detail, 0, 100);
            MotionSlider.Value = Math.Clamp(preset.Motion, 0, 100);
            SizeOriginal.IsChecked = preset.Size == 100; Size150.IsChecked = preset.Size == 150; Size200.IsChecked = preset.Size == 200;
            RemoveOsd.IsChecked = preset.Osd; OsdKeep.IsChecked = !preset.Osd;
            EffortFast.IsChecked = preset.Effort == "fast"; EffortBest.IsChecked = preset.Effort == "best";
            EffortBalanced.IsChecked = preset.Effort is not ("fast" or "best");
            QualityValue.Value = Math.Clamp(preset.Quality, 14, 32);
            SizeCap.Value = Math.Max(0, preset.SizeCapMb);
            SetLook(preset.Look, (preset.LookGrade, preset.LookGlow, preset.LookVignette, preset.LookGrain, preset.LookFringe));
        }
        finally { _applyingPreset = false; }
        Setting_Changed(null, new RoutedEventArgs());
    }

    private object BuildRecipe(FootageItem clip) => RecipeOf(CurrentRepair(), clip);

    /// <summary>The engine recipe for these settings and this clip, with the export choices of the Export page.</summary>
    private object RecipeOf(RepairPreset settings, FootageItem clip) => new
    {
        signalart = settings.Signal == "digital" ? "digital" : "analog",
        farbberuhigung = Math.Clamp(settings.Colour, 0, 100),
        blockpflege = Math.Clamp(settings.Block, 0, 100),
        kantendetail = Math.Clamp(settings.Detail, 0, 100),
        vergroesserung_prozent = settings.Size is 150 or 200 ? settings.Size : 100,
        bewegungsspur = Math.Clamp(settings.Motion, 0, 100),
        schnittbeginn = clip.In,
        schnittende = clip.Out,
        untertiteltext = CaptionText.Text ?? "",
        kompressionswert = (int)(QualityValue.Value ?? 18),
        dateiziel_mb = FormatUsesQuality ? (int)(SizeCap.Value ?? 0) : 0,
        osd_entfernen = settings.Osd,
        rauschminderung = Math.Clamp(settings.Noise, 0, 100),
        aufwand = settings.Effort is "fast" or "best" ? settings.Effort : "balanced",
        dateiformat = OutputFormat.Id,
        look = settings.Look,
        look_farbstimmung = Math.Clamp(settings.LookGrade, 0, 100) / 100.0,
        look_leuchten = Math.Clamp(settings.LookGlow, 0, 100) / 100.0,
        look_farbsaum = Math.Clamp(settings.LookFringe, 0, 100) / 100.0,
        look_vignette = Math.Clamp(settings.LookVignette, 0, 100) / 100.0,
        look_koernung = Math.Clamp(settings.LookGrain, 0, 100) / 100.0,
        // Hand corrections of the OSD mask, made in the mask editor for this recording.
        osd_handmaske = clip.ManualMask is { } mask && File.Exists(mask) ? mask : "",
    };

    private string Mode => ModeChoice.SelectedIndex == 1 ? "model" : "manual";

    private void Compare_Changed(object? sender, Avalonia.Controls.Primitives.RangeBaseValueChangedEventArgs e) => UpdateCompare();

    private bool _compareDragging;
    private double _compareAspect = 16.0 / 9;
    private double _reviewAspect = 16.0 / 9;

    private void SetCompareFromPointer(double x)
    {
        var shown = ImageRect(CompareCanvas, _compareAspect);
        CompareSlider.Value = Math.Clamp((x - shown.X) / Math.Max(1, shown.Width) * 100, 0, 100);
    }
    private void Compare_PointerPressed(object? sender, PointerPressedEventArgs e)
    {
        if (!CompareDivider.IsVisible || !e.GetCurrentPoint(CompareCanvas).Properties.IsLeftButtonPressed) return;
        _compareDragging = true; e.Pointer.Capture(CompareCanvas);
        SetCompareFromPointer(e.GetPosition(CompareCanvas).X);
        e.Handled = true;
    }
    private void Compare_PointerMoved(object? sender, PointerEventArgs e)
    {
        if (_compareDragging) SetCompareFromPointer(e.GetPosition(CompareCanvas).X);
    }
    private void Compare_PointerReleased(object? sender, PointerReleasedEventArgs e) { _compareDragging = false; e.Pointer.Capture(null); }
    private void Compare_SizeChanged(object? sender, SizeChangedEventArgs e) => UpdateCompare();

    private void UpdateCompare()
    {
        if (CompareCanvas == null || CompareAfter == null || CompareDivider == null) return;
        // The split line lives inside the shown picture, not in the empty canvas around it.
        var shown = ImageRect(CompareCanvas, _compareAspect);
        var width = CompareCanvas.Bounds.Width;
        var edge = shown.X + shown.Width * CompareSlider.Value / 100;
        CompareAfter.Clip = new RectangleGeometry(new Rect(edge, 0, Math.Max(0, width - edge), CompareCanvas.Bounds.Height));
        CompareDivider.Margin = new Thickness(edge - 1, 0, 0, 0);
        CompareGrip.Margin = new Thickness(edge - 13, 0, 0, 0);
        CompareGrip.IsVisible = CompareDivider.IsVisible;
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
        if (!IsCompatibleModel(path, IsAnalog ? "analog" : "digital")) { PreviewStatus.Text = UiText.ModelWrongSignal; return; }
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
    private async void TrainFolder_Click(object? sender, RoutedEventArgs e)
    {
        if (await PickFolderAsync(TrainFolder.Text) is { } folder) { TrainFolder.Text = folder; RememberSettings(); }
    }

    private async void StartTrain_Click(object? sender, RoutedEventArgs e)
    {
        if (_trainingCancel != null) return;
        if (string.IsNullOrWhiteSpace(TrainFolder.Text) || !Directory.Exists(TrainFolder.Text)) { TrainStatus.Text = UiText.TrainNeedFolder; return; }
        var signal = TrainSignal.SelectedIndex == 0 ? "analog" : "digital";
        var continuing = TrainApproach.SelectedIndex == 1;
        var initialModel = continuing ? (ResumeChoice.SelectedItem as ComboBoxItem)?.Tag as string : null;
        if (continuing && (initialModel == null || !IsCompatibleModel(initialModel, signal)))
        { TrainStatus.Text = UiText.TrainNeedModel; return; }
        var destination = System.IO.Path.Combine(ModelFolder(), $"{signal}-{DateTime.Now:yyyyMMdd-HHmmss}.json");
        TrainStatus.Text = UiText.TrainSampling; TrainProgress.Value = 0;
        _trainingCancel = new CancellationTokenSource(); CancelTrainButton.IsEnabled = true;
        ShowBusy(UiText.TrainBusyTitle, TrainStatus.Text, () => _trainingCancel?.Cancel());
        UpdateBusy(0);
        try
        {
            var seconds = (int)(TrainMinutes.Value ?? 5) * (TrainUnit.SelectedIndex == 1 ? 3600 : 60);
            var started = DateTime.UtcNow;
            var outcome = await EngineAsync(new { action = "train", folder = TrainFolder.Text, signal, seconds,
                initial_model = initialModel, target = destination },
                item => Dispatcher.UIThread.Post(() =>
                {
                    TrainProgress.Value = Math.Min(99, (DateTime.UtcNow - started).TotalSeconds / seconds * 100);
                    TrainStatus.Text = UiText.F(UiText.TrainLearning, item.GetProperty("samples").GetInt32());
                    UpdateBusy(TrainProgress.Value / 100, TrainStatus.Text, Duration(DateTime.UtcNow - started));
                }), _trainingCancel.Token);
            TrainProgress.Value = 100;
            TrainStatus.Text = UiText.F(UiText.TrainSaved, outcome.GetProperty("training_runs").GetInt32(), outcome.GetProperty("samples").GetInt32(), destination);
            RefreshModelChoices(); RefreshResumeChoices();
        }
        catch (OperationCanceledException) { TrainStatus.Text = UiText.TrainStopped; }
        catch (Exception ex) { TrainStatus.Text = ex.Message; }
        finally { _trainingCancel.Dispose(); _trainingCancel = null; CancelTrainButton.IsEnabled = false; HideBusy(); }
    }
    private void CancelTrain_Click(object? sender, RoutedEventArgs e) => _trainingCancel?.Cancel();

    // ---------------------------------------------------------------- export

    // The folder the user chose; null means the default "Cleaned" folder next to the first clip.
    private string? _outputFolder;

    /// <summary>A "Cleaned" folder next to the first clip in the queue, made when the export starts.</summary>
    private string? DefaultOutputFolder()
    {
        var first = _queue.FirstOrDefault()?.Path;
        var folder = first == null ? null : System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(first));
        return folder == null ? null : System.IO.Path.Combine(folder, "Cleaned");
    }

    /// <summary>Where the files go: the chosen folder, else the default next to the first clip.</summary>
    private string? OutputFolderPath => !string.IsNullOrWhiteSpace(_outputFolder) ? _outputFolder : DefaultOutputFolder();

    private void ShowOutputFolder()
    {
        if (OutputFolderChip == null) return;
        var folder = OutputFolderPath;
        OutputFolderChip.Text = folder ?? UiText.FolderNone;
        ToolTip.SetTip(OutputFolderChip, folder);
        OutputFolderNote.Text = folder != null && !Directory.Exists(folder) ? UiText.FolderCreatedOnExport : "";
    }

    private async void OutputFolder_Click(object? sender, RoutedEventArgs e)
    {
        var start = OutputFolderPath;
        if (start != null && !Directory.Exists(start)) start = System.IO.Path.GetDirectoryName(start);
        if (await PickFolderAsync(start) is { } folder) { _outputFolder = folder; ShowOutputFolder(); RememberSettings(); }
    }

    private void OpenOutput_Click(object? sender, RoutedEventArgs e)
    {
        var folder = OutputFolderPath;
        if (!Directory.Exists(folder)) return;
        try { OpenInFileManager(folder); }
        catch (Exception ex) { ExportStatus.Text = UiText.F(UiText.OpenFolderFailed, ex.Message); }
    }

    private void UpdateExportSummary()
    {
        var total = _queue.Sum(clip => clip.Kept);
        if (_exportCancel == null) ExportStatus.Text = _queue.Count == 0 ? UiText.ExportNeedClips : UiText.ExportReady;
        ExportButton.Content = _queue.Count > 1 ? string.Format(CultureInfo.CurrentCulture, UiText.ExportMany, _queue.Count) : UiText.ExportOne;
        ExportSummary.Text = _queue.Count == 0 ? UiText.ExportSummaryEmpty
            : string.Format(CultureInfo.CurrentCulture, UiText.ExportSummary, _queue.Count, Clock(total), CleanHint.Text);
        ExportFoldedText.Text = string.Format(CultureInfo.CurrentCulture, UiText.ExportFoldedSummary, OutputFolderPath, OutputFormat.Title);
    }

    private async void Export_Clicked(object? sender, RoutedEventArgs e) => await ExportAsync(_queue.ToList(), SuffixBox.Text?.Trim() ?? "");

    /// <summary>One click: settings from the measurement, then the original is exported again as a new file.</summary>
    private async void AutoRefine_Click(object? sender, RoutedEventArgs e)
    {
        if (_exportCancel != null) return;
        if (_reviewClip == null || !_queue.Contains(_reviewClip)) { ReviewNote.Text = UiText.ReviewClipGone; return; }
        if (_assessedHeadroom < 0) { ReviewNote.Text = UiText.ReviewStillMeasuring; return; }
        StopReviewPlayback();
        var (settings, explanation) = SuggestSettings(secondPass: false);
        ApplyRepair(settings);
        RememberSettings();
        ExportStatus.Text = UiText.F(UiText.AutoRefineStatus, explanation);
        ReviewNote.Text = UiText.AutoRefineRunning;
        await ExportAsync([_reviewClip], (SuffixBox.Text?.Trim() ?? "") + "-refined");
    }

    /// <summary>Progress shows right under the Export button; the settings are locked while it runs.</summary>
    private void ShowExportRunning(bool running)
    {
        ExportRunning.IsVisible = running;
        ExportButton.IsEnabled = !running;
        ExportDetails.IsEnabled = !running;
        ExportFoldButton.IsEnabled = !running;
        CancelExportButton.IsEnabled = running;
        AutoRefineButton.IsEnabled = !running;
        ShareButton.IsEnabled = !running;
    }

    private async Task ExportAsync(List<FootageItem> clips, string suffix)
    {
        if (_exportCancel != null) return;
        if (clips.Count == 0) { ExportStatus.Text = UiText.ExportNeedClips; return; }
        var folder = OutputFolderPath;
        if (string.IsNullOrWhiteSpace(folder)) { ExportStatus.Text = UiText.ExportNeedFolder; return; }
        if (Mode == "model" && !File.Exists(ModelPath.Text)) { ExportStatus.Text = UiText.ExportNeedModel; return; }
        if (suffix.IndexOfAny(System.IO.Path.GetInvalidFileNameChars()) >= 0) { ExportStatus.Text = UiText.ExportBadSuffix; return; }
        try { Directory.CreateDirectory(folder); }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { ExportStatus.Text = UiText.F(UiText.CreateFolderFailed, ex.Message); return; }
        ShowOutputFolder();
        RememberSettings();
        var join = JoinClips.IsChecked == true && clips.Count > 1;
        var usedTargets = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var targets = new List<string>();
        // The file extension follows the chosen format; the engine checks that they match.
        var extension = OutputFormat.Extension;
        var joined = System.IO.Path.Combine(folder, System.IO.Path.GetFileNameWithoutExtension(clips[0].Path) + suffix + "-joined" + extension);
        foreach (var clip in clips)
        {
            var baseName = System.IO.Path.GetFileNameWithoutExtension(clip.Path);
            // Parts of a joined export are hidden files that disappear after joining.
            var prefix = join ? "." : "";
            var target = System.IO.Path.Combine(folder, $"{prefix}{baseName}{suffix}{extension}");
            for (var number = 2; !usedTargets.Add(target) || File.Exists(target); number++)
                target = System.IO.Path.Combine(folder, $"{prefix}{baseName}{suffix}-{number}{extension}");
            if (System.IO.Path.GetFullPath(target).Equals(System.IO.Path.GetFullPath(clip.Path), StringComparison.OrdinalIgnoreCase))
            { ExportStatus.Text = UiText.ExportSameFile; return; }
            targets.Add(target);
        }
        // The settings are fixed when the export starts: the pages stay usable while it runs.
        var recipes = clips.Select(BuildRecipe).ToList();
        var mode = Mode;
        var model = ModelPath.Text;
        var quality = (int)(QualityValue.Value ?? 18);
        var signal = IsAnalog ? "analog" : "digital";
        // Remaining time is weighted by cut length, so long clips count for more.
        var total = Math.Max(0.001, clips.Sum(clip => clip.Kept));
        _exportCancel = new CancellationTokenSource();
        _exportStarted = DateTime.UtcNow; _exportDone = 0;
        ExportProgress.Value = 0; ExportTime.Text = UiText.Estimating; ExportRunText.Text = UiText.ExportStarting;
        ShowExportRunning(true);
        _exportClock.Start();
        var finished = 0.0;
        try
        {
            for (var i = 0; i < clips.Count; i++)
            {
                var clip = clips[i];
                var target = targets[i];
                ExportStatus.Text = "";
                ExportRunText.Text = clips.Count == 1 ? UiText.F(UiText.RenderingOne, clip) : UiText.F(UiText.RenderingMany, i + 1, clips.Count, clip);
                ExportHint.Text = UiText.F(UiText.HintRendering, i + 1, clips.Count);
                var before = finished;
                var share = clip.Kept / total;
                try
                {
                    await EngineAsync(new { action = "export", source = clip.Path, target, recipe = recipes[i], mode, model,
                            segments = clip.Segments.Select(part => new[] { part.In, part.Out }).ToList() },
                        item =>
                        {
                            var done = before + share * item.GetProperty("fraction").GetDouble();
                            Dispatcher.UIThread.Post(() => { _exportDone = done; ExportProgress.Value = 100 * done; });
                        }, _exportCancel.Token);
                }
                catch (OperationCanceledException)
                {
                    RemovePartialOutput(target);
                    if (join) foreach (var part in targets) { try { File.Delete(part); } catch (IOException) { } }
                    ExportStatus.Text = UiText.F(UiText.ExportCanceled, i, clips.Count);
                    ExportHint.Text = UiText.ExportHintCanceled;
                    return;
                }
                finished = before + share;
                _exportDone = finished;
                StopReviewPlayback();
                _reviewSource = clip.Path; _reviewOutput = target; _reviewSegments = clip.Segments.ToList(); _reviewClip = clip;
                try
                {
                    var made = await EngineAsync(new { action = "inspect", source = target });
                    _reviewOutInfo = (made.GetProperty("width").GetInt32(), made.GetProperty("height").GetInt32(),
                        made.TryGetProperty("fps", out var fps) ? fps.GetDouble() : clip.Fps);
                }
                catch { _reviewOutInfo = (clip.Width, clip.Height, clip.Fps); }
                ReviewTitle.Text = UiText.F(UiText.ReviewClipTitle, clip);
                _reviewSeeking = true; ReviewSeek.Maximum = clip.Kept; ReviewSeek.Value = 0; _reviewSeeking = false;
                ReviewPanel.IsVisible = true;
                Dispatcher.UIThread.Post(() => { _reviewSeeking = true; ReviewSeek.Value = 0; _reviewSeeking = false; });
                await RefreshReviewAsync();
                _ = AssessAsync(clip, target, quality, signal);
            }
            if (join)
            {
                ExportRunText.Text = UiText.JoiningClips;
                try
                {
                    await EngineAsync(new { action = "merge", sources = targets, target = joined }, null, _exportCancel.Token);
                    foreach (var part in targets) { try { File.Delete(part); } catch (IOException) { } }
                    _reviewSource = null; _reviewOutput = null; ReviewPanel.IsVisible = false;
                }
                catch (Exception ex) when (ex is not OperationCanceledException)
                {
                    // The clips stay as separate files, made visible again.
                    foreach (var part in targets)
                    {
                        var visible = System.IO.Path.Combine(folder, System.IO.Path.GetFileName(part).TrimStart('.'));
                        try { File.Move(part, visible, overwrite: true); } catch (IOException) { }
                    }
                    ExportStatus.Text = UiText.F(UiText.JoinFailed, clips.Count, Loc.Engine(ex.Message));
                    ExportHint.Text = UiText.Done;
                    FinishExport(folder);
                    return;
                }
            }
            ExportProgress.Value = 100;
            ExportStatus.Text = join
                ? UiText.F(UiText.ExportedJoined, clips.Count, System.IO.Path.GetFileName(joined), Duration(DateTime.UtcNow - _exportStarted))
                : UiText.F(UiText.Exported, clips.Count, Duration(DateTime.UtcNow - _exportStarted));
            ExportHint.Text = UiText.Done;
            FinishExport(folder);
        }
        catch (Exception ex) { ExportStatus.Text = UiText.F(UiText.ExportFailed, Loc.Engine(ex.Message)); ExportHint.Text = UiText.ExportHintFailed; }
        finally
        {
            _exportClock.Stop(); ExportTime.Text = "";
            _exportCancel.Dispose(); _exportCancel = null;
            ShowExportRunning(false);
            ShowOutputFolder();
        }
    }

    /// <summary>After an export the settings fold into one line, so the result view gets the room.</summary>
    private void ExportFold_Click(object? sender, RoutedEventArgs e) => FoldExportSettings(ExportDetails.IsVisible);

    private void FoldExportSettings(bool fold)
    {
        UpdateExportSummary();
        ExportDetails.IsVisible = !fold;
        ExportActions.IsVisible = !fold;
        ExportFolded.IsVisible = ReviewPanel.IsVisible;
        ExportFoldButton.Content = fold ? UiText.ShowSettings : UiText.HideSettings;
        ExportFoldedText.IsVisible = fold;
    }

    /// <summary>Exports take minutes: bring the window back and open the folder if wanted.</summary>
    private void FinishExport(string folder)
    {
        if (ReviewPanel.IsVisible) FoldExportSettings(true);
        try { Activate(); } catch { /* Not every window manager allows it. */ }
        if (OpenAfterExport.IsChecked == true && Directory.Exists(folder))
        {
            try { OpenInFileManager(folder); } catch { /* The status line already says where the files are. */ }
        }
    }

    private void CancelExport_Click(object? sender, RoutedEventArgs e)
    {
        if (_exportCancel == null) return;
        CancelExportButton.IsEnabled = false;
        ExportRunText.Text = UiText.Canceling;
        _exportCancel.Cancel();
    }

    private void ShowExportTime()
    {
        var elapsed = DateTime.UtcNow - _exportStarted;
        // The first seconds go to OSD search and decoder start-up; wait for real
        // progress before projecting a finishing time.
        if (_exportDone < 0.005 || elapsed.TotalSeconds < 3)
        { ExportTime.Text = UiText.F(UiText.ElapsedEstimating, Duration(elapsed)); return; }
        var remaining = TimeSpan.FromSeconds(elapsed.TotalSeconds * (1 - _exportDone) / _exportDone);
        ExportTime.Text = UiText.F(UiText.ExportClock, 100 * _exportDone, Duration(remaining), Duration(elapsed));
    }

    private static void RemovePartialOutput(string target)
    {
        // The engine renders into ".<name>-<random>.<ext>" next to the target and
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
            var sourceBox = _reviewClip?.Box;
            var outputBox = ScaledBox(sourceBox, _reviewClip?.Width ?? 0, _reviewOutInfo.Width);
            await Task.WhenAll(
                EngineAsync(new { action = "frame", source = _reviewSource, seconds = SourceTimeOf(moment), target = before,
                    crop = sourceBox is { } sb ? new[] { sb.X, sb.Y, sb.Width, sb.Height } : null }),
                EngineAsync(new { action = "frame", source = _reviewOutput, seconds = moment, target = after,
                    crop = outputBox is { } ob ? new[] { ob.X, ob.Y, ob.Width, ob.Height } : null }));
            var original = LoadBitmap(before);
            var cleaned = LoadBitmap(after);
            _reviewAspect = (double)cleaned.PixelSize.Width / Math.Max(1, cleaned.PixelSize.Height);
            BeforeImage.Source = original; SideBefore.Source = original;
            AfterImage.Source = cleaned; SideAfter.Source = cleaned;
            ReviewTime.Text = Clock(moment);
            UpdateWipe();
        }
        catch (Exception ex) { ExportStatus.Text = UiText.F(UiText.ReviewFrameFailed, Loc.Engine(ex.Message)); }
        finally { DeleteTemp(before, after); _loadingReview = false; }
    }

    /// <summary>The source moment that ended up at this point of the exported file.</summary>
    private double SourceTimeOf(double outputSeconds)
    {
        var remaining = outputSeconds;
        foreach (var part in _reviewSegments)
        {
            var length = part.Out - part.In;
            if (remaining <= length) return part.In + remaining;
            remaining -= length;
        }
        return _reviewSegments.Count > 0 ? _reviewSegments[^1].Out : outputSeconds;
    }

    private async void ReviewSeek_Changed(object? sender, Avalonia.Controls.Primitives.RangeBaseValueChangedEventArgs e)
    {
        if (_reviewSeeking || _reviewSource == null) return;
        if (_reviewPlaying) { StartReviewPlayback(ReviewSeek.Value); return; }
        await RefreshReviewAsync();
    }

    // ---- live comparison: original and cleaned clip play in sync

    private void ReviewPlay_Click(object? sender, RoutedEventArgs e)
    {
        if (_reviewPlaying) StopReviewPlayback();
        else StartReviewPlayback(ReviewSeek.Value >= ReviewSeek.Maximum - 0.05 ? 0 : ReviewSeek.Value);
    }

    private void StartReviewPlayback(double outputSeconds)
    {
        if (_reviewSource == null || _reviewOutput == null || _reviewClip == null) return;
        _reviewPlaying = true;
        ReviewPlayButton.Content = UiText.Pause;
        try
        {
            var source = SourceTimeOf(outputSeconds);
            _reviewSegmentPlaying = SegmentIndexOf(source);
            _reviewPairOffset = outputSeconds - source;
            // One decoder for both clips keeps them frame-accurate; the original side
            // is restarted at the next kept part when the cleaned clip crosses a cut.
            var outputBox = ScaledBox(_reviewClip.Box, _reviewClip.Width, _reviewOutInfo.Width);
            _reviewPair.PlayPair(_reviewOutput, outputSeconds, _reviewSource, source, _reviewOutInfo.Fps,
                _reviewOutInfo.Width, _reviewOutInfo.Height, outputBox, _reviewClip.Box);
            if (outputBox is { } shown && shown.Height > 0) _reviewAspect = (double)shown.Width / shown.Height;
        }
        catch (Exception ex) { StopReviewPlayback(); ReviewNote.Text = UiText.F(UiText.PlaybackFailed, Loc.Engine(ex.Message)); }
    }

    private double _reviewPairOffset;

    private void StopReviewPlayback()
    {
        if (!_reviewPlaying) return;
        _reviewPlaying = false;
        _reviewPair.Stop();
        ReviewPlayButton.Content = UiText.PlayBoth;
    }

    private int SegmentIndexOf(double sourceSeconds)
    {
        for (var i = 0; i < _reviewSegments.Count; i++)
            if (sourceSeconds >= _reviewSegments[i].In - 1e-6 && sourceSeconds <= _reviewSegments[i].Out + 1e-6) return i;
        return -1;
    }

    private void OnReviewPair(WriteableBitmap cleaned, WriteableBitmap original, double moment)
    {
        if (!_reviewPlaying) return;
        ShowReviewBitmap(cleaned, before: false);
        ShowReviewBitmap(original, before: true);
        _reviewSeeking = true; ReviewSeek.Value = Math.Min(moment, ReviewSeek.Maximum); _reviewSeeking = false;
        ReviewTime.Text = Clock(moment);
        // The original side runs straight on; when the cleaned clip crosses a cut, both restart there.
        var index = SegmentIndexOf(SourceTimeOf(moment));
        if (index != _reviewSegmentPlaying && index >= 0) StartReviewPlayback(moment);
    }

    private void ShowReviewBitmap(WriteableBitmap bitmap, bool before)
    {
        var wipe = before ? BeforeImage : AfterImage;
        var side = before ? SideBefore : SideAfter;
        if (!ReferenceEquals(wipe.Source, bitmap)) { wipe.Source = bitmap; side.Source = bitmap; }
        wipe.InvalidateVisual(); side.InvalidateVisual();
    }

    /// <summary>Measures artefacts in original and result: what was gained, what is left, what a re-encode costs.</summary>
    private async Task AssessAsync(FootageItem clip, string target, int quality, string signal)
    {
        _assessedHeadroom = -1;
        GainLabel.Text = HeadroomLabel.Text = LossLabel.Text = "…";
        ReviewAssessment.Text = UiText.MeasuringDetail;
        try
        {
            var report = await EngineAsync(new { action = "assess", source = clip.Path, target,
                segments = clip.Segments.Select(part => new[] { part.In, part.Out }).ToList(),
                quality, signal });
            if (_reviewOutput != target) return;
            _assessedGain = report.GetProperty("gain_percent").GetDouble();
            _assessedHeadroom = report.GetProperty("headroom_percent").GetDouble();
            _assessedLoss = report.GetProperty("reencode_loss_percent").GetDouble();
            _secondPassWorth = report.GetProperty("second_pass_worth").GetBoolean();
            _assessedHeadrooms = report.GetProperty("headrooms").EnumerateObject().ToDictionary(item => item.Name, item => item.Value.GetDouble());
            GainLabel.Text = $"{_assessedGain:0} %";
            HeadroomLabel.Text = $"~{_assessedHeadroom:0} %";
            LossLabel.Text = UiText.F(UiText.LossDetail, _assessedLoss);
            var gains = report.GetProperty("gains");
            var parts = new List<string>();
            foreach (var item in gains.EnumerateObject())
                parts.Add(UiText.F(UiText.ArtefactRemoved, item.Name switch { "grain" => UiText.ArtefactGrain, "colour" => UiText.ArtefactColour, "streaks" => UiText.ArtefactStreaks, _ => UiText.ArtefactBlocks }, item.Value.GetDouble()));
            var advice = _assessedHeadroom < 5
                ? UiText.AssessClose
                : _secondPassWorth
                    ? UiText.F(UiText.AssessWorth, _assessedLoss)
                    : UiText.AssessSmall;
            ReviewAssessment.Text = UiText.F(UiText.AssessSummary, string.Join(", ", parts), advice, 16);
        }
        catch (Exception ex)
        {
            GainLabel.Text = HeadroomLabel.Text = LossLabel.Text = "—";
            ReviewAssessment.Text = UiText.F(UiText.AssessFailed, Loc.Engine(ex.Message));
        }
    }

    private Dictionary<string, double> _assessedHeadrooms = new();

    /// <summary>
    /// Settings for the next round, derived from what the measurement found still
    /// missing: each remaining artefact raises the control that fights it. A second
    /// pass on the cleaned file also keeps the encode near lossless and skips what
    /// was already done (OSD, upscaling).
    /// </summary>
    private (RepairPreset Settings, string Explanation) SuggestSettings(bool secondPass)
    {
        var current = CurrentRepair();
        var next = current.Clone();
        var reasons = new List<string>();
        double Left(string key) => _assessedHeadrooms.TryGetValue(key, out var value) ? value : 0;
        static int Raise(int value, double headroom, int ceiling = 100) =>
            (int)Math.Clamp(value + Math.Round(headroom * 0.6), 0, ceiling);
        // Film grain added by the look is wanted; it is not noise left to remove.
        var grain = current.Look && current.LookGrain > 0 ? 0 : Left("grain");
        if (grain > 5 || Left("streaks") > 5)
        {
            next.Noise = Raise(current.Noise, Math.Max(grain, Left("streaks")));
            if (next.Noise != current.Noise) reasons.Add(UiText.F(UiText.ReasonNoise, current.Noise, next.Noise));
        }
        if (current.Signal == "analog" && Left("colour") > 5)
        {
            next.Colour = Raise(current.Colour, Left("colour"));
            if (next.Colour != current.Colour) reasons.Add(UiText.F(UiText.ReasonColour, current.Colour, next.Colour));
        }
        if (current.Signal == "digital" && Left("seams") > 5)
        {
            next.Block = Raise(current.Block, Left("seams"));
            if (next.Block != current.Block) reasons.Add(UiText.F(UiText.ReasonBlock, current.Block, next.Block));
        }
        if (_assessedHeadroom >= 15 && current.Effort != "best") { next.Effort = "best"; reasons.Add(UiText.ReasonBest); }
        if (secondPass)
        {
            // The first pass already handled these; doing them twice only blurs.
            if (current.Osd) { next.Osd = false; reasons.Add(UiText.ReasonOsdOff); }
            if (current.Size != 100) { next.Size = 100; reasons.Add(UiText.ReasonSizeOriginal); }
            if (current.Look) { next.Look = false; reasons.Add(UiText.ReasonLookOff); }
            if (current.Signal == "analog" && Left("colour") <= 5 && current.Colour > 20) { next.Colour = 20; reasons.Add(UiText.ReasonColourClean); }
            next.Detail = Math.Min(current.Detail, 10);
            next.Quality = Math.Min(current.Quality, 15); reasons.Add(UiText.F(UiText.ReasonQuality, next.Quality));
        }
        var explanation = reasons.Count == 0 ? UiText.RefineNothing
            : UiText.F(UiText.RefineChosen, string.Join(", ", reasons));
        return (next, explanation);
    }

    /// <summary>Not happy yet: change the settings and export the original again, lossless.</summary>
    private void TryAgain_Click(object? sender, RoutedEventArgs e)
    {
        StopReviewPlayback();
        if (_reviewClip != null && _queue.Contains(_reviewClip)) Select(_reviewClip);
        ShowPage(2);
        if (_assessedHeadroom >= 0)
        {
            var (settings, explanation) = SuggestSettings(secondPass: false);
            ApplyRepair(settings);
            PreviewStatus.Text = UiText.F(UiText.TryOtherChosen, explanation);
        }
        else PreviewStatus.Text = UiText.TryOtherNote;
    }

    /// <summary>A second pass over the cleaned file, for repairs that build on the first result.</summary>
    private async void Refine_Click(object? sender, RoutedEventArgs e)
    {
        if (_reviewOutput == null || !File.Exists(_reviewOutput)) { ReviewNote.Text = UiText.CleanedGone; return; }
        StopReviewPlayback();
        if (_assessedHeadroom >= 0 && !_secondPassWorth)
        {
            // Say what the second pass costs and what it can bring before anything is queued.
            ReviewNote.Text = UiText.F(UiText.RefineWarning, _assessedHeadroom, _assessedLoss);
            _assessedHeadroom = -1;
            return;
        }
        var output = _reviewOutput;
        await AddFilesAsync([output]);
        var added = _queue.FirstOrDefault(clip => clip.Path.Equals(output, StringComparison.OrdinalIgnoreCase));
        if (added == null) return;
        Select(added);
        ShowPage(2);
        var (settings, explanation) = SuggestSettings(secondPass: true);
        ApplyRepair(settings);
        PreviewStatus.Text = UiText.F(UiText.SecondPass, explanation, Math.Max(0.5, (settings.Quality - 12) * 0.6));
    }

    private void ReviewMode_Changed(object? sender, RoutedEventArgs e)
    {
        if (SideBySideView == null || WipeView == null || WipeSlider == null) return;
        var wipe = ReviewWipeMode.IsChecked == true;
        SideBySideView.IsVisible = !wipe;
        WipeView.IsVisible = wipe;
        UpdateWipe();
    }
    private void Wipe_Changed(object? sender, Avalonia.Controls.Primitives.RangeBaseValueChangedEventArgs e) => UpdateWipe();

    private bool _wipeDragging;
    private void SetWipeFromPointer(double x)
    {
        var shown = ImageRect(WipeView, _reviewAspect);
        WipeSlider.Value = Math.Clamp((x - shown.X) / Math.Max(1, shown.Width) * 100, 0, 100);
    }
    private void Wipe_PointerPressed(object? sender, PointerPressedEventArgs e)
    {
        if (!e.GetCurrentPoint(WipeView).Properties.IsLeftButtonPressed) return;
        _wipeDragging = true; e.Pointer.Capture(WipeView);
        SetWipeFromPointer(e.GetPosition(WipeView).X);
        e.Handled = true;
    }
    private void Wipe_PointerMoved(object? sender, PointerEventArgs e)
    {
        if (_wipeDragging) SetWipeFromPointer(e.GetPosition(WipeView).X);
    }
    private void Wipe_PointerReleased(object? sender, PointerReleasedEventArgs e) { _wipeDragging = false; e.Pointer.Capture(null); }
    private void Review_SizeChanged(object? sender, SizeChangedEventArgs e) => UpdateWipe();

    private void UpdateWipe()
    {
        if (ReviewCanvas == null || AfterImage == null || Divider == null || WipeSlider == null) return;
        var shown = ImageRect(ReviewCanvas, _reviewAspect);
        var width = ReviewCanvas.Bounds.Width;
        var edge = shown.X + shown.Width * WipeSlider.Value / 100;
        AfterImage.Clip = new RectangleGeometry(new Rect(edge, 0, Math.Max(0, width - edge), ReviewCanvas.Bounds.Height));
        Divider.Margin = new Thickness(edge - 1, 0, 0, 0);
        DividerGrip.Margin = new Thickness(edge - 13, 0, 0, 0);
    }

    // ---------------------------------------------------------------- share video

    private bool _fillingShare;
    private string? _shareMade;

    private ShareChoices ShareMemory => _settings.Share ??= new ShareChoices();
    private bool ShareIsVertical => ShareVertical.IsChecked == true;

    /// <summary>Opens the share card with the last choices, ready for one click on Make video.</summary>
    private void Share_Click(object? sender, RoutedEventArgs e)
    {
        if (_reviewOutput == null || !File.Exists(_reviewOutput))
        { ReviewNote.Text = UiText.CleanedGone; return; }
        StopReviewPlayback();
        _fillingShare = true;
        var memory = ShareMemory;
        ShareVertical.IsChecked = memory.Format == "vertical";
        ShareLandscape.IsChecked = memory.Format != "vertical";
        _fillingShare = false;
        ShowShareSound();
        _shareMade = null;
        ShareFolderButton.IsVisible = false;
        ShareStatus.Text = UiText.F(UiText.ShareWillSave, System.IO.Path.GetFileName(ShareTarget()));
        ShareOverlay.IsVisible = true;
        PageScroller.Effect = new BlurEffect { Radius = 10 };
    }

    /// <summary>Sound is remembered per format: on for 16:9, off for 9:16 unless changed.</summary>
    private void ShowShareSound()
    {
        _fillingShare = true;
        ShareSound.IsChecked = ShareIsVertical ? ShareMemory.SoundVertical : ShareMemory.SoundLandscape;
        _fillingShare = false;
    }

    private void ShareFormat_Changed(object? sender, RoutedEventArgs e)
    {
        if (_fillingShare || ShareSound == null || sender is RadioButton { IsChecked: not true }) return;
        ShowShareSound();
        RememberShare();
        ShareStatus.Text = UiText.F(UiText.ShareWillSave, System.IO.Path.GetFileName(ShareTarget()));
    }

    private void ShareSound_Changed(object? sender, RoutedEventArgs e)
    {
        if (_fillingShare || ShareSound == null) return;
        RememberShare();
    }

    private void RememberShare()
    {
        var memory = ShareMemory;
        var vertical = ShareIsVertical;
        memory.Format = vertical ? "vertical" : "landscape";
        if (vertical) memory.SoundVertical = ShareSound.IsChecked == true;
        else memory.SoundLandscape = ShareSound.IsChecked == true;
        _settings.Save();
    }

    /// <summary>"name-youtube.mp4" or "name-short.mp4" next to the cleaned file, numbered if taken.</summary>
    private string ShareTarget()
    {
        var output = _reviewOutput ?? "";
        var folder = System.IO.Path.GetDirectoryName(output) ?? "";
        var stem = System.IO.Path.GetFileNameWithoutExtension(output) + (ShareIsVertical ? "-short" : "-youtube");
        var target = System.IO.Path.Combine(folder, stem + ".mp4");
        for (var number = 2; File.Exists(target); number++) target = System.IO.Path.Combine(folder, $"{stem}-{number}.mp4");
        return target;
    }

    private void ShareClose_Click(object? sender, RoutedEventArgs e) => CloseShare();

    private void CloseShare()
    {
        ShareOverlay.IsVisible = false;
        if (!BusyOverlay.IsVisible) PageScroller.Effect = null;
    }

    private void ShareFolder_Click(object? sender, RoutedEventArgs e)
    {
        var folder = System.IO.Path.GetDirectoryName(_shareMade ?? _reviewOutput ?? "");
        if (folder == null || !Directory.Exists(folder)) return;
        try { OpenInFileManager(folder); }
        catch (Exception ex) { ShareStatus.Text = UiText.F(UiText.OpenFolderFailed, ex.Message); }
    }

    private CancellationTokenSource? _shareCancel;

    private async void ShareMake_Click(object? sender, RoutedEventArgs e)
    {
        if (_shareCancel != null || _reviewOutput == null) return;
        if (!File.Exists(_reviewOutput)) { ShareStatus.Text = UiText.CleanedGone; return; }
        RememberShare();
        var target = ShareTarget();
        var vertical = ShareIsVertical;
        _shareCancel = new CancellationTokenSource();
        var started = DateTime.UtcNow;
        ShareOverlay.IsVisible = false;
        ShowBusy(vertical ? UiText.ShareBusyVertical : UiText.ShareBusyLandscape,
            vertical ? UiText.ShareBusyVerticalText : UiText.ShareBusyLandscapeText,
            () => _shareCancel?.Cancel());
        UpdateBusy(0);
        try
        {
            var result = await EngineAsync(new
            {
                action = "share", target = _reviewOutput, output = target,
                format = vertical ? "vertical" : "landscape", sound = ShareSound.IsChecked == true,
            }, item =>
            {
                var done = item.GetProperty("fraction").GetDouble();
                Dispatcher.UIThread.Post(() => UpdateBusy(done, null, UiText.F(UiText.ElapsedShort, Duration(DateTime.UtcNow - started))));
            }, _shareCancel.Token);
            _shareMade = target;
            var seconds = result.GetProperty("duration").GetDouble();
            ShareStatus.Text = UiText.F(UiText.ShareSaved, System.IO.Path.GetFileName(target), seconds, result.GetProperty("width").GetInt32(), result.GetProperty("height").GetInt32());
            ShareFolderButton.IsVisible = true;
        }
        catch (OperationCanceledException)
        {
            RemovePartialOutput(target);
            ShareStatus.Text = UiText.ShareCanceled;
        }
        catch (Exception ex) { ShareStatus.Text = UiText.F(UiText.ShareFailed, Loc.Engine(ex.Message)); }
        finally
        {
            _shareCancel.Dispose(); _shareCancel = null;
            HideBusy();
            ShareOverlay.IsVisible = true;
            PageScroller.Effect = new BlurEffect { Radius = 10 };
        }
    }
}
