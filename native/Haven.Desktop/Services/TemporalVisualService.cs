using System.Text.Json;
using Haven.Desktop.Models;
using Microsoft.UI.Dispatching;

namespace Haven.Desktop.Services;

/// <summary>Resolves the current temporal daypart, its bundled local asset,
/// and schedules the one timer needed for the next boundary (Needs You spec
/// 20). Presentation logic tied to Windows resources and the local clock --
/// deliberately native-only, no IPC, no network for the default family.</summary>
public interface ITemporalVisualService
{
    TemporalVisualState Resolve(DateTimeOffset localNow, bool reducedMotion);

    event EventHandler<TemporalVisualState>? StateChanged;

    void Start();

    void Stop();
}

public sealed class TemporalVisualService : ITemporalVisualService, IDisposable
{
    // Daypart default local-time boundaries (spec 12.1). Ordered so the
    // first entry whose start hour is still ahead of `localNow` wins;
    // falling through all of them means "past Night's start (21:00)," which
    // Night already covers via ResolveDaypart's own fallback.
    private static readonly (TemporalDaypart Daypart, int StartHour)[] Boundaries =
    {
        (TemporalDaypart.Dawn, 5),
        (TemporalDaypart.Morning, 7),
        (TemporalDaypart.Midday, 11),
        (TemporalDaypart.Afternoon, 14),
        (TemporalDaypart.Evening, 18),
        (TemporalDaypart.Night, 21),
    };

    private const string DefaultOverlayBrushKey = "HavenHeroOverlayBrush";
    private const string DefaultForegroundBrushKey = "HavenHeroForegroundBrush";

    private readonly IReadOnlyDictionary<TemporalDaypart, Uri> _assets;
    private readonly DispatcherQueue _dispatcher;
    private DispatcherQueueTimer? _timer;
    private bool _reducedMotion;

    public event EventHandler<TemporalVisualState>? StateChanged;

    public TemporalVisualService(DispatcherQueue dispatcher)
    {
        _dispatcher = dispatcher;
        _assets = LoadManifest();
    }

    /// <summary>Load and validate the packaged manifest once (spec 20.1).
    /// A missing or malformed manifest degrades to an empty asset map --
    /// the hero then falls back to a flat themed surface (spec 21.1's
    /// alternate presentation) rather than throwing Today into an error
    /// state over decorative art.</summary>
    private static IReadOnlyDictionary<TemporalDaypart, Uri> LoadManifest()
    {
        var map = new Dictionary<TemporalDaypart, Uri>();
        var path = Path.Combine(AppContext.BaseDirectory, "Assets", "Today", "Temporal", "temporal-manifest.json");
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            var assets = document.RootElement.GetProperty("assets");
            foreach (var daypart in Enum.GetValues<TemporalDaypart>())
            {
                var key = daypart.ToString().ToLowerInvariant();
                if (assets.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String)
                {
                    var uriText = value.GetString();
                    if (!string.IsNullOrWhiteSpace(uriText))
                    {
                        map[daypart] = new Uri(uriText);
                    }
                }
            }
        }
        catch (Exception)
        {
            return map;
        }
        return map;
    }

    public static TemporalDaypart ResolveDaypart(DateTimeOffset localNow)
    {
        var hour = localNow.Hour;
        if (hour >= 5 && hour < 7) return TemporalDaypart.Dawn;
        if (hour >= 7 && hour < 11) return TemporalDaypart.Morning;
        if (hour >= 11 && hour < 14) return TemporalDaypart.Midday;
        if (hour >= 14 && hour < 18) return TemporalDaypart.Afternoon;
        if (hour >= 18 && hour < 21) return TemporalDaypart.Evening;
        return TemporalDaypart.Night;
    }

    public static DateTimeOffset NextBoundary(DateTimeOffset localNow)
    {
        foreach (var (_, startHour) in Boundaries)
        {
            var candidate = new DateTimeOffset(
                localNow.Year, localNow.Month, localNow.Day, startHour, 0, 0, localNow.Offset);
            if (candidate > localNow)
            {
                return candidate;
            }
        }
        // Every boundary today has passed (it's Night, past 21:00) -- the
        // next one is tomorrow's Dawn.
        var tomorrow = localNow.Date.AddDays(1);
        return new DateTimeOffset(tomorrow.Year, tomorrow.Month, tomorrow.Day, 5, 0, 0, localNow.Offset);
    }

    public TemporalVisualState Resolve(DateTimeOffset localNow, bool reducedMotion)
    {
        var daypart = ResolveDaypart(localNow);
        _assets.TryGetValue(daypart, out var assetUri);
        return new TemporalVisualState(
            daypart,
            assetUri ?? new Uri($"ms-appx:///Assets/Today/Temporal/{daypart.ToString().ToLowerInvariant()}.jpg"),
            DefaultOverlayBrushKey,
            DefaultForegroundBrushKey,
            NextBoundary(localNow),
            MotionAllowed: !reducedMotion);
    }

    /// <summary>Schedule one timer for the next daypart boundary rather than
    /// a per-second tick (spec 15.1) -- cheap, deterministic, and easy to
    /// test via the static `ResolveDaypart`/`NextBoundary` helpers above.</summary>
    public void Start()
    {
        _reducedMotion = !new global::Windows.UI.ViewManagement.UISettings().AnimationsEnabled;
        ScheduleNext();
    }

    /// <summary>The state for right now, using the reduced-motion flag
    /// captured at <see cref="Start"/>. Callers use this for the initial
    /// paint; <see cref="StateChanged"/> carries every state after that.</summary>
    public TemporalVisualState GetCurrentState() => Resolve(DateTimeOffset.Now, _reducedMotion);

    private void ScheduleNext()
    {
        var state = Resolve(DateTimeOffset.Now, _reducedMotion);
        var delay = state.NextBoundary - DateTimeOffset.Now;
        if (delay < TimeSpan.FromSeconds(1))
        {
            delay = TimeSpan.FromSeconds(1);
        }
        _timer?.Stop();
        _timer = _dispatcher.CreateTimer();
        _timer.Interval = delay;
        _timer.IsRepeating = false;
        _timer.Tick += OnTimerTick;
        _timer.Start();
    }

    private void OnTimerTick(DispatcherQueueTimer sender, object args)
    {
        var next = Resolve(DateTimeOffset.Now, _reducedMotion);
        StateChanged?.Invoke(this, next);
        ScheduleNext();
    }

    public void Stop()
    {
        if (_timer is not null)
        {
            _timer.Tick -= OnTimerTick;
            _timer.Stop();
            _timer = null;
        }
    }

    public void Dispose() => Stop();
}
