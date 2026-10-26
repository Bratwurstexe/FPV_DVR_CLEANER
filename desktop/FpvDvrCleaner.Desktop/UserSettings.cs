using System.Text.Json;
using System.Text.Json.Serialization;

namespace FpvDvrCleaner.Desktop;

/// <summary>Repair settings that can be saved as a preset and restored between runs.</summary>
public sealed class RepairPreset
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("signal")] public string Signal { get; set; } = "analog";
    [JsonPropertyName("colour")] public int Colour { get; set; } = 60;
    [JsonPropertyName("noise")] public int Noise { get; set; } = 50;
    [JsonPropertyName("block")] public int Block { get; set; } = 35;
    [JsonPropertyName("detail")] public int Detail { get; set; } = 15;
    [JsonPropertyName("motion")] public int Motion { get; set; }
    [JsonPropertyName("size")] public int Size { get; set; } = 100;
    [JsonPropertyName("osd")] public bool Osd { get; set; }
    [JsonPropertyName("effort")] public string Effort { get; set; } = "balanced";
    [JsonPropertyName("quality")] public int Quality { get; set; } = 18;
    [JsonPropertyName("sizeCapMb")] public int SizeCapMb { get; set; }
    // The finishing look, strengths 0..100; presets saved before it existed load with the look off.
    [JsonPropertyName("look")] public bool Look { get; set; }
    [JsonPropertyName("lookGrade")] public int LookGrade { get; set; }
    [JsonPropertyName("lookGlow")] public int LookGlow { get; set; }
    [JsonPropertyName("lookVignette")] public int LookVignette { get; set; }
    [JsonPropertyName("lookGrain")] public int LookGrain { get; set; }
    [JsonPropertyName("lookFringe")] public int LookFringe { get; set; }

    public RepairPreset Clone() => (RepairPreset)MemberwiseClone();

    /// <summary>Presets that ship with the app; they cannot be deleted.</summary>
    public static IReadOnlyList<RepairPreset> BuiltIn { get; } =
    [
        new() { Name = "Analog standard" },
        new() { Name = "Analog, clean and upscale to 1080p", Size = 150, Osd = true, Effort = "best" },
        new() { Name = "HDZero standard", Signal = "digital" },
        new() { Name = "Quick look (fast)", Noise = 0, Detail = 0, Effort = "fast", Quality = 22 },
    ];
}

/// <summary>The cut of one recording, so it comes back when the file is added again.</summary>
public sealed class ClipMemory
{
    [JsonPropertyName("size")] public long Size { get; set; }
    [JsonPropertyName("modified")] public long Modified { get; set; }
    [JsonPropertyName("segments")] public List<double[]> Segments { get; set; } = [];
    [JsonPropertyName("bookmarks")] public List<double> Bookmarks { get; set; } = [];
    [JsonPropertyName("seen")] public long Seen { get; set; }
}

/// <summary>
/// Choices the user should not have to repeat, kept in the per-user application
/// data folder (next to the trained models). Missing or damaged files are ignored.
/// </summary>
public sealed class UserSettings
{
    [JsonPropertyName("outputFolder")] public string? OutputFolder { get; set; }
    [JsonPropertyName("trainFolder")] public string? TrainFolder { get; set; }
    [JsonPropertyName("outputSuffix")] public string OutputSuffix { get; set; } = "-cleaned";
    [JsonPropertyName("outputFormat")] public string OutputFormat { get; set; } = "mp4-h264";
    [JsonPropertyName("openFolderAfterExport")] public bool OpenFolderAfterExport { get; set; } = true;
    [JsonPropertyName("joinClips")] public bool JoinClips { get; set; }
    [JsonPropertyName("snap")] public bool Snap { get; set; } = true;
    [JsonPropertyName("lastRepair")] public RepairPreset? LastRepair { get; set; }
    [JsonPropertyName("presets")] public List<RepairPreset> Presets { get; set; } = [];
    [JsonPropertyName("clips")] public Dictionary<string, ClipMemory> Clips { get; set; } = new();

    private const int RememberedClips = 300;
    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true };

    public static string Folder()
    {
        var folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "FPV DVR Cleaner");
        Directory.CreateDirectory(folder);
        return folder;
    }

    public static string FilePath() => Path.Combine(Folder(), "settings.json");

    public static UserSettings Load()
    {
        try
        {
            var path = FilePath();
            if (File.Exists(path))
                return JsonSerializer.Deserialize<UserSettings>(File.ReadAllText(path), Options) ?? new UserSettings();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException)
        {
            // A broken settings file must never stop the app from starting.
        }
        return new UserSettings();
    }

    public void Save()
    {
        try
        {
            // Forget the cuts of recordings that have not been opened for a long time.
            if (Clips.Count > RememberedClips)
                foreach (var key in Clips.OrderBy(pair => pair.Value.Seen).Take(Clips.Count - RememberedClips).Select(pair => pair.Key).ToList())
                    Clips.Remove(key);
            var path = FilePath();
            var draft = path + ".tmp";
            File.WriteAllText(draft, JsonSerializer.Serialize(this, Options));
            File.Move(draft, path, overwrite: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // Settings are a convenience; losing them is not worth an error dialog.
        }
    }

    public static string ClipKey(string path) =>
        OperatingSystem.IsWindows() ? Path.GetFullPath(path).ToLowerInvariant() : Path.GetFullPath(path);

    /// <summary>The remembered cut of a file, if the file is still the same one.</summary>
    public ClipMemory? RememberedCut(string path)
    {
        if (!Clips.TryGetValue(ClipKey(path), out var memory)) return null;
        try
        {
            var info = new FileInfo(path);
            if (info.Length != memory.Size || info.LastWriteTimeUtc.Ticks != memory.Modified) return null;
        }
        catch (IOException) { return null; }
        memory.Seen = DateTime.UtcNow.Ticks;
        return memory;
    }

    public void RememberCut(string path, IEnumerable<(double In, double Out)> segments, IEnumerable<double> bookmarks)
    {
        try
        {
            var info = new FileInfo(path);
            Clips[ClipKey(path)] = new ClipMemory
            {
                Size = info.Length, Modified = info.LastWriteTimeUtc.Ticks, Seen = DateTime.UtcNow.Ticks,
                Segments = segments.Select(s => new[] { s.In, s.Out }).ToList(),
                Bookmarks = bookmarks.ToList(),
            };
        }
        catch (IOException) { /* The file went away; nothing to remember. */ }
    }
}
