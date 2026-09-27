namespace Haven.Desktop.Models;

/// <summary>Six deterministic, clock-based dayparts (Needs You spec 12) --
/// no location or network required for the default state.</summary>
public enum TemporalDaypart
{
    Dawn,
    Morning,
    Midday,
    Afternoon,
    Evening,
    Night,
}

/// <summary>What the Today hero should show right now (Needs You spec 20).
/// `OverlayBrushKey`/`ForegroundBrushKey` are semantic resource keys, not a
/// hard-coded brush instance, so theme integration (spec 14) can retint the
/// same daypart asset without the service knowing about themes.</summary>
public sealed record TemporalVisualState(
    TemporalDaypart Daypart,
    Uri AssetUri,
    string OverlayBrushKey,
    string ForegroundBrushKey,
    DateTimeOffset NextBoundary,
    bool MotionAllowed);
