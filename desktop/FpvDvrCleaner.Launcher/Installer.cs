using System.Diagnostics;
using System.Formats.Tar;
using System.IO.Compression;
using System.Net;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace FpvDvrCleaner.Launcher;

/// <summary>Something of the app already in the launcher's folder.</summary>
public sealed record InstalledItem(string Path, Slot Slot, string? Version, bool IsArchive)
{
    public string Name => System.IO.Path.GetFileName(Path);
}

public sealed record DownloadProgress(long Done, long Total, double BytesPerSecond, string Stage);

/// <summary>A failure with a message meant for the person using the launcher.</summary>
public sealed class InstallException(string message, Exception? inner = null) : Exception(message, inner);

/// <summary>
/// Installs releases into the folder the launcher sits in. Each system has one slot:
/// installing replaces whatever of that system was there, including folders unpacked
/// by hand from an earlier release and leftover downloaded archives.
///
/// The old version is never touched until the new one is downloaded, verified and
/// unpacked. It is then parked under its own name in a side folder, the new one is
/// moved in, and only then is the parked copy deleted. If the launcher dies in between,
/// the next start puts the parked copy back.
/// </summary>
public sealed partial class Installer(string folder)
{
    const string ManifestName = ".fpv-dvr-cleaner-launcher.json";
    const string WorkName = ".fpv-dvr-cleaner-launcher-work";
    const string ParkingName = ".fpv-dvr-cleaner-launcher-old";
    const string LockName = ".fpv-dvr-cleaner-launcher.lock";
    public const string LogName = ".fpv-dvr-cleaner-launcher.log";

    static readonly TimeSpan StallTimeout = TimeSpan.FromSeconds(45);
    const int DownloadAttempts = 5;

    public string Folder { get; } = folder;

    /// <summary>The folder next to the launcher; for a launcher inside a .app bundle, the bundle's folder.</summary>
    public static string LauncherFolder()
    {
        var dir = System.IO.Path.GetDirectoryName(Environment.ProcessPath) ?? AppContext.BaseDirectory;
        var bundle = dir.IndexOf(".app/Contents/MacOS", StringComparison.Ordinal);
        if (bundle >= 0)
            dir = System.IO.Path.GetDirectoryName(dir[..(bundle + 4)])!;
        return dir;
    }

    public void Log(string message)
    {
        try
        {
            var file = System.IO.Path.Combine(Folder, LogName);
            if (File.Exists(file) && new FileInfo(file).Length > 256 * 1024)
                File.Delete(file);
            File.AppendAllText(file, $"{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss} {message}{Environment.NewLine}");
        }
        catch (Exception) { }
    }

    /// <summary>Null when the folder can be written to, otherwise why not.</summary>
    public string? CheckWritable()
    {
        try
        {
            var probe = System.IO.Path.Combine(Folder, $".fpv-dvr-cleaner-launcher-probe-{Environment.ProcessId}");
            File.WriteAllText(probe, "");
            File.Delete(probe);
            return null;
        }
        catch (Exception error)
        {
            Log($"Folder not writable: {error}");
            return "You cannot write to this folder. Choose another one, for example in Documents or on the Desktop.";
        }
    }

    /// <summary>Finishes or undoes an install that was interrupted (crash, power loss, killed process).</summary>
    public void RecoverInterrupted()
    {
        var parking = System.IO.Path.Combine(Folder, ParkingName);
        if (Directory.Exists(parking))
        {
            foreach (var entry in Directory.EnumerateFileSystemEntries(parking))
            {
                var home = System.IO.Path.Combine(Folder, System.IO.Path.GetFileName(entry));
                try
                {
                    if (Directory.Exists(home) || File.Exists(home))
                    {
                        // The new version made it in: the parked copy is the old one.
                        Log($"Recovery: removing the parked old copy of {System.IO.Path.GetFileName(entry)}");
                        TryDelete(entry);
                    }
                    else
                    {
                        Log($"Recovery: putting {System.IO.Path.GetFileName(entry)} back");
                        Move(entry, home);
                    }
                }
                catch (Exception error) { Log($"Recovery of {entry} failed: {error}"); }
            }
            if (!Directory.EnumerateFileSystemEntries(parking).Any())
                TryDelete(parking);
        }
        TryDelete(System.IO.Path.Combine(Folder, WorkName));
    }

    [GeneratedRegex(@"^FPV-DVR-Cleaner-(v[^-]+(?:-[a-z0-9.]+)*?)-linux-x64\.AppImage$")]
    private static partial Regex AppImageName();

    [GeneratedRegex(@"^FPV-DVR-Cleaner-(v[^-]+(?:-[a-z0-9.]+)*?)-(windows-x64\.zip|macos-apple-silicon\.zip|macos-intel\.zip|linux-x64\.tar\.gz)$")]
    private static partial Regex ArchiveName();

    /// <summary>Everything of one system found in the folder, the current install first.</summary>
    public List<InstalledItem> Find(Slot slot)
    {
        try { return FindUnsafe(slot); }
        catch (Exception error)
        {
            Log($"Scanning the folder failed: {error}");
            return [];
        }
    }

    List<InstalledItem> FindUnsafe(Slot slot)
    {
        var versions = ReadManifest();
        string? Known(string path) => versions.TryGetValue(System.IO.Path.GetFileName(path), out var v) ? v : null;
        var items = new List<InstalledItem>();
        if (!Directory.Exists(Folder))
            return items;

        foreach (var dir in Directory.EnumerateDirectories(Folder))
        {
            var name = System.IO.Path.GetFileName(dir);
            Slot? found = null;
            string? version = Known(dir);
            if (name is "FPV DVR Cleaner.app")
            {
                found = Slot.MacOS;
                version ??= BundleVersion(dir);
            }
            else if (name is "FPV DVR Cleaner (Windows)" or "FPV DVR Cleaner (Linux)" or "FPV-DVR-Cleaner")
            {
                if (File.Exists(System.IO.Path.Combine(dir, "FpvDvrCleaner.Desktop.exe")))
                {
                    found = Slot.Windows;
                    version ??= AssemblyVersion(System.IO.Path.Combine(dir, "FpvDvrCleaner.Desktop.dll"));
                }
                else if (File.Exists(System.IO.Path.Combine(dir, "FpvDvrCleaner.Desktop")))
                    found = Slot.Linux;
            }
            if (found == slot)
                items.Add(new InstalledItem(dir, slot, version, false));
        }

        foreach (var file in Directory.EnumerateFiles(Folder))
        {
            var name = System.IO.Path.GetFileName(file);
            var appImage = AppImageName().Match(name);
            if (appImage.Success && slot == Slot.Linux)
            {
                items.Add(new InstalledItem(file, slot, Known(file) ?? appImage.Groups[1].Value, false));
                continue;
            }
            var archive = ArchiveName().Match(name);
            if (archive.Success && SlotOfSuffix(archive.Groups[2].Value) == slot)
                items.Add(new InstalledItem(file, slot, archive.Groups[1].Value, true));
        }

        // The install the launcher made itself comes first, then the newest by date.
        return items.OrderBy(i => i.IsArchive)
                    .ThenByDescending(i => versions.ContainsKey(i.Name))
                    .ThenByDescending(i => Directory.Exists(i.Path) ? Directory.GetLastWriteTimeUtc(i.Path) : File.GetLastWriteTimeUtc(i.Path))
                    .ToList();
    }

    static Slot SlotOfSuffix(string suffix) =>
        suffix.StartsWith("windows", StringComparison.Ordinal) ? Slot.Windows :
        suffix.StartsWith("macos", StringComparison.Ordinal) ? Slot.MacOS : Slot.Linux;

    static string? AssemblyVersion(string dll)
    {
        try
        {
            var info = FileVersionInfo.GetVersionInfo(dll).ProductVersion;
            return string.IsNullOrEmpty(info) ? null : "v" + info.Split('+')[0];
        }
        catch (Exception) { return null; }
    }

    static string? BundleVersion(string bundle)
    {
        try
        {
            var plist = File.ReadAllText(System.IO.Path.Combine(bundle, "Contents", "Info.plist"));
            var m = Regex.Match(plist, @"<key>CFBundleShortVersionString</key>\s*<string>([^<]+)</string>");
            return m.Success ? "v" + m.Groups[1].Value : null;
        }
        catch (Exception) { return null; }
    }

    /// <summary>Downloads, checks and unpacks a release, then swaps it in for the old one.</summary>
    public async Task<InstalledItem> InstallAsync(Release release, Variant variant, IProgress<DownloadProgress> progress, CancellationToken cancel)
    {
        var asset = release.AssetFor(variant) ?? throw new InstallException($"Release {release.Tag} has no package for {variant.Slot} ({variant.Label}).");
        Log($"Install {release.Tag} {variant.Id} from {asset.Url}");

        using var guard = AcquireLock();
        RecoverInterrupted();
        var target = System.IO.Path.Combine(Folder, variant.TargetName(asset));
        var old = Find(variant.Slot).Select(i => i.Path).ToList();
        if ((Directory.Exists(target) || File.Exists(target)) && !old.Contains(target))
            old.Add(target);
        ProtectLauncher(old.Append(target));
        // On Windows a running app locks its files; say so before a long download, not after.
        if (OperatingSystem.IsWindows() && RunningFrom(old) is { } running)
            throw new InstallException($"FPV DVR Cleaner is running from {running}. Close it, then try again.");
        CheckFreeSpace(asset, variant);

        var work = System.IO.Path.Combine(Folder, WorkName);
        Directory.CreateDirectory(work);
        try
        {
            var download = System.IO.Path.Combine(work, asset.Name);
            var size = await DownloadAsync(asset, download, progress, cancel);

            progress.Report(new DownloadProgress(size, size, 0, "Checking the download"));
            if (asset.Size > 0 && size != asset.Size)
                throw new InstallException($"The download is incomplete ({size:N0} of {asset.Size:N0} bytes). Please try again.");
            var expected = asset.Sha256 ?? await ChecksumFromListAsync(release, asset, cancel);
            if (expected is null && asset.Size == 0)
                throw new InstallException("This release has no checksum for the package, so the download cannot be verified.");
            if (expected is null)
                Log("No checksum published for this asset; size check only.");
            else
            {
                var actual = await Task.Run(() => Hash(download, cancel), cancel);
                if (!string.Equals(actual, expected, StringComparison.OrdinalIgnoreCase))
                {
                    Log($"Checksum mismatch: expected {expected}, got {actual}");
                    throw new InstallException("The download is damaged (checksum mismatch). Please try again.");
                }
            }

            progress.Report(new DownloadProgress(size, size, 0, "Unpacking"));
            var payload = await Task.Run(() => Unpack(variant, download, System.IO.Path.Combine(work, "unpacked"), cancel), cancel);
            cancel.ThrowIfCancellationRequested();

            // From here on the swap runs to its end; cancelling would only leave a mess.
            progress.Report(new DownloadProgress(size, size, 0, "Replacing the old version"));
            if (!OperatingSystem.IsWindows() && variant.Kind == PackageKind.AppImage)
                File.SetUnixFileMode(payload, File.GetUnixFileMode(payload) | UnixFileMode.UserExecute | UnixFileMode.GroupExecute | UnixFileMode.OtherExecute);
            Swap(old, target, payload);

            WriteManifest(variant.Slot, System.IO.Path.GetFileName(target), release.Tag);
            Log($"Installed {release.Tag} as {target}; removed {string.Join(", ", old.Select(System.IO.Path.GetFileName))}");
            return new InstalledItem(target, variant.Slot, release.Tag, false);
        }
        finally
        {
            TryDelete(work);
        }
    }

    sealed class Lock(FileStream stream) : IDisposable
    {
        public void Dispose() => stream.Dispose();
    }

    IDisposable AcquireLock()
    {
        try
        {
            return new Lock(new FileStream(System.IO.Path.Combine(Folder, LockName), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None, 1, FileOptions.DeleteOnClose));
        }
        catch (IOException)
        {
            throw new InstallException("Another launcher is already installing into this folder. Wait for it to finish.");
        }
    }

    /// <summary>Refuses to remove a folder the launcher itself runs from.</summary>
    static void ProtectLauncher(IEnumerable<string> paths)
    {
        var self = Environment.ProcessPath;
        if (self is null)
            return;
        var comparison = OperatingSystem.IsLinux() ? StringComparison.Ordinal : StringComparison.OrdinalIgnoreCase;
        foreach (var path in paths)
        {
            var full = System.IO.Path.GetFullPath(path).TrimEnd(System.IO.Path.DirectorySeparatorChar) + System.IO.Path.DirectorySeparatorChar;
            if (self.StartsWith(full, comparison) || string.Equals(self, path, comparison))
                throw new InstallException("The launcher is inside the app folder it would replace. Move the launcher one folder up and start it from there.");
        }
    }

    /// <summary>The name of the installed item an app process runs from, if any.</summary>
    static string? RunningFrom(IEnumerable<string> items)
    {
        var folders = items.Select(i => System.IO.Path.GetFullPath(i).TrimEnd(System.IO.Path.DirectorySeparatorChar) + System.IO.Path.DirectorySeparatorChar).ToList();
        foreach (var process in Process.GetProcessesByName("FpvDvrCleaner.Desktop").Concat(Process.GetProcessesByName("flugbild-engine")))
        {
            using (process)
            {
                string? path;
                try { path = process.MainModule?.FileName; }
                catch (Exception) { continue; }
                if (path is not null && folders.FirstOrDefault(f => path.StartsWith(f, StringComparison.OrdinalIgnoreCase)) is { } hit)
                    return System.IO.Path.GetFileName(hit.TrimEnd(System.IO.Path.DirectorySeparatorChar));
            }
        }
        return null;
    }

    void CheckFreeSpace(ReleaseAsset asset, Variant variant)
    {
        try
        {
            var free = new DriveInfo(System.IO.Path.GetPathRoot(System.IO.Path.GetFullPath(Folder))!).AvailableFreeSpace;
            // Archive plus unpacked copy (about 2.5x the archive) plus a margin.
            var size = asset.Size > 0 ? asset.Size : 300L * 1024 * 1024;
            var needed = (variant.Kind == PackageKind.AppImage ? size : size * 7 / 2) + 100L * 1024 * 1024;
            if (free < needed)
                throw new InstallException($"Not enough free disk space: {needed / 1048576.0:N0} MB needed, {free / 1048576.0:N0} MB free.");
        }
        catch (InstallException) { throw; }
        catch (Exception error) { Log($"Free space check skipped: {error.Message}"); }
    }

    /// <summary>Downloads with retries; a broken connection resumes where it stopped.</summary>
    async Task<long> DownloadAsync(ReleaseAsset asset, string path, IProgress<DownloadProgress> progress, CancellationToken cancel)
    {
        Exception? last = null;
        for (var attempt = 1; attempt <= DownloadAttempts; attempt++)
        {
            cancel.ThrowIfCancellationRequested();
            try
            {
                return await DownloadOnceAsync(asset, path, progress, cancel);
            }
            catch (OperationCanceledException) when (cancel.IsCancellationRequested) { throw; }
            catch (Exception error) when (error is HttpRequestException or IOException or TimeoutException or OperationCanceledException)
            {
                if (error is HttpRequestException { StatusCode: HttpStatusCode.NotFound or HttpStatusCode.Forbidden or HttpStatusCode.Unauthorized } refused)
                    throw new InstallException($"GitHub refused the download ({(int)refused.StatusCode!}). The release may have just changed; restart the launcher.", error);
                last = error;
                Log($"Download attempt {attempt} failed: {error.GetType().Name}: {error.Message}");
                if (attempt == DownloadAttempts)
                    break;
                var wait = TimeSpan.FromSeconds(2 << (attempt - 1));
                progress.Report(new DownloadProgress(0, 0, 0, $"Connection lost, retrying in {wait.TotalSeconds:0} s (attempt {attempt + 1} of {DownloadAttempts})"));
                await Task.Delay(wait, cancel);
            }
        }
        throw new InstallException("The download kept failing. Check the internet connection and try again; it resumes where it stopped.", last);
    }

    async Task<long> DownloadOnceAsync(ReleaseAsset asset, string path, IProgress<DownloadProgress> progress, CancellationToken cancel)
    {
        long have = File.Exists(path) ? new FileInfo(path).Length : 0;
        using var request = new HttpRequestMessage(HttpMethod.Get, asset.Url);
        if (have > 0)
            request.Headers.Range = new System.Net.Http.Headers.RangeHeaderValue(have, null);

        using var headers = CancellationTokenSource.CreateLinkedTokenSource(cancel);
        headers.CancelAfter(StallTimeout);
        using var response = await GitHub.Http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, headers.Token);
        if (response.StatusCode == HttpStatusCode.RequestedRangeNotSatisfiable && asset.Size > 0 && have == asset.Size)
            return have;
        response.EnsureSuccessStatusCode();

        var resumed = response.StatusCode == HttpStatusCode.PartialContent;
        if (!resumed)
            have = 0;
        var total = have + (response.Content.Headers.ContentLength ?? Math.Max(0, asset.Size - have));

        await using var source = await response.Content.ReadAsStreamAsync(cancel);
        await using var file = new FileStream(path, resumed ? FileMode.Append : FileMode.Create, FileAccess.Write, FileShare.None, 1 << 16, true);

        var buffer = new byte[1 << 16];
        var clock = Stopwatch.StartNew();
        long done = have, lastBytes = have;
        var lastTick = TimeSpan.Zero;
        double speed = 0;
        while (true)
        {
            // A connection that stays open but sends nothing counts as broken.
            using var stall = CancellationTokenSource.CreateLinkedTokenSource(cancel);
            stall.CancelAfter(StallTimeout);
            int read;
            try { read = await source.ReadAsync(buffer, stall.Token); }
            catch (OperationCanceledException) when (!cancel.IsCancellationRequested)
            {
                throw new TimeoutException($"No data for {StallTimeout.TotalSeconds:0} s.");
            }
            if (read == 0)
                break;
            await file.WriteAsync(buffer.AsMemory(0, read), cancel);
            done += read;
            var now = clock.Elapsed;
            if ((now - lastTick).TotalMilliseconds >= 250)
            {
                var rate = (done - lastBytes) / (now - lastTick).TotalSeconds;
                speed = speed == 0 ? rate : speed * 0.7 + rate * 0.3;
                lastBytes = done;
                lastTick = now;
                progress.Report(new DownloadProgress(done, total, speed, "Downloading"));
            }
        }
        await file.FlushAsync(cancel);
        progress.Report(new DownloadProgress(done, total, speed, "Downloading"));
        if (total > 0 && done < total)
            throw new IOException($"The connection closed early ({done:N0} of {total:N0} bytes).");
        return done;
    }

    async Task<string?> ChecksumFromListAsync(Release release, ReleaseAsset asset, CancellationToken cancel)
    {
        if (release.Checksums is not { } list)
            return null;
        try
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancel);
            timeout.CancelAfter(TimeSpan.FromSeconds(30));
            var text = await GitHub.Http.GetStringAsync(list.Url, timeout.Token);
            foreach (var line in text.Split('\n'))
            {
                var parts = line.Trim().Split(' ', 2, StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length == 2 && parts[1].TrimStart('*').Trim() == asset.Name)
                    return parts[0];
            }
            return null;
        }
        catch (Exception error) when (!cancel.IsCancellationRequested)
        {
            // Without the list there is nothing to compare against, so the install stops.
            Log($"Checksum list could not be read: {error.Message}");
            throw new InstallException("The checksum list could not be loaded, so the download cannot be verified. Please try again.", error);
        }
    }

    static string Hash(string path, CancellationToken cancel)
    {
        using var stream = File.OpenRead(path);
        using var sha = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var buffer = new byte[1 << 20];
        int read;
        while ((read = stream.Read(buffer)) > 0)
        {
            cancel.ThrowIfCancellationRequested();
            sha.AppendData(buffer, 0, read);
        }
        return Convert.ToHexString(sha.GetHashAndReset()).ToLowerInvariant();
    }

    /// <summary>Unpacks the download and returns the one file or folder to install.</summary>
    static string Unpack(Variant variant, string download, string into, CancellationToken cancel)
    {
        if (variant.Kind == PackageKind.AppImage)
            return download;

        Directory.CreateDirectory(into);
        try
        {
            if (variant.Kind == PackageKind.TarGz)
            {
                using var gz = new GZipStream(File.OpenRead(download), CompressionMode.Decompress);
                TarFile.ExtractToDirectory(gz, into, overwriteFiles: true);
            }
            else if (OperatingSystem.IsMacOS())
            {
                // ditto keeps the bundle exactly as it was signed, links and all.
                using var ditto = Process.Start(new ProcessStartInfo("ditto", ["-x", "-k", download, into]) { UseShellExecute = false, RedirectStandardError = true })!;
                var stderr = ditto.StandardError.ReadToEnd();
                ditto.WaitForExit();
                if (ditto.ExitCode != 0)
                    throw new InstallException($"The package could not be unpacked: {stderr.Trim()}");
            }
            else
            {
                ZipFile.ExtractToDirectory(download, into, overwriteFiles: true);
            }
        }
        catch (InvalidDataException error)
        {
            throw new InstallException("The package is damaged and could not be unpacked. Please try again.", error);
        }
        cancel.ThrowIfCancellationRequested();

        var top = Directory.EnumerateFileSystemEntries(into).Where(e => !System.IO.Path.GetFileName(e).StartsWith("__MACOSX", StringComparison.Ordinal)).ToList();
        if (top.Count != 1 || !Directory.Exists(top[0]))
            throw new InstallException("The package does not have the expected layout.");
        var expected = variant.Slot switch
        {
            Slot.Windows => "FpvDvrCleaner.Desktop.exe",
            Slot.MacOS => System.IO.Path.Combine("Contents", "MacOS", "FpvDvrCleaner.Desktop"),
            _ => "FpvDvrCleaner.Desktop",
        };
        if (!File.Exists(System.IO.Path.Combine(top[0], expected)))
            throw new InstallException($"The package is missing {expected}.");
        return top[0];
    }

    /// <summary>
    /// Parks the old items under their own names first, so a running app (locked files on
    /// Windows) stops the update before anything is lost; then moves the new one in and
    /// deletes the parked copies. The parking folder lets <see cref="RecoverInterrupted"/>
    /// undo a swap that was cut off.
    /// </summary>
    void Swap(List<string> old, string target, string payload)
    {
        var parking = System.IO.Path.Combine(Folder, ParkingName);
        Directory.CreateDirectory(parking);
        var parked = new List<(string From, string To)>();
        try
        {
            foreach (var item in old)
            {
                var to = System.IO.Path.Combine(parking, System.IO.Path.GetFileName(item));
                MoveWithRetry(item, to);
                parked.Add((item, to));
            }
            MoveWithRetry(payload, target);
        }
        catch (Exception error)
        {
            Log($"Swap failed, restoring: {error}");
            foreach (var (from, to) in Enumerable.Reverse(parked))
            {
                try { MoveWithRetry(to, from); }
                catch (Exception undo) { Log($"Restoring {from} failed, it stays in {ParkingName}: {undo}"); }
            }
            if (!Directory.EnumerateFileSystemEntries(parking).Any())
                TryDelete(parking);
            throw new InstallException(error is IOException or UnauthorizedAccessException
                ? "The old version is still in use. Close FPV DVR Cleaner and try again. Nothing was changed."
                : error.Message, error);
        }
        foreach (var (_, to) in parked)
        {
            if (!TryDelete(to))
                Log($"Could not delete the old copy {to}; it is removed on the next start.");
        }
        TryDelete(parking);
    }

    /// <summary>Virus scanners briefly lock freshly written files; a few short retries ride that out.</summary>
    static void MoveWithRetry(string from, string to)
    {
        for (var attempt = 1; ; attempt++)
        {
            try
            {
                Move(from, to);
                return;
            }
            catch (Exception error) when (attempt < 6 && error is IOException or UnauthorizedAccessException)
            {
                Thread.Sleep(400 * attempt);
            }
        }
    }

    static void Move(string from, string to)
    {
        if (Directory.Exists(from))
            Directory.Move(from, to);
        else
            File.Move(from, to);
    }

    static bool TryDelete(string path)
    {
        for (var attempt = 1; attempt <= 3; attempt++)
        {
            try
            {
                if (Directory.Exists(path))
                {
                    // Read-only files (from zips made on other systems) block a recursive delete on Windows.
                    foreach (var file in Directory.EnumerateFiles(path, "*", SearchOption.AllDirectories))
                        File.SetAttributes(file, FileAttributes.Normal);
                    Directory.Delete(path, true);
                }
                else if (File.Exists(path))
                {
                    File.SetAttributes(path, FileAttributes.Normal);
                    File.Delete(path);
                }
                return true;
            }
            catch (Exception) when (attempt < 3)
            {
                Thread.Sleep(300 * attempt);
            }
            catch (Exception) { }
        }
        return false;
    }

    Dictionary<string, string> ReadManifest()
    {
        var result = new Dictionary<string, string>();
        try
        {
            var root = JsonNode.Parse(File.ReadAllText(System.IO.Path.Combine(Folder, ManifestName)))?["installs"]?.AsObject();
            foreach (var (_, entry) in root ?? [])
            {
                if (entry?["path"]?.GetValue<string>() is { } path && entry["version"]?.GetValue<string>() is { } version)
                    result[path] = version;
            }
        }
        catch (Exception) { }
        return result;
    }

    void WriteManifest(Slot slot, string path, string version)
    {
        var file = System.IO.Path.Combine(Folder, ManifestName);
        JsonObject root;
        try { root = JsonNode.Parse(File.ReadAllText(file))?.AsObject() ?? []; }
        catch (Exception) { root = []; }
        if (root["installs"] is not JsonObject installs)
            root["installs"] = installs = [];
        installs[slot.ToString()] = new JsonObject
        {
            ["path"] = path,
            ["version"] = version,
            ["installed"] = DateTimeOffset.Now.ToString("O"),
        };
        // Written next to it and moved over, so a crash never leaves half a file.
        try
        {
            File.WriteAllText(file + ".tmp", root.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
            File.Move(file + ".tmp", file, overwrite: true);
        }
        catch (Exception error) { Log($"Writing {ManifestName} failed: {error.Message}"); }
    }

    /// <summary>Starts an installed app; only for the system the launcher runs on.</summary>
    public static void Launch(InstalledItem item)
    {
        ProcessStartInfo start = item.Slot switch
        {
            Slot.Windows => new(System.IO.Path.Combine(item.Path, "FpvDvrCleaner.Desktop.exe")) { WorkingDirectory = item.Path },
            Slot.MacOS => new("open", ["-n", item.Path]),
            _ when File.Exists(item.Path) => new(item.Path) { WorkingDirectory = System.IO.Path.GetDirectoryName(item.Path)! },
            _ => new(System.IO.Path.Combine(item.Path, "FpvDvrCleaner.Desktop")) { WorkingDirectory = item.Path },
        };
        if (item.Slot != Slot.MacOS && !File.Exists(start.FileName))
            throw new InstallException($"{System.IO.Path.GetFileName(start.FileName)} is missing. Install the app again.");
        start.UseShellExecute = false;
        Process.Start(start);
    }
}
