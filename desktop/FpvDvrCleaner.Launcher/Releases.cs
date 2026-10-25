using System.Net.Http.Headers;
using System.Runtime.InteropServices;
using System.Text.Json;

namespace FpvDvrCleaner.Launcher;

/// <summary>The three systems; each has one install slot next to the launcher.</summary>
public enum Slot { Windows, MacOS, Linux }

public enum PackageKind { Zip, TarGz, AppImage }

/// <summary>One release package and where it goes in the launcher's folder.</summary>
public sealed record Variant(string Id, Slot Slot, string Label, string AssetSuffix, PackageKind Kind, string InstallName)
{
    public static readonly Variant Windows = new("windows-x64", Slot.Windows, "64-bit", "-windows-x64.zip", PackageKind.Zip, "FPV DVR Cleaner (Windows)");
    public static readonly Variant MacArm = new("macos-apple-silicon", Slot.MacOS, "Apple Silicon", "-macos-apple-silicon.zip", PackageKind.Zip, "FPV DVR Cleaner.app");
    public static readonly Variant MacIntel = new("macos-intel", Slot.MacOS, "Intel", "-macos-intel.zip", PackageKind.Zip, "FPV DVR Cleaner.app");
    public static readonly Variant LinuxAppImage = new("linux-appimage", Slot.Linux, "AppImage", "-linux-x64.AppImage", PackageKind.AppImage, "");
    public static readonly Variant LinuxFolder = new("linux-folder", Slot.Linux, "Folder", "-linux-x64.tar.gz", PackageKind.TarGz, "FPV DVR Cleaner (Linux)");

    public static readonly Variant[] All = [Windows, MacArm, MacIntel, LinuxAppImage, LinuxFolder];

    /// <summary>The AppImage keeps its release name, the others a fixed folder or bundle name.</summary>
    public string TargetName(ReleaseAsset asset) => Kind == PackageKind.AppImage ? asset.Name : InstallName;

    public static Slot? CurrentSlot =>
        OperatingSystem.IsWindows() ? Slot.Windows :
        OperatingSystem.IsMacOS() ? Slot.MacOS :
        OperatingSystem.IsLinux() ? Slot.Linux : null;

    public static Variant DefaultFor(Slot slot) => slot switch
    {
        Slot.Windows => Windows,
        Slot.MacOS => RuntimeInformation.OSArchitecture == Architecture.Arm64 || !OperatingSystem.IsMacOS() ? MacArm : MacIntel,
        _ => LinuxAppImage,
    };
}

public sealed record ReleaseAsset(string Name, long Size, string Url, string? Sha256);

public sealed record Release(string Tag, DateTimeOffset Published, string PageUrl, IReadOnlyList<ReleaseAsset> Assets)
{
    public ReleaseAsset? AssetFor(Variant variant) =>
        Assets.FirstOrDefault(a => a.Name.StartsWith("FPV-DVR-Cleaner-v", StringComparison.Ordinal)
                                   && a.Name.EndsWith(variant.AssetSuffix, StringComparison.Ordinal));

    public ReleaseAsset? Checksums => Assets.FirstOrDefault(a => a.Name == "SHA256SUMS.txt");
}

public static class GitHub
{
    public const string Repository = "Bratwurstexe/FPV_DVR_CLEANER";
    public const string ProjectUrl = "https://github.com/" + Repository;

    public static readonly HttpClient Http = CreateClient();

    static HttpClient CreateClient()
    {
        // GitHub's download servers drop idle connections; reusing one of those fails with
        // "connection closed" or an SSL error, so pooled connections are retired early.
        var handler = new SocketsHttpHandler
        {
            PooledConnectionIdleTimeout = TimeSpan.FromSeconds(5),
            PooledConnectionLifetime = TimeSpan.FromMinutes(2),
            ConnectTimeout = TimeSpan.FromSeconds(20),
        };
        var http = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };
        http.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("FpvDvrCleaner-Launcher", "1.0"));
        return http;
    }

    /// <summary>The newest release; when the API is unavailable (rate limit, outage), from the release page redirect.</summary>
    public static async Task<Release> LatestAsync(CancellationToken cancel)
    {
        try
        {
            return await LatestFromApiAsync(cancel);
        }
        catch (Exception error) when (!cancel.IsCancellationRequested)
        {
            try
            {
                return await LatestFromRedirectAsync(cancel);
            }
            catch (Exception) when (!cancel.IsCancellationRequested)
            {
                throw error;
            }
        }
    }

    static readonly HttpClient NoRedirect = CreateNoRedirectClient();

    static HttpClient CreateNoRedirectClient()
    {
        var http = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = TimeSpan.FromSeconds(20) };
        http.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("FpvDvrCleaner-Launcher", "1.0"));
        return http;
    }

    /// <summary>
    /// github.com/…/releases/latest redirects to …/releases/tag/vX.Y.Z. The asset names follow
    /// from the tag; sizes are unknown, so the checksum list is what the download is checked against.
    /// </summary>
    static async Task<Release> LatestFromRedirectAsync(CancellationToken cancel)
    {
        using var response = await NoRedirect.GetAsync($"{ProjectUrl}/releases/latest", cancel);
        var location = response.Headers.Location?.ToString() ?? throw new InvalidOperationException("No release found.");
        var marker = location.LastIndexOf("/releases/tag/", StringComparison.Ordinal);
        if (marker < 0)
            throw new InvalidOperationException("No release found.");
        var tag = Uri.UnescapeDataString(location[(marker + "/releases/tag/".Length)..]);
        var download = $"{ProjectUrl}/releases/download/{Uri.EscapeDataString(tag)}/";
        var assets = Variant.All.Select(v => $"FPV-DVR-Cleaner-{tag}{v.AssetSuffix}")
                                .Append("SHA256SUMS.txt")
                                .Select(name => new ReleaseAsset(name, 0, download + name, null))
                                .ToList();
        return new Release(tag, DateTimeOffset.MinValue, $"{ProjectUrl}/releases/tag/{Uri.EscapeDataString(tag)}", assets);
    }

    static async Task<Release> LatestFromApiAsync(CancellationToken cancel)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, $"https://api.github.com/repos/{Repository}/releases/latest");
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancel);
        timeout.CancelAfter(TimeSpan.FromSeconds(20));
        using var response = await Http.SendAsync(request, timeout.Token);
        if ((int)response.StatusCode == 403 || (int)response.StatusCode == 429)
            throw new InvalidOperationException("GitHub is limiting requests from this network right now. Try again in a few minutes.");
        response.EnsureSuccessStatusCode();

        using var json = JsonDocument.Parse(await response.Content.ReadAsStreamAsync(timeout.Token));
        var root = json.RootElement;
        var assets = new List<ReleaseAsset>();
        foreach (var a in root.GetProperty("assets").EnumerateArray())
        {
            // GitHub lists a "sha256:…" digest for assets uploaded since mid 2025.
            string? digest = a.TryGetProperty("digest", out var d) && d.ValueKind == JsonValueKind.String ? d.GetString() : null;
            assets.Add(new ReleaseAsset(
                a.GetProperty("name").GetString()!,
                a.GetProperty("size").GetInt64(),
                a.GetProperty("browser_download_url").GetString()!,
                digest is not null && digest.StartsWith("sha256:", StringComparison.Ordinal) ? digest[7..].ToLowerInvariant() : null));
        }
        return new Release(
            root.GetProperty("tag_name").GetString()!,
            root.TryGetProperty("published_at", out var p) && p.ValueKind == JsonValueKind.String ? p.GetDateTimeOffset() : DateTimeOffset.MinValue,
            root.GetProperty("html_url").GetString()!,
            assets);
    }
}
