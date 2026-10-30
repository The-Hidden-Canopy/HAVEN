namespace Haven.Desktop;

/// <summary>Refresh-race protection for the Latch plugin pages. Every
/// refresh starts with <see cref="BeginRefresh"/>, which hands back a
/// monotonically increasing token; when the HTTP response lands, the page
/// applies it only if <see cref="IsCurrent"/> still says the token is the
/// latest one. A newer refresh (or an explicit <see cref="Invalidate"/> on
/// navigation/tab switch) supersedes every in-flight one, so slow or
/// duplicated responses can never paint stale data over a fresher load.
/// Thread-safe: tokens are unique across concurrent starters.</summary>
public sealed class LatchRefreshCoordinator
{
    private long _latest;

    /// <summary>The most recently issued token.</summary>
    public long Current => Interlocked.Read(ref _latest);

    /// <summary>Starts a new refresh and returns its token. All previously
    /// issued tokens immediately become stale.</summary>
    public long BeginRefresh() => Interlocked.Increment(ref _latest);

    /// <summary>True while <paramref name="token"/> is still the latest —
    /// i.e. its response may be applied. False once any newer refresh
    /// began or <see cref="Invalidate"/> ran.</summary>
    public bool IsCurrent(long token) => Interlocked.Read(ref _latest) == token;

    /// <summary>Supersedes every in-flight refresh without starting a new
    /// one (e.g. the page navigated away and nothing should land).</summary>
    public void Invalidate() => Interlocked.Increment(ref _latest);
}
