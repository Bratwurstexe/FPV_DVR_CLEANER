using System.Text.Json;
using System.Text.Json.Serialization;

namespace FpvDvrCleaner.Launcher;

/// <summary>
/// The launcher's choices, kept next to the app's own settings in the per-user
/// application data folder. Missing or damaged files are ignored.
/// </summary>
public sealed class LauncherSettings
{
    /// <summary>Where the app is installed; null means the folder the launcher sits in.</summary>
    [JsonPropertyName("installFolder")] public string? InstallFolder { get; set; }

    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true };

    public static string FilePath() =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "FPV DVR Cleaner", "launcher.json");

    public static LauncherSettings Load()
    {
        try
        {
            var path = FilePath();
            if (File.Exists(path))
                return JsonSerializer.Deserialize<LauncherSettings>(File.ReadAllText(path), Options) ?? new LauncherSettings();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException)
        {
            // A broken settings file must never stop the launcher from starting.
        }
        return new LauncherSettings();
    }

    /// <summary>False when the choice could not be written.</summary>
    public bool Save()
    {
        try
        {
            var path = FilePath();
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            var draft = path + ".tmp";
            File.WriteAllText(draft, JsonSerializer.Serialize(this, Options));
            File.Move(draft, path, overwrite: true);
            return true;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return false;
        }
    }

    /// <summary>The saved folder if it still exists, otherwise the launcher's own folder.</summary>
    public string EffectiveFolder(out bool savedFolderMissing)
    {
        savedFolderMissing = false;
        if (string.IsNullOrWhiteSpace(InstallFolder))
            return Installer.LauncherFolder();
        if (Directory.Exists(InstallFolder))
            return InstallFolder;
        savedFolderMissing = true;
        return Installer.LauncherFolder();
    }
}
