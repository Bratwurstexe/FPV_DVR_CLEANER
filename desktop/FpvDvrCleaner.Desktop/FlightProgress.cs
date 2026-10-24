using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using Avalonia.Threading;

namespace FpvDvrCleaner.Desktop;

/// <summary>
/// A progress bar with a little flight in it: a gradient track that fills up,
/// a light that sweeps along it, and a tiny quad that flies at the tip and
/// bobs while it waits. In the indeterminate state the quad patrols the bar.
/// </summary>
public sealed class FlightProgress : Control
{
    public static readonly StyledProperty<double> ValueProperty =
        AvaloniaProperty.Register<FlightProgress, double>(nameof(Value));
    public static readonly StyledProperty<bool> IsIndeterminateProperty =
        AvaloniaProperty.Register<FlightProgress, bool>(nameof(IsIndeterminate), true);

    private static readonly IBrush TrackBrush = new SolidColorBrush(Color.Parse("#ECEDEA"));
    private readonly DispatcherTimer _clock = new() { Interval = TimeSpan.FromMilliseconds(33) };
    private double _time;

    public FlightProgress()
    {
        Height = 48;
        ClipToBounds = false;
        _clock.Tick += (_, _) => { _time += 0.033; InvalidateVisual(); };
        AttachedToVisualTree += (_, _) => _clock.Start();
        DetachedFromVisualTree += (_, _) => _clock.Stop();
    }

    /// <summary>0 to 100.</summary>
    public double Value { get => GetValue(ValueProperty); set => SetValue(ValueProperty, value); }
    public bool IsIndeterminate { get => GetValue(IsIndeterminateProperty); set => SetValue(IsIndeterminateProperty, value); }

    public override void Render(DrawingContext context)
    {
        var width = Bounds.Width;
        if (width <= 0) return;
        const double trackHeight = 10;
        var trackTop = Bounds.Height - trackHeight - 2;
        var track = new Rect(0, trackTop, width, trackHeight);
        context.DrawRectangle(TrackBrush, null, new RoundedRect(track, trackHeight / 2));

        // Where the tip is: the real value, or a patrol between the ends.
        var fraction = IsIndeterminate ? 0.5 + 0.5 * Math.Sin(_time * 1.6) : Math.Clamp(Value / 100, 0, 1);
        var tipX = fraction * width;

        if (IsIndeterminate)
        {
            // A soft trail behind the patrolling quad.
            var direction = Math.Cos(_time * 1.6) >= 0 ? 1 : -1;
            var trailStart = Math.Clamp(tipX - direction * 90, 0, width);
            var trail = new Rect(Math.Min(tipX, trailStart), trackTop, Math.Abs(tipX - trailStart), trackHeight);
            var trailBrush = new LinearGradientBrush
            {
                StartPoint = new RelativePoint(direction > 0 ? 0 : 1, 0, RelativeUnit.Relative),
                EndPoint = new RelativePoint(direction > 0 ? 1 : 0, 0, RelativeUnit.Relative),
                GradientStops = { new GradientStop(Color.Parse("#00F08A3A"), 0), new GradientStop(Color.Parse("#F08A3A"), 1) },
            };
            if (trail.Width > 1) context.DrawRectangle(trailBrush, null, new RoundedRect(trail, trackHeight / 2));
        }
        else if (tipX > 1)
        {
            var fill = new Rect(0, trackTop, tipX, trackHeight);
            var fillBrush = new LinearGradientBrush
            {
                StartPoint = new RelativePoint(0, 0, RelativeUnit.Relative),
                EndPoint = new RelativePoint(1, 0, RelativeUnit.Relative),
                GradientStops = { new GradientStop(Color.Parse("#A64600"), 0), new GradientStop(Color.Parse("#F08A3A"), 0.7), new GradientStop(Color.Parse("#FFC857"), 1) },
            };
            context.DrawRectangle(fillBrush, null, new RoundedRect(fill, trackHeight / 2));
            // A light sweeping along the filled part.
            var sweep = (_time * 0.45) % 1.3 - 0.15;
            var lightX = sweep * tipX;
            var light = new Rect(lightX - 18, trackTop, 36, trackHeight);
            var lightBrush = new LinearGradientBrush
            {
                StartPoint = new RelativePoint(0, 0, RelativeUnit.Relative),
                EndPoint = new RelativePoint(1, 0, RelativeUnit.Relative),
                GradientStops = { new GradientStop(Color.Parse("#00FFFFFF"), 0), new GradientStop(Color.Parse("#90FFFFFF"), 0.5), new GradientStop(Color.Parse("#00FFFFFF"), 1) },
            };
            using (context.PushClip(new RoundedRect(fill, trackHeight / 2)))
                context.DrawRectangle(lightBrush, null, light);
        }

        DrawQuad(context, Math.Clamp(tipX, 20, width - 20), trackTop - 16);
    }

    private static readonly IBrush FrameBrush = new SolidColorBrush(Color.Parse("#2C3238"));
    private static readonly IBrush MotorBrush = new SolidColorBrush(Color.Parse("#4B545D"));
    private static readonly IBrush PropBrush = new SolidColorBrush(Color.Parse("#7020252B"));
    private static readonly IBrush PropTipBrush = new SolidColorBrush(Color.Parse("#F08A3A"));
    private static readonly IBrush CameraBrush = new SolidColorBrush(Color.Parse("#111518"));
    private static readonly IBrush LensBrush = new SolidColorBrush(Color.Parse("#5FA8E8"));
    private static readonly IBrush BatteryBrush = new SolidColorBrush(Color.Parse("#FFC857"));
    private static readonly IBrush TailBrush = new SolidColorBrush(Color.Parse("#E5484D"));
    private static readonly IBrush ShadowBrush = new SolidColorBrush(Color.Parse("#30000000"));
    private static readonly Typeface Face = new("Inter, Segoe UI, Helvetica, Arial");

    /// <summary>
    /// A small 5-inch quad seen from above, flying to the right: X frame with
    /// four arms, motors with spinning two-blade props, a camera and lens at
    /// the front, the battery strapped on top, an antenna at the back and a red
    /// tail LED. It hovers a little and casts a shadow on the track.
    /// </summary>
    private void DrawQuad(DrawingContext context, double x, double y)
    {
        const double scale = 1.45;
        using var _ = context.PushTransform(Matrix.CreateTranslation(-x, -y) * Matrix.CreateScale(scale, scale) * Matrix.CreateTranslation(x, y));
        var hover = Math.Sin(_time * 5) * 2.5;
        y += hover;
        // Shadow on the track, smaller when the quad is higher.
        var shadowScale = 1 - (hover + 2.5) / 12;
        context.DrawEllipse(ShadowBrush, null, new Point(x, y + 14), 12 * shadowScale, 2.2 * shadowScale);

        var arms = new[] { (-8.0, -6.0), (8.0, -6.0), (-8.0, 6.0), (8.0, 6.0) };
        var armPen = new Pen(FrameBrush, 2.4, lineCap: PenLineCap.Round);
        foreach (var (dx, dy) in arms)
            context.DrawLine(armPen, new Point(x, y), new Point(x + dx, y + dy));
        // Props first, so the motors sit on top of the blades.
        foreach (var (dx, dy) in arms)
        {
            var angle = _time * 22 + dx * 0.7 + dy * 0.3;
            for (var blade = 0; blade < 2; blade++)
            {
                var a = angle + blade * Math.PI;
                var tip = new Point(x + dx + Math.Cos(a) * 6.5, y + dy + Math.Sin(a) * 6.5 * 0.6);
                var root = new Point(x + dx, y + dy);
                context.DrawLine(new Pen(PropBrush, 2.2, lineCap: PenLineCap.Round), root, tip);
                context.DrawEllipse(PropTipBrush, null, tip, 1.1, 1.1);
            }
            // The blur disc of a spinning prop.
            context.DrawEllipse(new SolidColorBrush(Color.Parse("#2220252B")), null, new Point(x + dx, y + dy), 6.5, 3.9);
            context.DrawEllipse(MotorBrush, null, new Point(x + dx, y + dy), 2.4, 2.4);
            context.DrawEllipse(FrameBrush, null, new Point(x + dx, y + dy), 1.1, 1.1);
        }
        // Body plates, battery on top, camera at the front (right), antenna and tail LED at the back.
        context.DrawRectangle(FrameBrush, null, new RoundedRect(new Rect(x - 6, y - 3, 12, 6), 2));
        context.DrawRectangle(BatteryBrush, null, new RoundedRect(new Rect(x - 4.5, y - 2, 7, 4), 1.2));
        context.DrawRectangle(FrameBrush, null, new Rect(x - 2.5, y - 2.6, 1, 5.2));
        context.DrawRectangle(FrameBrush, null, new Rect(x + 0.5, y - 2.6, 1, 5.2));
        context.DrawRectangle(CameraBrush, null, new RoundedRect(new Rect(x + 5, y - 2.2, 3.6, 4.4), 1));
        context.DrawEllipse(LensBrush, null, new Point(x + 7.6, y), 1.2, 1.2);
        context.DrawLine(new Pen(FrameBrush, 1), new Point(x - 6, y), new Point(x - 11, y - 1.5));
        var blink = Math.Sin(_time * 8) > 0;
        context.DrawEllipse(blink ? TailBrush : MotorBrush, null, new Point(x - 6.5, y + 1.5), 1.3, 1.3);

        if (!IsIndeterminate)
        {
            var label = new FormattedText($"{Math.Clamp(Value, 0, 100):0} %", System.Globalization.CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight, new Typeface(Face.FontFamily, FontStyle.Normal, FontWeight.SemiBold), 11, FrameBrush);
            var labelX = x + 16 + label.Width > Bounds.Width ? x - 16 - label.Width : x + 16;
            context.DrawText(label, new Point(labelX, y - 7));
        }
    }
}
