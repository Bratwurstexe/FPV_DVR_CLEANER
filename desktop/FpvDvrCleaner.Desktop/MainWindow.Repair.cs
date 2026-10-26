using System.Globalization;
using System.Text.Json;
using Avalonia;
using Avalonia.Automation;
using Avalonia.Controls;
using Ellipse = Avalonia.Controls.Shapes.Ellipse;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Layout;
using Avalonia.LogicalTree;
using Avalonia.Media;
using Avalonia.Threading;

namespace FpvDvrCleaner.Desktop;

/// <summary>
/// The Repair page: two simple questions (signal, how bad), Best quality, the OSD,
/// the user's own presets, Advanced with a Show button per repair, the check of one
/// frame (rendered again on its own after a change) and the OSD mask editor.
/// </summary>
public partial class MainWindow
{
    // The level or user preset the current settings started from; after a change by hand the header says so.
    private RepairPreset? _basePreset;
    private readonly List<(RepairPreset Preset, Button Row)> _presetRows = [];
    // Inline editor of the preset list: a new preset, a rename, or a delete to confirm.
    private RepairPreset? _renaming, _deleting;
    private bool _savingPreset;
    private bool _syncingRepair;
    // Recordings whose signal was detected or chosen by hand, so it is not changed behind the user's back.
    private readonly HashSet<string> _signalSettled = new(StringComparer.OrdinalIgnoreCase);
    // The frame is rendered again shortly after any change; a newer change cancels an older render.
    private readonly DispatcherTimer _autoRender = new() { Interval = TimeSpan.FromMilliseconds(1200) };
    private CancellationTokenSource? _previewCancel;

    private void SetUpRepairPage()
    {
        _autoRender.Tick += async (_, _) => { _autoRender.Stop(); await AutoRenderAsync(); };
        foreach (var button in this.GetLogicalDescendants().OfType<Button>().Where(button => button.Classes.Contains("eye")))
            button.Content = EyeContent();
        MaskView.Changed += ShowMaskState;
        MaskView.BrushSizeRequested += size => MaskBrushSlider.Value = size;
        // The preset of last time comes back; without one, any level or preset the settings match.
        var current = CurrentRepair();
        _basePreset = AllPresets.FirstOrDefault(preset => PresetKey(preset) == _settings.LastPreset)
                      ?? AllPresets.FirstOrDefault(preset => preset.Matches(current));
        BuildUserPresets();
        ShowPresetState();
        ShowOsdState();
    }

    private static StackPanel EyeContent() => new()
    {
        Orientation = Orientation.Horizontal, Spacing = 4,
        Children =
        {
            new Avalonia.Controls.Shapes.Path
            {
                Data = Geometry.Parse("M0.5,5 C2.5,1 9.5,1 11.5,5 C9.5,9 2.5,9 0.5,5 Z M6,3.2 A1.8,1.8 0 1 1 5.99,3.2 Z"),
                Stroke = new SolidColorBrush(Color.Parse("#4E565C")), StrokeThickness = 1.2, Fill = null,
                Width = 12, Height = 10, Stretch = Stretch.None, VerticalAlignment = VerticalAlignment.Center,
            },
            new TextBlock { Text = UiText.ShowButton, VerticalAlignment = VerticalAlignment.Center },
        },
    };

    private IEnumerable<RepairPreset> AllPresets => RepairPreset.BuiltIn.Concat(_settings.Presets);

    private static string PresetKey(RepairPreset preset) => preset.IsBuiltIn ? preset.Id : "user:" + preset.Name;

    private string Signal => IsAnalog ? "analog" : "digital";

    // ---- signal, how bad, best quality

    /// <summary>
    /// The signal of a recording, when it is clear: HDZero records at 90 fps and more,
    /// analog DVRs at 60 fps and less, in SD or with the 4:3 picture between black bars.
    /// Otherwise the last choice stays.
    /// </summary>
    private static string? DetectSignal(FootageItem clip)
    {
        if (clip.Fps >= 85) return "digital";
        if (clip.Width <= 720 || (clip.Box != null && clip.Fps is > 0 and <= 61)) return "analog";
        return null;
    }

    /// <summary>
    /// Preselects the signal of a newly selected recording, once, keeping the chosen level.
    /// Before the black bars are measured an unclear recording waits for that measurement.
    /// </summary>
    private void SettleSignal(FootageItem clip)
    {
        if (_selected != clip || _signalSettled.Contains(clip.Path)) return;
        var signal = DetectSignal(clip);
        if (signal == null && !clip.BoxMeasured) return;
        _signalSettled.Add(clip.Path);
        if (signal == null || signal == Signal) return;
        var level = CurrentLevel()?.Level ?? (_basePreset is { IsBuiltIn: true } basis ? basis.Level : "normal");
        ApplyLevel(signal, level);
    }

    /// <summary>The built-in level the repairs match, for the chosen signal; null for settings of one's own.</summary>
    private RepairPreset? CurrentLevel()
    {
        var current = CurrentRepair();
        return RepairPreset.BuiltIn.FirstOrDefault(level => level.Signal == current.Signal && level.Matches(current));
    }

    private void ApplyLevel(string signal, string level)
    {
        var preset = RepairPreset.LevelFor(signal, level);
        ApplyRepair(preset.AppliedTo(CurrentRepair()));
        _basePreset = preset;
        RememberSettings();
        RepairChanged();
    }

    private void Level_Click(object? sender, RoutedEventArgs e)
    {
        if (sender is not RadioButton { Tag: string level }) return;
        ClosePresetEditor();
        ApplyLevel(Signal, level);
    }

    /// <summary>A signal chosen by hand keeps the answer to "How bad is it?" for the new signal.</summary>
    private void SignalChoice_Click(object? sender, RoutedEventArgs e)
    {
        if (_selected != null) _signalSettled.Add(_selected.Path);
        var level = _basePreset is { IsBuiltIn: true } basis ? basis.Level : "normal";
        ApplyLevel(Signal, level);
    }

    private void Best_Changed(object? sender, RoutedEventArgs e)
    {
        if (_syncingRepair || BestToggle == null) return;
        var best = BestToggle.IsChecked == true;
        _applyingPreset = true;
        try
        {
            SizeOriginal.IsChecked = !best; Size150.IsChecked = best; Size200.IsChecked = false;
            EffortBest.IsChecked = best; EffortBalanced.IsChecked = !best; EffortFast.IsChecked = false;
        }
        finally { _applyingPreset = false; }
        Setting_Changed(null, e);
        Effort_Changed(null, e);
    }

    /// <summary>Any change on the Repair page: header, answers, presets, OSD line and the frame follow.</summary>
    private void RepairChanged()
    {
        ShowPresetState();
        ShowOsdState();
        RequestAutoRender();
    }

    /// <summary>
    /// The answers that match the settings are selected; with settings of one's own no
    /// level is, and the header says what they were based on, or which preset is in use.
    /// </summary>
    private void ShowPresetState()
    {
        if (PresetState == null || LevelLight == null) return;
        var current = CurrentRepair();
        var level = CurrentLevel();
        var user = _basePreset is { IsBuiltIn: false } own && own.Matches(current) ? own
            : level == null ? _settings.Presets.FirstOrDefault(preset => preset.Matches(current)) : null;
        _syncingRepair = true;
        try
        {
            foreach (var choice in new[] { LevelLight, LevelNormal, LevelStrong })
                choice.IsChecked = level != null && choice.Tag as string == level.Level;
            BestToggle.IsChecked = current.Size == 150 && current.Effort == "best";
        }
        finally { _syncingRepair = false; }
        LevelNote.Text = level?.Description ?? UiText.LevelNone;
        PresetState.Text = user != null ? string.Format(CultureInfo.CurrentCulture, UiText.StateUsing, user.Name)
            : level != null ? level.FullName
            : _basePreset != null ? string.Format(CultureInfo.CurrentCulture, UiText.StateCustom, _basePreset.FullName)
            : UiText.StateCustomNoBase;
        PresetState.Foreground = new SolidColorBrush(Color.Parse(user != null || level != null ? "#20252B" : "#9A4100"));
        foreach (var (preset, row) in _presetRows)
        {
            var active = ReferenceEquals(preset, user);
            row.Classes.Set("active", active);
            if (row.Content is StackPanel { Children: [Ellipse dot, ..] })
            {
                dot.Fill = active ? new SolidColorBrush(Color.Parse("#A64600")) : Brushes.Transparent;
                dot.Stroke = new SolidColorBrush(Color.Parse(active ? "#A64600" : "#9AA3A9"));
            }
        }
    }

    // ---- my presets

    /// <summary>The user's presets as rows like radio buttons, each with a menu: rename, update, delete.</summary>
    private void BuildUserPresets()
    {
        UserPresetList.Children.Clear();
        _presetRows.Clear();
        foreach (var preset in _settings.Presets)
        {
            var row = new Button
            {
                Classes = { "presetrow" },
                Content = new StackPanel
                {
                    Orientation = Orientation.Horizontal, Spacing = 9,
                    Children =
                    {
                        new Ellipse { Width = 12, Height = 12, StrokeThickness = 1.5, VerticalAlignment = VerticalAlignment.Center },
                        new TextBlock { Text = preset.Name, Classes = { "cardname" }, TextWrapping = TextWrapping.NoWrap, TextTrimming = TextTrimming.CharacterEllipsis,
                                        VerticalAlignment = VerticalAlignment.Center },
                    },
                },
            };
            ToolTip.SetTip(row, string.Format(CultureInfo.CurrentCulture, UiText.PresetRowTip, preset.Name, Summary(preset)));
            AutomationProperties.SetName(row, preset.Name);
            row.Click += (_, _) => ChoosePreset(preset);
            var menu = new Button { Classes = { "cardmenu" }, VerticalAlignment = VerticalAlignment.Center,
                Content = new TextBlock { Text = "•••", FontSize = 9, Foreground = new SolidColorBrush(Color.Parse("#4E565C")), VerticalAlignment = VerticalAlignment.Center } };
            ToolTip.SetTip(menu, UiText.PresetMenuTip);
            AutomationProperties.SetName(menu, $"{preset.Name} •••");
            var rename = new MenuItem { Header = UiText.PresetMenuRename };
            rename.Click += (_, _) => StartPresetEditor(preset);
            var update = new MenuItem { Header = UiText.PresetMenuUpdate };
            update.Click += (_, _) => UpdatePreset(preset);
            var delete = new MenuItem { Header = UiText.PresetMenuDelete };
            delete.Click += (_, _) => AskDeletePreset(preset);
            menu.Flyout = new MenuFlyout { Items = { rename, update, delete } };
            var line = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), Children = { row, menu } };
            Grid.SetColumn(menu, 1);
            UserPresetList.Children.Add(line);
            _presetRows.Add((preset, row));
        }
        MyPresetsEmpty.IsVisible = _settings.Presets.Count == 0;
    }

    /// <summary>A short plain description of saved settings, for the tooltip of a preset row.</summary>
    private static string Summary(RepairPreset preset)
    {
        var parts = new List<string> { UiText.SignalName(preset.Signal) };
        parts.Add(preset.Signal == "digital" ? $"{UiText.BlockLabel} {preset.Block}" : $"{UiText.ColourLabel} {preset.Colour}");
        parts.Add($"{UiText.NoiseLabel} {preset.Noise}");
        if (preset.Size != 100) parts.Add((preset.Size / 100.0).ToString("0.#", CultureInfo.InvariantCulture) + "×");
        if (preset.Osd) parts.Add(UiText.OsdRemovedSummary);
        if (preset.Look) parts.Add(UiText.LookLabel);
        return string.Join(" · ", parts);
    }

    private void ChoosePreset(RepairPreset preset)
    {
        ClosePresetEditor();
        ApplyRepair(preset.AppliedTo(CurrentRepair()));
        _basePreset = preset;
        RememberSettings();
        RepairChanged();
    }

    private void SavePreset_Click(object? sender, RoutedEventArgs e) => StartPresetEditor(null);

    /// <summary>A name field under the list: for a new preset, or to rename one.</summary>
    private void StartPresetEditor(RepairPreset? renaming)
    {
        _renaming = renaming; _deleting = null; _savingPreset = true;
        PresetName.Text = renaming?.Name ?? "";
        PresetStatus.IsVisible = false;
        PresetConfirm.IsVisible = false; PresetEditor.IsVisible = true; SavePresetButton.IsVisible = false;
        Dispatcher.UIThread.Post(() => { PresetName.Focus(); PresetName.SelectAll(); }, DispatcherPriority.Input);
    }

    private void ClosePresetEditor()
    {
        _renaming = _deleting = null; _savingPreset = false;
        PresetEditor.IsVisible = false; PresetConfirm.IsVisible = false; SavePresetButton.IsVisible = true;
    }

    private void SayPreset(string message)
    {
        PresetStatus.Text = message;
        PresetStatus.Foreground = new SolidColorBrush(Color.Parse("#5F676D"));
        PresetStatus.IsVisible = true;
    }

    private void PresetEditorCancel_Click(object? sender, RoutedEventArgs e) => ClosePresetEditor();

    private void PresetName_KeyDown(object? sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter) { PresetEditorSave_Click(sender, e); e.Handled = true; }
        else if (e.Key == Key.Escape) { ClosePresetEditor(); e.Handled = true; }
    }

    private bool NameTaken(string name, RepairPreset? except) =>
        _settings.Presets.Any(preset => !ReferenceEquals(preset, except) && preset.Name.Equals(name, StringComparison.OrdinalIgnoreCase))
        || RepairPreset.BuiltIn.Any(preset => preset.FullName.Equals(name, StringComparison.OrdinalIgnoreCase));

    private void PresetEditorSave_Click(object? sender, RoutedEventArgs e)
    {
        if (!_savingPreset) return;
        var name = PresetName.Text?.Trim() ?? "";
        var problem = name.Length == 0 ? UiText.PresetNameEmpty : NameTaken(name, _renaming) ? UiText.PresetNameTaken : null;
        if (problem != null)
        {
            PresetStatus.Text = problem;
            PresetStatus.Foreground = new SolidColorBrush(Color.Parse("#9C3F1F"));
            PresetStatus.IsVisible = true;
            return;
        }
        string message;
        if (_renaming is { } renamed)
        {
            renamed.Name = name;
            message = string.Format(CultureInfo.CurrentCulture, UiText.PresetRenamed, name);
        }
        else
        {
            var saved = CurrentRepair();
            saved.Name = name;
            _settings.Presets.Add(saved);
            _basePreset = saved;
            message = string.Format(CultureInfo.CurrentCulture, UiText.PresetSaved, name);
        }
        ClosePresetEditor();
        RememberSettings();
        BuildUserPresets();
        ShowPresetState();
        SayPreset(message);
    }

    private void UpdatePreset(RepairPreset preset)
    {
        ClosePresetEditor();
        var index = _settings.Presets.IndexOf(preset);
        if (index < 0) return;
        var current = CurrentRepair();
        current.Name = preset.Name;
        _settings.Presets[index] = current;
        _basePreset = current;
        RememberSettings();
        BuildUserPresets();
        ShowPresetState();
        SayPreset(string.Format(CultureInfo.CurrentCulture, UiText.PresetUpdated, preset.Name));
    }

    private void AskDeletePreset(RepairPreset preset)
    {
        ClosePresetEditor();
        _deleting = preset;
        PresetConfirmText.Text = string.Format(CultureInfo.CurrentCulture, UiText.PresetDeleteQuestion, preset.Name);
        PresetStatus.IsVisible = false;
        PresetConfirm.IsVisible = true; SavePresetButton.IsVisible = false;
    }

    private void PresetConfirmDelete_Click(object? sender, RoutedEventArgs e)
    {
        if (_deleting is not { } preset) return;
        _settings.Presets.Remove(preset);
        if (ReferenceEquals(_basePreset, preset)) _basePreset = CurrentLevel();
        ClosePresetEditor();
        RememberSettings();
        BuildUserPresets();
        ShowPresetState();
        SayPreset(string.Format(CultureInfo.CurrentCulture, UiText.PresetDeleted, preset.Name));
    }

    // ---- the big check: one frame, original against result or without against with one effect

    /// <summary>The frame the check shows: the trim playhead, inside a kept part.</summary>
    private double PreviewMoment(FootageItem clip) =>
        Timeline.SegmentAt(_head) >= 0 && _selected == clip ? Math.Clamp(_head, clip.In, Math.Max(clip.In, clip.Out - 0.3))
            : Math.Clamp(clip.Segments[Math.Max(0, _selected == clip ? Timeline.NearestSegment(_head) : 0)].In, clip.In, Math.Max(clip.In, clip.Out - 0.3));

    private void RequestAutoRender()
    {
        if (_page != 2 || _selected == null) return;
        _autoRender.Stop();
        _autoRender.Start();
    }

    /// <summary>After a change the frame is rendered again on its own, without covering the page.</summary>
    private async Task AutoRenderAsync()
    {
        if (_page != 2 || _selected is not { } clip || MaskOverlay.IsVisible || BusyOverlay.IsVisible) return;
        await RenderCompareAsync(clip, BuildRecipe(clip), null, UiText.F(UiText.ClipAt, clip, Clock(PreviewMoment(clip))),
            UiText.LabelOriginal, UiText.LabelCleaned, UiText.PreviewStatusDone, quiet: true);
    }

    private async void Preview_Click(object? sender, RoutedEventArgs e)
    {
        if (_selected == null) { PreviewStatus.Text = UiText.NeedClip; return; }
        _autoRender.Stop();
        var clip = _selected;
        await RenderCompareAsync(clip, BuildRecipe(clip), null, UiText.F(UiText.ClipAt, clip, Clock(PreviewMoment(clip))),
            UiText.LabelOriginal, UiText.LabelCleaned, UiText.PreviewStatusDone);
    }

    /// <summary>Renders the frame with only one effect changed: its current value against 0.</summary>
    private async void ShowEffect_Click(object? sender, RoutedEventArgs e)
    {
        if (sender is not Button { Tag: string effect }) return;
        if (_selected == null) { PreviewStatus.Text = UiText.NeedClip; return; }
        _autoRender.Stop();
        var clip = _selected;
        var current = CurrentRepair();
        var without = current.Clone();
        var (label, explain, value) = effect switch
        {
            "colour" => (UiText.ColourLabel, UiText.ColourExplain, current.Colour),
            "block" => (UiText.BlockLabel, UiText.BlockExplain, current.Block),
            "noise" => (UiText.NoiseLabel, UiText.NoiseExplain, current.Noise),
            _ => (UiText.DetailLabel, UiText.DetailExplain, current.Detail),
        };
        switch (effect)
        {
            case "colour": without.Colour = 0; break;
            case "block": without.Block = 0; break;
            case "noise": without.Noise = 0; break;
            default: without.Detail = 0; break;
        }
        if (value == 0) { PreviewStatus.Text = string.Format(CultureInfo.CurrentCulture, UiText.ShowOff, label); return; }
        await RenderCompareAsync(clip, RecipeOf(current, clip), RecipeOf(without, clip),
            string.Format(CultureInfo.CurrentCulture, UiText.ShowTitle, label),
            $"{UiText.LabelWithout} · 0", $"{UiText.LabelWith} · {value}", explain,
            string.Format(CultureInfo.CurrentCulture, UiText.ShowRendering, label.ToLowerInvariant()));
    }

    /// <summary>
    /// Renders one frame and puts it into the split view. ``reference`` renders the left
    /// side with other settings instead of showing the original. A quiet render (after a
    /// change) only shows a small badge and gives way to any newer render.
    /// </summary>
    private async Task RenderCompareAsync(FootageItem clip, object recipe, object? reference, string title,
        string leftLabel, string rightLabel, string doneText, string? busyText = null, bool quiet = false)
    {
        if (Mode == "model" && !File.Exists(ModelPath.Text)) { PreviewStatus.Text = UiText.NeedModelOrStandard; return; }
        _previewCancel?.Cancel();
        var cancel = _previewCancel = new CancellationTokenSource();
        var moment = PreviewMoment(clip);
        var before = TempFile("before");
        var after = TempFile("after");
        var plain = TempFile("plain");
        if (quiet) RenderBadge.IsVisible = true;
        else
        {
            PreviewButton.IsEnabled = false;
            PreviewStatus.Text = busyText ?? UiText.RenderingFrameSteps;
            ShowBusy(reference == null ? UiText.RenderingFrame : title, busyText ?? UiText.F(UiText.ClipAtSteps, clip, Clock(moment)));
        }
        try
        {
            var outcome = await EngineAsync(new { action = "preview", source = clip.Path, seconds = moment, before, after,
                plain = LookOn && reference == null ? plain : null, recipe, reference_recipe = reference, mode = Mode, model = ModelPath.Text },
                null, cancel.Token);
            if (cancel.IsCancellationRequested || clip != _selected) return;
            var original = LoadBitmap(before);
            var cleaned = LoadBitmap(after);
            // A rendered reference may be upscaled like the result; the box scales along.
            _compareOriginal = Cropped(original, clip.Box, clip.Width);
            _comparePlain = reference == null && outcome.ValueKind == JsonValueKind.Object && outcome.TryGetProperty("plain", out var made)
                            && made.ValueKind == JsonValueKind.String && File.Exists(plain)
                ? Cropped(LoadBitmap(plain), clip.Box, clip.Width) : null;
            LookCompare.IsVisible = _comparePlain != null;
            if (reference != null) LookCompare.IsChecked = false;
            CompareBefore.Source = _compareOriginal;
            ShowCompareLeft();
            CompareLeftLabel.Text = leftLabel;
            CompareRightLabel.Text = rightLabel;
            CompareAfter.Source = Cropped(cleaned, clip.Box, clip.Width);
            _compareAspect = clip.Box is { } cb ? (double)cb.Width / cb.Height : (double)clip.Width / clip.Height;
            PreviewTitle.Text = title;
            CompareHint.IsVisible = false;
            CompareDivider.IsVisible = true;
            PreviewStatus.Text = doneText;
            ToolTip.SetTip(PreviewStatus, doneText);
            UpdateCompare();
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { if (!cancel.IsCancellationRequested) PreviewStatus.Text = UiText.F(UiText.PreviewFailed, Loc.Engine(ex.Message)); }
        finally
        {
            DeleteTemp(before, after, plain);
            if (ReferenceEquals(_previewCancel, cancel)) { RenderBadge.IsVisible = false; _previewCancel = null; }
            if (!quiet) { PreviewButton.IsEnabled = true; HideBusy(); }
            cancel.Dispose();
        }
    }

    // ---- OSD: keep or remove, and the hand corrections

    private void Osd_Changed(object? sender, RoutedEventArgs e)
    {
        if (sender is RadioButton { IsChecked: not true }) return;
        Setting_Changed(sender, e);
    }

    private void ShowOsdState()
    {
        if (OsdStatus == null || FixOsdButton == null) return;
        var remove = RemoveOsd.IsChecked == true;
        FixOsdButton.IsVisible = remove;
        var corrected = _selected?.ManualMask is { } mask && File.Exists(mask);
        OsdStatus.Text = !remove ? UiText.OsdKeepNote : corrected ? UiText.OsdRemoveHandNote : UiText.OsdRemoveNote;
    }

    private async void DetectOsd_Click(object? sender, RoutedEventArgs e)
    {
        if (_selected == null) { PreviewStatus.Text = UiText.NeedClip; return; }
        var clip = _selected;
        var target = TempFile("osd");
        DetectOsdButton.IsEnabled = false;
        ShowBusy(UiText.MaskLoading, string.Format(CultureInfo.CurrentCulture, UiText.MaskLoadingText, clip));
        try
        {
            var outcome = await EngineAsync(new { action = "osd", source = clip.Path, start = clip.In, end = clip.Out,
                seconds = PreviewMoment(clip), target, manual_mask = clip.ManualMask is { } mask && File.Exists(mask) ? mask : null });
            var marked = Cropped(LoadBitmap(target), clip.Box, clip.Width);
            _compareOriginal = _comparePlain = null; LookCompare.IsVisible = false; ShowCompareLeft();
            _compareAspect = clip.Box is { } cb ? (double)cb.Width / cb.Height : (double)clip.Width / clip.Height;
            CompareBefore.Source = marked; CompareAfter.Source = marked; CompareHint.IsVisible = false;
            PreviewTitle.Text = UiText.OverlaysMarked;
            PreviewStatus.Text = outcome.GetProperty("found").GetBoolean()
                ? UiText.F(UiText.OverlaysFound, outcome.GetProperty("coverage").GetDouble())
                : UiText.NoOverlays;
        }
        catch (Exception ex) { PreviewStatus.Text = string.Format(CultureInfo.CurrentCulture, UiText.MaskFailed, ex.Message); }
        finally { DeleteTemp(target); DetectOsdButton.IsEnabled = true; HideBusy(); }
    }

    private FootageItem? _maskClip;

    /// <summary>Opens the mask editor over the Repair page with the frame at the playhead.</summary>
    private async void FixOsd_Click(object? sender, RoutedEventArgs e)
    {
        if (_selected == null) { PreviewStatus.Text = UiText.NeedClip; return; }
        var clip = _selected;
        var marked = TempFile("osd");
        var frameFile = TempFile("osd-frame");
        var maskFile = TempFile("osd-mask");
        ShowBusy(UiText.MaskLoading, string.Format(CultureInfo.CurrentCulture, UiText.MaskLoadingText, clip));
        try
        {
            await EngineAsync(new { action = "osd", source = clip.Path, start = clip.In, end = clip.Out, seconds = PreviewMoment(clip),
                target = marked, frame_target = frameFile, mask_target = maskFile });
            using var frame = LoadBitmap(frameFile);
            using var automatic = LoadBitmap(maskFile);
            int width = frame.PixelSize.Width, height = frame.PixelSize.Height;
            var pixels = MaskCanvas.Pixels(frame);
            var maskPixels = MaskCanvas.Pixels(automatic);
            var found = new byte[width * height];
            for (var i = 0; i < found.Length && i * 4 < maskPixels.Length; i++) found[i] = maskPixels[i * 4 + 2] > 127 ? (byte)1 : (byte)0;
            var corrections = clip.ManualMask is { } saved && File.Exists(saved) ? MaskCanvas.LoadCorrections(saved, width, height) : null;
            var view = clip.Box is { } box ? new PixelRect(box.X, box.Y, box.Width, box.Height) : new PixelRect(0, 0, width, height);
            _maskClip = clip;
            MaskTitleText.Text = $"{UiText.MaskTitle} · {clip}";
            MaskView.Load(pixels, found, corrections, width, height, view);
            SetMaskTool(MaskCanvas.Tool.BrushAdd);
            MaskBrushSlider.Value = Math.Clamp(Math.Round(height / 50.0), 6, 40);
            MaskOverlay.IsVisible = true;
            PageScroller.Effect = new BlurEffect { Radius = 10 };
            MaskView.Focus();
        }
        catch (Exception ex) { PreviewStatus.Text = string.Format(CultureInfo.CurrentCulture, UiText.MaskFailed, ex.Message); }
        finally
        {
            DeleteTemp(marked, frameFile, maskFile);
            HideBusy();
            if (MaskOverlay.IsVisible) PageScroller.Effect = new BlurEffect { Radius = 10 };
        }
    }

    private void ShowMaskState()
    {
        MaskUndoButton.IsEnabled = MaskView.CanUndo;
        MaskRedoButton.IsEnabled = MaskView.CanRedo;
        MaskResetButton.IsEnabled = MaskView.HasCorrections;
        var (automatic, corrected) = MaskView.Coverage();
        MaskStatsText.Text = string.Format(CultureInfo.CurrentCulture, UiText.MaskStats, automatic, corrected);
    }

    private void SetMaskTool(MaskCanvas.Tool tool)
    {
        MaskView.Current = tool;
        MaskBrushAdd.IsChecked = tool == MaskCanvas.Tool.BrushAdd;
        MaskBrushKeep.IsChecked = tool == MaskCanvas.Tool.BrushKeep;
        MaskBoxAdd.IsChecked = tool == MaskCanvas.Tool.BoxAdd;
        MaskBoxKeep.IsChecked = tool == MaskCanvas.Tool.BoxKeep;
        MaskView.InvalidateVisual();
    }

    private void MaskTool_Click(object? sender, RoutedEventArgs e) => SetMaskTool(
        ReferenceEquals(sender, MaskBrushKeep) ? MaskCanvas.Tool.BrushKeep
        : ReferenceEquals(sender, MaskBoxAdd) ? MaskCanvas.Tool.BoxAdd
        : ReferenceEquals(sender, MaskBoxKeep) ? MaskCanvas.Tool.BoxKeep : MaskCanvas.Tool.BrushAdd);

    private void MaskBrush_Changed(object? sender, Avalonia.Controls.Primitives.RangeBaseValueChangedEventArgs e)
    {
        if (MaskView == null || MaskBrushValue == null) return;
        MaskView.BrushSize = MaskBrushSlider.Value;
        MaskBrushValue.Text = $"{MaskBrushSlider.Value:0} px";
        MaskView.InvalidateVisual();
    }

    private void MaskUndo_Click(object? sender, RoutedEventArgs e) => MaskView.Undo();
    private void MaskRedo_Click(object? sender, RoutedEventArgs e) => MaskView.Redo();
    private void MaskReset_Click(object? sender, RoutedEventArgs e) => MaskView.Reset();
    private void MaskCancel_Click(object? sender, RoutedEventArgs e) => CloseMaskEditor();

    private void CloseMaskEditor()
    {
        MaskOverlay.IsVisible = false;
        MaskView.Unload();
        _maskClip = null;
        if (!BusyOverlay.IsVisible) PageScroller.Effect = null;
    }

    /// <summary>Saves the corrections for the recording; every copy of it in the queue uses them.</summary>
    private void MaskDone_Click(object? sender, RoutedEventArgs e)
    {
        if (_maskClip is not { } clip || !MaskView.HasPicture) { CloseMaskEditor(); return; }
        var path = UserSettings.MaskPath(clip.Path);
        string? kept = null;
        try
        {
            if (MaskView.HasCorrections)
            {
                MaskCanvas.SaveCorrections(MaskView.Corrections, MaskView.FrameWidth, MaskView.FrameHeight, path);
                kept = path;
            }
            else if (File.Exists(path)) File.Delete(path);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            MaskStatsText.Text = ex.Message;
            return;
        }
        foreach (var item in _queue.Where(item => item.Path.Equals(clip.Path, StringComparison.OrdinalIgnoreCase))) item.ManualMask = kept;
        clip.ManualMask = kept;
        CloseMaskEditor();
        RememberSettings();
        PreviewStatus.Text = string.Format(CultureInfo.CurrentCulture, kept != null ? UiText.MaskSaved : UiText.MaskCleared, clip);
        RepairChanged();
    }

    /// <summary>Keys of the mask editor: tools, undo and redo, Escape and Enter.</summary>
    private bool MaskKey(KeyEventArgs e)
    {
        var control = e.KeyModifiers.HasFlag(KeyModifiers.Control) || e.KeyModifiers.HasFlag(KeyModifiers.Meta);
        switch (e.Key)
        {
            case Key.Escape: CloseMaskEditor(); return true;
            case Key.Enter: MaskDone_Click(null, e); return true;
            case Key.Z when control && e.KeyModifiers.HasFlag(KeyModifiers.Shift): MaskView.Redo(); return true;
            case Key.Z when control: MaskView.Undo(); return true;
            case Key.Y when control: MaskView.Redo(); return true;
            case Key.A when !control: SetMaskTool(MaskCanvas.Tool.BrushAdd); return true;
            case Key.E when !control: SetMaskTool(MaskCanvas.Tool.BrushKeep); return true;
            case Key.R when !control: SetMaskTool(MaskCanvas.Tool.BoxAdd); return true;
            case Key.K when !control: SetMaskTool(MaskCanvas.Tool.BoxKeep); return true;
            default: return false;
        }
    }
}
