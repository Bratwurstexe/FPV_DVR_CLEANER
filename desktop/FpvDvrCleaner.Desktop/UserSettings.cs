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

    // Built-in levels only: a stable id, the level (light, normal, strong) and the plain line shown for it.
    [JsonIgnore] public string Id { get; init; } = "";
    [JsonIgnore] public string Level { get; init; } = "";
    [JsonIgnore] public string Description { get; init; } = "";
    [JsonIgnore] public bool IsBuiltIn => Id.Length > 0;
    /// <summary>"Analog · Normal" for a built-in level, the name the user gave otherwise.</summary>
    [JsonIgnore] public string FullName => IsBuiltIn ? $"{UiText.SignalName(Signal)} · {Name}" : Name;

    public RepairPreset Clone() => (RepairPreset)MemberwiseClone();

    /// <summary>
    /// The settings with this preset applied. A built-in level sets the repairs only
    /// and leaves size, speed, the OSD choice, the look and the export settings as they
    /// are; a user preset brings back everything it was saved with.
    /// </summary>
    public RepairPreset AppliedTo(RepairPreset current)
    {
        var next = Clone();
        next.Name = "";
        if (!IsBuiltIn) return next;
        next = current.Clone();
        next.Name = "";
        next.Signal = Signal; next.Colour = Colour; next.Noise = Noise; next.Block = Block;
        next.Detail = Detail; next.Motion = Motion;
        return next;
    }

    /// <summary>Whether the repairs are what this preset gives; a user preset must match size, speed, OSD and look too.</summary>
    public bool Matches(RepairPreset current)
    {
        var target = AppliedTo(current);
        return target.Signal == current.Signal && target.Noise == current.Noise && target.Detail == current.Detail
               && target.Motion == current.Motion && target.Size == current.Size && target.Effort == current.Effort
               && (current.Signal == "digital" || target.Colour == current.Colour)
               && (current.Signal != "digital" || target.Block == current.Block)
               && target.Osd == current.Osd && target.Look == current.Look
               && (!current.Look || (target.LookGrade, target.LookGlow, target.LookVignette, target.LookGrain, target.LookFringe)
                   == (current.LookGrade, current.LookGlow, current.LookVignette, current.LookGrain, current.LookFringe));
    }

    /// <summary>
    /// The answers to "How bad is it?" per signal, light to strong; they cannot be renamed
    /// or deleted. Values follow the engine: colour repair and block repair 0..100 (analog
    /// colour rebuild, HDZero breakup rebuild), noise reduction is the weight of the
    /// five-frame fusion, detail its clipped edge lift. Normal is the former standard preset.
    /// </summary>
    public static IReadOnlyList<RepairPreset> BuiltIn { get; } =
    [
        new() { Id = "analog-light", Level = "light", Name = UiText.LevelLight, Description = UiText.LevelAnalogLight, Colour = 35, Noise = 25, Detail = 15 },
        new() { Id = "analog-normal", Level = "normal", Name = UiText.LevelNormal, Description = UiText.LevelAnalogNormal },
        new() { Id = "analog-strong", Level = "strong", Name = UiText.LevelStrong, Description = UiText.LevelAnalogStrong, Colour = 90, Noise = 80, Detail = 10 },
        new() { Id = "hdzero-light", Level = "light", Signal = "digital", Name = UiText.LevelLight, Description = UiText.LevelHdzeroLight, Block = 20, Noise = 0, Detail = 15 },
        new() { Id = "hdzero-normal", Level = "normal", Signal = "digital", Name = UiText.LevelNormal, Description = UiText.LevelHdzeroNormal },
        new() { Id = "hdzero-strong", Level = "strong", Signal = "digital", Name = UiText.LevelStrong, Description = UiText.LevelHdzeroStrong, Block = 70, Noise = 75, Detail = 10 },
    ];

    /// <summary>The built-in level for a signal ("analog" or "digital") and a level name.</summary>
    public static RepairPreset LevelFor(string signal, string level) =>
        BuiltIn.FirstOrDefault(preset => preset.Signal == signal && preset.Level == level)
        ?? BuiltIn.First(preset => preset.Signal == signal && preset.Level == "normal");
}

/// <summary>The cut of one recording, so it comes back when the file is added again.</summary>
public sealed class ClipMemory
{
    [JsonPropertyName("size")] public long Size { get; set; }
    [JsonPropertyName("modified")] public long Modified { get; set; }
    [JsonPropertyName("segments")] public List<double[]> Segments { get; set; } = [];
    [JsonPropertyName("bookmarks")] public List<double> Bookmarks { get; set; } = [];
    [JsonPropertyName("seen")] public long Seen { get; set; }
    // File name (in the osd-masks folder) of the hand corrections of the OSD mask; null without.
    [JsonPropertyName("osdMask")] public string? OsdMask { get; set; }
}

/// <summary>How the last share video was made: format, layout per format, length and the extras.</summary>
public sealed class ShareChoices
{
    [JsonPropertyName("format")] public string Format { get; set; } = "landscape";
    [JsonPropertyName("layoutLandscape")] public string LayoutLandscape { get; set; } = "side";
    [JsonPropertyName("layoutVertical")] public string LayoutVertical { get; set; } = "stacked";
    [JsonPropertyName("length")] public int Length { get; set; } = 30;
    [JsonPropertyName("highlights")] public bool Highlights { get; set; } = true;
    // Sound is on for 16:9 and off for 9:16, where music is usually added on the platform.
    [JsonPropertyName("soundLandscape")] public bool SoundLandscape { get; set; } = true;
    [JsonPropertyName("soundVertical")] public bool SoundVertical { get; set; }
    [JsonPropertyName("watermark")] public bool Watermark { get; set; } = true;
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
    // The user's own presets; presets saved by 0.6.0 and earlier load here as they were.
    [JsonPropertyName("presets")] public List<RepairPreset> Presets { get; set; } = [];
    // The preset the current settings started from: a built-in level id or "user:" plus the name.
    [JsonPropertyName("lastPreset")] public string? LastPreset { get; set; }
    [JsonPropertyName("clips")] public Dictionary<string, ClipMemory> Clips { get; set; } = new();
    // The last share video choices; settings files from before Share load with these defaults.
    [JsonPropertyName("share")] public ShareChoices Share { get; set; } = new();

    private const int RememberedClips = 300;
    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true };

    public static string Folder()
    {
        // FPV_DVR_CLEANER_HOME points the settings and models somewhere else, e.g. a portable folder or a test profile.
        var custom = Environment.GetEnvironmentVariable("FPV_DVR_CLEANER_HOME");
        var folder = !string.IsNullOrWhiteSpace(custom) ? custom
            : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "FPV DVR Cleaner");
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

    /// <summary>Folder of the hand-corrected OSD masks, one PNG per recording.</summary>
    public static string MaskFolder()
    {
        var folder = Path.Combine(Folder(), "osd-masks");
        Directory.CreateDirectory(folder);
        return folder;
    }

    /// <summary>Where the OSD mask corrections of a recording are kept.</summary>
    public static string MaskPath(string path)
    {
        var hash = Convert.ToHexString(System.Security.Cryptography.SHA1.HashData(System.Text.Encoding.UTF8.GetBytes(ClipKey(path))))[..16];
        return Path.Combine(MaskFolder(), $"{hash.ToLowerInvariant()}.png");
    }

    /// <summary>The OSD mask corrections remembered for a recording, if the file is still there.</summary>
    public static string? RememberedMask(ClipMemory memory) =>
        memory.OsdMask is { Length: > 0 } name && File.Exists(Path.Combine(MaskFolder(), name)) ? Path.Combine(MaskFolder(), name) : null;

    public void RememberCut(string path, IEnumerable<(double In, double Out)> segments, IEnumerable<double> bookmarks, string? osdMask = null)
    {
        try
        {
            var info = new FileInfo(path);
            Clips[ClipKey(path)] = new ClipMemory
            {
                Size = info.Length, Modified = info.LastWriteTimeUtc.Ticks, Seen = DateTime.UtcNow.Ticks,
                Segments = segments.Select(s => new[] { s.In, s.Out }).ToList(),
                Bookmarks = bookmarks.ToList(),
                OsdMask = osdMask is { } mask && File.Exists(mask) ? Path.GetFileName(mask) : null,
            };
        }
        catch (IOException) { /* The file went away; nothing to remember. */ }
    }
}
