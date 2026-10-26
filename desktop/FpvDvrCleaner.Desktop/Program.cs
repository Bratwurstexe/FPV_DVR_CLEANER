using Avalonia;
using Avalonia.Media;
using System;

namespace FpvDvrCleaner.Desktop;

class Program
{
    // Initialization code. Don't use any Avalonia, third-party APIs or any
    // SynchronizationContext-reliant code before AppMain is called: things aren't initialized
    // yet and stuff might break.
    [STAThread]
    public static void Main(string[] args)
    {
        // The language comes first: every text of the window is read in it.
        Loc.Use(UserSettings.Load().Language);
        BuildAvaloniaApp().StartWithClassicDesktopLifetime(args);
    }

    // Avalonia configuration, don't remove; also used by visual designer.
    public static AppBuilder BuildAvaloniaApp()
        => AppBuilder.Configure<App>()
            .UsePlatformDetect()
#if DEBUG
            .WithDeveloperTools()
#endif
            .WithInterFont()
            .With(new FontManagerOptions { FontFallbacks = Loc.FontFallbacks().Select(family => new FontFallback { FontFamily = family }).ToArray() })
            .LogToTrace();
}
