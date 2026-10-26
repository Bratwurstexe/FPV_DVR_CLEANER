using System.Globalization;
using System.Reflection;
using System.Text.Json;
using System.Text.RegularExpressions;
using Avalonia.Media;

namespace FpvDvrCleaner.Desktop;

/// <summary>
/// The language of the app. English lives in <see cref="UiText"/> and is the fallback for
/// anything a translation lacks; every other language is an embedded Locales/&lt;code&gt;.json
/// with the same names under "ui" and the engine's English messages under "engine".
/// It is chosen once at start, before any window exists; a change needs a restart.
/// </summary>
public static class Loc
{
    public sealed record Language(string Code, string Name);

    /// <summary>The languages of the language menu, each in its own name.</summary>
    public static IReadOnlyList<Language> Languages { get; } =
    [
        new("en", "English"), new("de", "Deutsch"), new("fr", "Français"), new("es", "Español"),
        new("it", "Italiano"), new("pt-BR", "Português (Brasil)"), new("nl", "Nederlands"), new("pl", "Polski"),
        new("cs", "Čeština"), new("ru", "Русский"), new("uk", "Українська"), new("tr", "Türkçe"),
        new("ja", "日本語"), new("zh-Hans", "简体中文"), new("ko", "한국어"),
    ];

    /// <summary>The language in use, a code of <see cref="Languages"/>.</summary>
    public static string Current { get; private set; } = "en";

    private static Dictionary<string, string> _engine = new();
    private static List<(Regex Pattern, string Text)> _enginePatterns = [];

    /// <summary>The supported language closest to a culture; English when there is none.</summary>
    public static string Detect(CultureInfo culture)
    {
        for (var current = culture; !string.IsNullOrEmpty(current.Name); current = current.Parent)
        {
            var name = current.Name;
            if (name.StartsWith("zh", StringComparison.OrdinalIgnoreCase)) return "zh-Hans";
            if (name.StartsWith("pt", StringComparison.OrdinalIgnoreCase)) return "pt-BR";
            if (Languages.FirstOrDefault(language => string.Equals(language.Code, name, StringComparison.OrdinalIgnoreCase)) is { } match)
                return match.Code;
        }
        return "en";
    }

    /// <summary>Switches to a language code, or to the system language for null or "auto".</summary>
    public static void Use(string? choice)
    {
        var code = choice is null or "" or "auto" ? Detect(CultureInfo.CurrentUICulture)
            : Languages.Any(language => language.Code == choice) ? choice : "en";
        Current = code;
        if (code == "en") return;
        using var stream = typeof(Loc).Assembly.GetManifestResourceStream($"FpvDvrCleaner.Desktop.Locales.{code}.json");
        if (stream == null) { Current = "en"; return; }
        try
        {
            using var document = JsonDocument.Parse(stream);
            if (document.RootElement.TryGetProperty("ui", out var ui))
                foreach (var entry in ui.EnumerateObject())
                    if (entry.Value.GetString() is { Length: > 0 } text
                        && typeof(UiText).GetProperty(entry.Name, BindingFlags.Public | BindingFlags.Static) is { PropertyType: var type } property
                        && type == typeof(string))
                        property.SetValue(null, text);
            if (document.RootElement.TryGetProperty("engine", out var engine))
                foreach (var entry in engine.EnumerateObject())
                    if (entry.Value.GetString() is { Length: > 0 } text)
                    {
                        if (entry.Name.Contains("{0}"))
                            _enginePatterns.Add((new Regex("^" + Regex.Replace(Regex.Escape(entry.Name), @"\\\{(\d)}", "(?<p$1>.+?)") + "$", RegexOptions.Singleline), text));
                        else _engine[entry.Name] = text;
                    }
        }
        catch (JsonException)
        {
            // A damaged translation leaves English in place rather than stopping the app.
        }
    }

    /// <summary>A message of the video engine in the current language; unknown ones (FFmpeg's own) stay as they are.</summary>
    public static string Engine(string? message)
    {
        if (string.IsNullOrEmpty(message)) return message ?? "";
        var trimmed = message.Trim();
        if (_engine.TryGetValue(trimmed, out var text)) return text;
        foreach (var (pattern, translated) in _enginePatterns)
            if (pattern.Match(trimmed) is { Success: true } match)
                return Regex.Replace(translated, @"\{(\d)}", placeholder => match.Groups["p" + placeholder.Groups[1].Value].Value);
        return message;
    }

    /// <summary>
    /// Fonts with Chinese, Japanese and Korean glyphs on Windows, macOS and Linux, for text
    /// Inter cannot draw. The current language's script comes first, so Japanese text is not
    /// drawn with Chinese forms of the same characters.
    /// </summary>
    public static FontFamily[] FontFallbacks()
    {
        string[] chinese = ["Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei"];
        string[] japanese = ["Yu Gothic UI", "Meiryo UI", "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans CJK JP", "Source Han Sans JP"];
        string[] korean = ["Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans CJK KR", "Source Han Sans KR"];
        string[][] order = Current switch
        {
            "ja" => [japanese, chinese, korean],
            "ko" => [korean, chinese, japanese],
            _ => [chinese, japanese, korean],
        };
        return order.SelectMany(names => names).Append("Segoe UI").Append("DejaVu Sans")
            .Select(name => new FontFamily(name)).ToArray();
    }
}
