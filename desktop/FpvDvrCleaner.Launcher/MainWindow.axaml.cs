using System.Globalization;
using System.Runtime.InteropServices;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Media;
using Avalonia.Platform.Storage;
using Avalonia.Threading;

namespace FpvDvrCleaner.Launcher;

public partial class MainWindow : Window
{
    readonly LauncherSettings settings = LauncherSettings.Load();
    readonly Dictionary<Slot, Variant> chosen = new();
    Installer installer;
    string? folderProblem;
    Release? release;
    string? releaseError;
    CancellationTokenSource? busy;
    bool closeWhenDone;

    public MainWindow()
    {
        InitializeComponent();
        installer = new Installer(settings.EffectiveFolder(out var savedMissing));
        UseFolder(installer.Folder, savedMissing ? $"The saved folder {settings.InstallFolder} is not available (drive unplugged?). Using the launcher's folder for now." : null);

        var here = Variant.CurrentSlot;
        WindowsHere.IsVisible = here == Slot.Windows;
        MacHere.IsVisible = here == Slot.MacOS;
        LinuxHere.IsVisible = here == Slot.Linux;
        foreach (var slot in Enum.GetValues<Slot>())
            chosen[slot] = Variant.DefaultFor(slot);
        DetectedLabel.Text = here switch
        {
            Slot.Windows => "Windows detected on this PC · already selected",
            Slot.MacOS => $"Mac with {(chosen[Slot.MacOS] == Variant.MacArm ? "Apple Silicon" : "Intel")} detected · already selected",
            Slot.Linux => "Linux detected on this PC · already selected",
            _ => "Pick the system the app should run on",
        };

        (here switch { Slot.MacOS => MacCard, Slot.Linux => LinuxCard, _ => WindowsCard }).IsChecked = true;
        Refresh();
        Opened += async (_, _) => await LoadReleaseAsync();
        Closing += Window_Closing;
    }

    Slot SelectedSlot => MacCard.IsChecked == true ? Slot.MacOS : LinuxCard.IsChecked == true ? Slot.Linux : Slot.Windows;
    Variant SelectedVariant => chosen[SelectedSlot];

    RadioButton SegmentOf(Variant v) =>
        v == Variant.MacArm ? MacArm : v == Variant.MacIntel ? MacIntel :
        v == Variant.LinuxAppImage ? LinuxAppImage : v == Variant.LinuxFolder ? LinuxFolder : WindowsX64;

    /// <summary>Switches to an install folder: finishes an interrupted install there and checks it can be written.</summary>
    void UseFolder(string folder, string? note)
    {
        installer = new Installer(folder);
        installer.RecoverInterrupted();
        folderProblem = installer.CheckWritable();
        FolderLabel.Text = installer.Folder;
        var isLauncherFolder = SameFolder(installer.Folder, Installer.LauncherFolder());
        FolderNote.Text = folderProblem is not null ? "Not writable for you" : note ?? (isLauncherFolder ? "Next to the launcher (default)" : "Your chosen folder · remembered for next time");
        FolderNote.Foreground = new SolidColorBrush(Color.Parse(folderProblem is not null || note is not null ? "#F0A060" : "#98A2AA"));
        ResetFolderButton.IsVisible = !isLauncherFolder || !string.IsNullOrEmpty(settings.InstallFolder);
    }

    static bool SameFolder(string a, string b) =>
        string.Equals(Path.GetFullPath(a).TrimEnd(Path.DirectorySeparatorChar), Path.GetFullPath(b).TrimEnd(Path.DirectorySeparatorChar),
                      OperatingSystem.IsLinux() ? StringComparison.Ordinal : StringComparison.OrdinalIgnoreCase);

    async Task LoadReleaseAsync()
    {
        release = null;
        releaseError = null;
        ReleaseDot.Fill = new SolidColorBrush(Color.Parse("#98A2AA"));
        ReleaseLabel.Text = "Checking for updates…";
        Refresh();
        try
        {
            release = await GitHub.LatestAsync(CancellationToken.None);
            ReleaseDot.Fill = new SolidColorBrush(Color.Parse("#4CC38A"));
            ReleaseLabel.Text = release.Published == DateTimeOffset.MinValue
                ? $"Latest {release.Tag}"
                : $"Latest {release.Tag} · {release.Published.LocalDateTime.ToString("d MMM yyyy", CultureInfo.InvariantCulture)}";
        }
        catch (Exception error)
        {
            installer.Log($"Release check failed: {error}");
            releaseError = error is HttpRequestException or TaskCanceledException
                ? "GitHub could not be reached. Check the internet connection."
                : error.Message;
            ReleaseDot.Fill = new SolidColorBrush(Color.Parse("#E5484D"));
            ReleaseLabel.Text = "Offline";
        }
        Refresh();
    }

    void System_Changed(object? sender, RoutedEventArgs e)
    {
        if (sender is RadioButton { IsChecked: true })
            Refresh();
    }

    void Variant_Changed(object? sender, RoutedEventArgs e)
    {
        if (sender is not RadioButton { IsChecked: true } button)
            return;
        var variant = Variant.All.FirstOrDefault(v => SegmentOf(v) == button);
        if (variant is not null && variant.Slot == SelectedSlot && chosen[variant.Slot] != variant)
        {
            chosen[variant.Slot] = variant;
            Refresh();
        }
    }

    void RefreshStatuses()
    {
        foreach (var (slot, label) in new[] { (Slot.Windows, WindowsStatus), (Slot.MacOS, MacStatus), (Slot.Linux, LinuxStatus) })
        {
            var current = installer.Find(slot).FirstOrDefault(i => !i.IsArchive);
            if (current is null)
            {
                label.Text = "Not installed";
                label.Foreground = new SolidColorBrush(Color.Parse("#98A2AA"));
            }
            else if (release is not null && current.Version == release.Tag)
            {
                label.Text = $"✓ {current.Version} · up to date";
                label.Foreground = new SolidColorBrush(Color.Parse("#4CC38A"));
            }
            else
            {
                label.Text = release is null ? $"Installed {current.Version ?? ""}".TrimEnd() : $"↑ {current.Version ?? "Older version"} · update ready";
                label.Foreground = new SolidColorBrush(Color.Parse(release is null ? "#C9D0D6" : "#F0A060"));
            }
        }
    }

    void Refresh()
    {
        if (busy is not null)
            return;
        RefreshStatuses();

        var slot = SelectedSlot;
        var variant = SelectedVariant;
        foreach (var v in Variant.All)
        {
            var segment = SegmentOf(v);
            segment.IsVisible = v.Slot == slot;
            if (v == variant && segment.IsChecked != true)
                segment.IsChecked = true;
        }
        var native = Variant.CurrentSlot == slot;
        VariantNote.Text = slot switch
        {
            Slot.MacOS when native => variant == Variant.DefaultFor(Slot.MacOS) ? "Matches this Mac" : "Does not match this Mac's chip",
            Slot.MacOS => "M1 and newer, or older Intel Macs",
            Slot.Linux => variant == Variant.LinuxAppImage ? "One file, recommended" : "Plain folder, start ./FpvDvrCleaner.Desktop",
            _ => "Unpacked into its own folder",
        };

        var items = installer.Find(slot);
        var current = items.FirstOrDefault(i => !i.IsArchive);
        var leftovers = items.Count(i => i.IsArchive) + Math.Max(0, items.Count(i => !i.IsArchive) - 1);
        var asset = release?.AssetFor(variant);
        var size = asset is null || asset.Size == 0 ? "" : $"{asset.Size / 1048576.0:0} MB download. ";
        var cleanup = leftovers > 0 ? $" {leftovers} older download{(leftovers == 1 ? "" : "s")} will be removed as well." : "";
        var elsewhere = native ? "" : $" This copy is for a {slot.ToDisplay()} computer; it cannot run here.";

        PrimaryButton.IsEnabled = true;
        if (folderProblem is not null)
        {
            ActionTitle.Text = "Choose a folder you can write to";
            ActionDetail.Text = folderProblem;
            PrimaryButton.Content = "Choose folder…";
        }
        else if (release is null)
        {
            if (releaseError is null)
            {
                ActionTitle.Text = "Looking for the newest release…";
                ActionDetail.Text = "Asking GitHub which version is current.";
                PrimaryButton.Content = "Please wait";
                PrimaryButton.IsEnabled = false;
            }
            else if (current is not null && native)
            {
                ActionTitle.Text = $"{current.Version ?? "FPV DVR Cleaner"} is installed";
                ActionDetail.Text = releaseError + " You can still open the installed version.";
                PrimaryButton.Content = "Open";
            }
            else
            {
                ActionTitle.Text = "No connection to GitHub";
                ActionDetail.Text = releaseError;
                PrimaryButton.Content = "Try again";
            }
        }
        else if (asset is null)
        {
            ActionTitle.Text = $"{release.Tag} has no {variant.Label} package";
            ActionDetail.Text = "A new release may still be uploading. Try again in a few minutes, or pick another package.";
            PrimaryButton.Content = "Check again";
        }
        else if (current is not null && current.Version == release.Tag && current.Name == variant.TargetName(asset))
        {
            ActionTitle.Text = $"Up to date · {release.Tag}";
            ActionDetail.Text = (native ? "The newest version is installed and ready." : $"The newest {slot.ToDisplay()} version is in the folder.") + cleanup;
            PrimaryButton.Content = native ? "Open FPV DVR Cleaner" : "Download again";
        }
        else if (current is not null)
        {
            ActionTitle.Text = $"Update available · {current.Version ?? "older version"} → {release.Tag}";
            ActionDetail.Text = $"{size}The old version “{current.Name}” is deleted once the new one is in place.{cleanup}{elsewhere}";
            PrimaryButton.Content = native ? "Update and open" : "Update";
        }
        else
        {
            ActionTitle.Text = $"FPV DVR Cleaner {release.Tag} for {slot.ToDisplay()}";
            ActionDetail.Text = $"{size}Unpacked into the folder above. Nothing else to install.{cleanup}{elsewhere}";
            PrimaryButton.Content = native ? "Install and open" : "Download";
        }
    }

    async void Primary_Click(object? sender, RoutedEventArgs e)
    {
        if (busy is not null)
        {
            busy.Cancel();
            PrimaryButton.IsEnabled = false;
            return;
        }
        if (folderProblem is not null)
        {
            await ChooseFolderAsync();
            return;
        }

        var slot = SelectedSlot;
        var native = Variant.CurrentSlot == slot;
        var current = installer.Find(slot).FirstOrDefault(i => !i.IsArchive);

        if (release is null)
        {
            if (current is not null && native && releaseError is not null)
                LaunchAndClose(current);
            else
                await LoadReleaseAsync();
            return;
        }

        var variant = SelectedVariant;
        var asset = release.AssetFor(variant);
        if (asset is null)
        {
            await LoadReleaseAsync();
            return;
        }
        if (current is not null && native && current.Version == release.Tag && current.Name == variant.TargetName(asset))
        {
            LaunchAndClose(current);
            return;
        }

        await InstallAsync(variant, native);
    }

    async Task InstallAsync(Variant variant, bool launch)
    {
        busy = new CancellationTokenSource();
        SetControlsEnabled(false);
        PrimaryButton.Content = "Cancel";
        PrimaryButton.Classes.Remove("primary");
        ProgressPanel.IsVisible = true;
        Progress.IsIndeterminate = true;
        Progress.Value = 0;
        ProgressStage.Text = "Connecting";
        ProgressNumbers.Text = "";
        var hadOld = installer.Find(variant.Slot).Any(i => !i.IsArchive);
        ActionTitle.Text = $"Getting {release!.Tag} for {variant.Slot.ToDisplay()}…";
        ActionDetail.Text = "The old version stays until the new one is complete.";

        var progress = new Progress<DownloadProgress>(p =>
        {
            if (busy is null)
                return;
            ProgressStage.Text = p.Stage;
            if (p.Stage == "Replacing the old version")
            {
                // Past the point of no return: the swap finishes by itself in a moment.
                PrimaryButton.IsEnabled = false;
            }
            if (p.Stage == "Downloading" && p.Total > 0)
            {
                Progress.IsIndeterminate = false;
                Progress.Value = (double)p.Done / p.Total;
                var eta = p.BytesPerSecond > 0 ? TimeSpan.FromSeconds((p.Total - p.Done) / p.BytesPerSecond) : (TimeSpan?)null;
                ProgressNumbers.Text = FormattableString.Invariant($"{p.Done / 1048576.0:0} / {p.Total / 1048576.0:0} MB  ·  {p.BytesPerSecond / 1048576.0:0.0} MB/s")
                                       + (eta is { } t ? $"  ·  {(t.TotalMinutes >= 1 ? $"{(int)t.TotalMinutes} min {t.Seconds:00} s" : $"{t.Seconds} s")} left" : "");
            }
            else
            {
                Progress.IsIndeterminate = true;
                ProgressNumbers.Text = "";
            }
        });

        InstalledItem? installed = null;
        string? failure = null;
        try
        {
            installed = await installer.InstallAsync(release, variant, progress, busy.Token);
        }
        catch (OperationCanceledException)
        {
            failure = "Cancelled. Nothing was changed.";
        }
        catch (InstallException error)
        {
            installer.Log($"Install failed: {error}");
            failure = error.Message;
        }
        catch (Exception error)
        {
            installer.Log($"Install failed: {error}");
            failure = $"{error.Message} Nothing was changed; details are in the log.";
        }

        busy.Dispose();
        busy = null;
        ProgressPanel.IsVisible = false;
        PrimaryButton.Classes.Add("primary");
        SetControlsEnabled(true);
        if (closeWhenDone)
        {
            Close();
            return;
        }
        Refresh();

        if (failure is not null)
        {
            ActionTitle.Text = hadOld ? "Update not installed · your current version is unchanged" : "Not installed";
            ActionDetail.Text = failure;
        }
        else if (launch && installed is not null)
        {
            ActionTitle.Text = $"Installed {release.Tag} · starting…";
            LaunchAndClose(installed);
        }
        else
        {
            ActionTitle.Text = $"Done · {release.Tag} for {variant.Slot.ToDisplay()} is in the folder";
        }
    }

    void LaunchAndClose(InstalledItem item)
    {
        try
        {
            Installer.Launch(item);
            DispatcherTimer.RunOnce(Close, TimeSpan.FromMilliseconds(600));
        }
        catch (Exception error)
        {
            installer.Log($"Launch failed: {error}");
            ActionTitle.Text = "Could not start the app";
            ActionDetail.Text = error.Message;
        }
    }

    /// <summary>Closing during an install cancels it first, so nothing is left half done.</summary>
    void Window_Closing(object? sender, WindowClosingEventArgs e)
    {
        if (busy is null)
            return;
        e.Cancel = true;
        closeWhenDone = true;
        busy.Cancel();
        ActionTitle.Text = "Stopping safely…";
        ActionDetail.Text = "The launcher closes as soon as the folder is in a clean state.";
    }

    void SetControlsEnabled(bool enabled)
    {
        foreach (var control in new Control[] { WindowsCard, MacCard, LinuxCard, WindowsX64, MacArm, MacIntel, LinuxAppImage, LinuxFolder, ChangeFolderButton, ResetFolderButton })
            control.IsEnabled = enabled;
    }

    async Task ChooseFolderAsync()
    {
        IReadOnlyList<IStorageFolder> picked;
        try
        {
            var start = await StorageProvider.TryGetFolderFromPathAsync(installer.Folder);
            picked = await StorageProvider.OpenFolderPickerAsync(new FolderPickerOpenOptions
            {
                Title = "Where should FPV DVR Cleaner be installed?",
                AllowMultiple = false,
                SuggestedStartLocation = start,
            });
        }
        catch (Exception error)
        {
            installer.Log($"Folder picker failed: {error}");
            return;
        }
        if (picked.Count == 0 || picked[0].TryGetLocalPath() is not { } path)
            return;

        settings.InstallFolder = SameFolder(path, Installer.LauncherFolder()) ? null : path;
        var saved = settings.Save();
        UseFolder(path, saved ? null : "This folder is used now, but could not be remembered for next time.");
        Refresh();
    }

    async void ChangeFolder_Click(object? sender, RoutedEventArgs e) => await ChooseFolderAsync();

    void ResetFolder_Click(object? sender, RoutedEventArgs e)
    {
        settings.InstallFolder = null;
        var saved = settings.Save();
        UseFolder(Installer.LauncherFolder(), saved ? null : "Could not be remembered for next time.");
        Refresh();
    }

    async void Folder_Click(object? sender, RoutedEventArgs e) =>
        await Launcher.LaunchDirectoryInfoAsync(new DirectoryInfo(installer.Folder));

    async void Log_Click(object? sender, RoutedEventArgs e)
    {
        var log = new FileInfo(Path.Combine(installer.Folder, Installer.LogName));
        if (log.Exists)
            await Launcher.LaunchFileInfoAsync(log);
        else
        {
            ActionDetail.Text = "Nothing has been logged yet; the log only records problems and installs.";
        }
    }

    async void Notes_Click(object? sender, RoutedEventArgs e) =>
        await Launcher.LaunchUriAsync(new Uri(release?.PageUrl ?? GitHub.ProjectUrl + "/releases/latest"));

    async void Project_Click(object? sender, RoutedEventArgs e) =>
        await Launcher.LaunchUriAsync(new Uri(GitHub.ProjectUrl));
}

static class SlotText
{
    public static string ToDisplay(this Slot slot) => slot switch
    {
        Slot.MacOS => "macOS",
        Slot.Linux => "Linux",
        _ => "Windows",
    };
}
