"""Source/XAML contract tests for the native Latch plugin page.

The Latch page is native WinUI, so like test_native_repair_contracts.py these
checks pin the high-risk contracts (shared hasp mark, theme-token-only
styling, accessibility, honest state surfaces, the read/propose-only action
boundary and handoff honesty) at the source level, where no WinUI test host
is required. They deliberately read only this track's files: the Latch plugin
page, its navigation wiring, and the hasp mark control.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native" / "Haven.Desktop"
XAML = (NATIVE / "MainWindow.xaml").read_text(encoding="utf-8")
NAV = (NATIVE / "MainWindow.Navigation.cs").read_text(encoding="utf-8")
LATCH = (NATIVE / "MainWindow.Latch.cs").read_text(encoding="utf-8")
HASP_XAML = (NATIVE / "Controls" / "LatchHaspMark.xaml").read_text(encoding="utf-8")
HASP_CS = (NATIVE / "Controls" / "LatchHaspMark.xaml.cs").read_text(encoding="utf-8")
THEME_SERVICE = (NATIVE / "Services" / "ThemeService.cs").read_text(encoding="utf-8")
PROJECTION = (NATIVE / "Services" / "LatchRecordProjection.cs").read_text(encoding="utf-8")


def _latch_xaml_section() -> str:
    start = XAML.index('x:Name="LatchPanel"')
    end = XAML.index('<!-- Models (spec 38)')
    return XAML[start:end]


def test_haspmark_draws_the_full_hinged_gate_latch_geometry():
    # Recognizable gate-latch/hasp: base plate, hinge barrel, strap, staple,
    # T-handle - explicitly not a door/bolt/padlock shape vocabulary.
    for part in ("BasePlate", "HingeBarrel", "HingePin", "HaspStrap", "Staple", "HandleBar", "HandleGrip"):
        assert f'x:Name="{part}"' in HASP_XAML
    # The staple is a U-shaped receiver (open path), the handle a T lever.
    assert 'Data="M 28.5 10.5 L 37.5 10.5 L 37.5 21.5 L 28.5 21.5"' in HASP_XAML
    # Simplified mode is a first-class switch on the same vector geometry.
    assert "SimplifiedProperty" in HASP_CS
    assert 'new PropertyMetadata(false, OnSimplifiedChanged)' in HASP_CS


def test_same_haspmark_serves_heading_full_and_navigation_simplified():
    nav = XAML[XAML.index('Tag="latch"'):XAML.index('Tag="latch"') + 800]
    assert "LatchHaspMark" in nav
    assert "Simplified=\"True\"" in nav
    section = _latch_xaml_section()
    heading_mark = section[section.index("LatchHaspMark"):]
    assert "Simplified=\"True\"" not in heading_mark[: 120]


def test_latch_ui_uses_only_haven_theme_tokens_no_competing_palette_or_switch():
    section = _latch_xaml_section()
    # Every dynamic brush in the page binds a Haven* ThemeResource token.
    assert "ThemeResource Haven" in section
    for bad in ("#FF", "#00", "#1", "#2", "#3", "#4", "#5", "#6", "#7", "#8", "#9", "#A", "#B", "#C", "#D", "#E", "#F"):
        assert f'Fill="{bad}' not in section
        assert f'Foreground="{bad}' not in section
        assert f'Background="{bad}' not in section
        assert f'BorderBrush="{bad}' not in section
    # No competing theme switch anywhere in the track's files.
    for text in (LATCH, HASP_CS, HASP_XAML):
        assert "RequestedTheme" not in text
        assert "ElementTheme" not in text
        assert "settings.json" not in text
    assert "ThemeService" not in LATCH  # appearance stays owned by the shell


def test_themeservice_persistence_remains_the_single_appearance_owner():
    # The plugin reuses ThemeService by inheritance (tokens), never by
    # touching its persisted settings or adding a second persistence path.
    assert "settings.json" in THEME_SERVICE
    assert "SaveAsync" in THEME_SERVICE
    assert "Haven.Desktop" in THEME_SERVICE
    for text in (LATCH, HASP_CS):
        assert "SaveAsync" not in text
        assert "LocalApplicationData" not in text
    for text in (HASP_XAML, _latch_xaml_section()):
        assert "ThemeDictionaries" not in text


def test_page_has_three_keyboard_accessible_tabs_with_accessible_names():
    section = _latch_xaml_section()
    assert 'AutomationProperties.Name="Latch ledger tab"' in section
    assert 'AutomationProperties.Name="Latch access review tab"' in section
    assert 'AutomationProperties.Name="Latch review proposals tab"' in section
    assert 'AutomationProperties.Name="Latch records"' in section
    assert 'AutomationProperties.Name="Latch"' in XAML
    for shortcut in ("VirtualKey.Number1", "VirtualKey.Number2", "VirtualKey.Number3", "VirtualKey.F5"):
        assert shortcut in LATCH
    assert "LatchPanel.KeyboardAccelerators.Add" in LATCH
    assert "KeyboardAccelerator" in LATCH


def test_state_surfaces_are_distinct_and_honest():
    section = _latch_xaml_section()
    # Loading, empty, stale-last-good, disconnected and error each have their
    # own surface; they are never folded into one another.
    for surface in (
        "LatchLoadingPanel",
        "LatchEmptyText",
        "LatchStaleBanner",
        "LatchDisconnectedCard",
        "LatchErrorText",
    ):
        assert surface in section
    assert "Showing last successful data from" in LATCH
    assert "Not connected to Latch" in section
    assert "LatchDemoChip" in section
    assert "Synthetic demo" in section
    # Refresh-race protection: coordinator tokens, latest-wins rendering.
    assert "_latchRefresh" in LATCH
    assert "_latchRefresh.BeginRefresh()" in LATCH
    assert "_latchRefresh.IsCurrent(token)" in LATCH


def test_action_boundary_is_read_and_propose_only():
    # The only mutating call the page makes is the idempotent proposal.
    assert "PlanCancellationAsync" in LATCH
    for forbidden in ("AuthorizeAsync", "ExecuteAsync", "VerifyAsync", '"/authorize"', '"/execute"', '"/verify"'):
        assert forbidden not in LATCH
    # Repeated-click guard while a proposal is in flight, re-enabled after.
    assert "_latchPlanInFlight" in LATCH
    assert "button.IsEnabled = true" in LATCH
    # Handoff honesty: the deep link is only opened when Latch's own UI is
    # actually served (IsHandoffAvailableAsync probes the root for HTML, so
    # an API-only daemon can never open a 404), otherwise the button is
    # disabled with an explanation.
    assert "IsHandoffAvailableAsync" in LATCH
    assert "GetAuthorizationHandoffUri" in LATCH
    assert "Latch UI unavailable" in LATCH
    assert "Launcher.LaunchUriAsync" in LATCH


def test_action_states_stay_distinct_and_transport_success_is_not_verification():
    for state in ('"proposed"', '"authorized"', '"executed"', '"needs_verification"', '"succeeded"', '"failed"'):
        assert state in PROJECTION
    # needs_verification is explicitly not complete; only verified is.
    assert '"needs_verification" => new("Executed — awaiting verification", false' in PROJECTION
    assert '"succeeded" or "verified" => new("Verified", true' in PROJECTION
    assert '"failed" => new("Failed", false' in PROJECTION
    assert "transport success is not verification" in LATCH
    # Distinct lifecycle stages in action details.
    assert "ActionStages" in LATCH
    assert "LatchStageChip" in LATCH
    # Status re-read on window reactivation/return.
    assert "Activated +=" in LATCH
    assert "WindowActivationState.Deactivated" in LATCH


def test_real_latch_shapes_are_projected_not_flat_fields():
    # Commitments: economics.{amount_minor,currency,cadence},
    # temporal.{renewal_at,cancel_before}, confidence.overall.
    assert "economics" in PROJECTION
    assert '"temporal"' in PROJECTION or "TryGetProperty(\"temporal\"" in PROJECTION
    assert '"renewal_at"' in PROJECTION
    assert '"cancel_before"' in PROJECTION
    assert '"overall"' in PROJECTION
    # Access: grantee/resource objects, stale/stale_reason, connector fields.
    for field in ('"grantee"', '"resource"', '"stale_reason"', '"source_connector"', '"source_object_id"'):
        assert field in PROJECTION
    # Scheduler proposals: nested targets (access_grant_id/commitment_id) and
    # an evidence ARRAY of objects.
    assert "access_grant_id" in PROJECTION
    assert "commitment_id" in PROJECTION
    assert "LatchEvidenceRef" in PROJECTION
    # The review screen asks Latch for what needs review.
    assert "GetAccessAsync(staleOnly: true)" in LATCH
    assert "No access currently needs review." in LATCH
    # The page consumes the projection rather than re-deriving flat fields.
    assert "LatchRecordProjection." in LATCH


def test_details_load_original_evidence_under_a_race_token():
    assert "GetCommitmentAsync" in LATCH
    assert "GetEvidenceAsync(id, includeContent: true)" in LATCH
    assert "_latchDetailsSequence" in LATCH
    assert "Interlocked.Increment(ref _latchDetailsSequence)" in LATCH
    assert "unavailable" in LATCH
    # Connector-observation-only grants must not block on or invent evidence.
    assert "No backing evidence record - this grant is a connector observation" in LATCH
    # Source observation comes from real evidence records, never fabricated.
    assert "_latchEvidence" in LATCH
    assert "unavailable" in LATCH
    for fake in ("observed via", "Source: synthetic", "demo connector"):
        assert fake not in LATCH


def test_startup_guidance_uses_the_full_latchd_command():
    # Every startup hint must carry the full form: a bare "--web-root" with
    # no path does not serve Latch's desktop UI.
    full_form = "--http-port 8787 --web-root apps/desktop"
    section = _latch_xaml_section()
    assert full_form in section
    assert full_form in LATCH
    assert "--web-root." not in section
    assert "--web-root)" not in section


def test_all_data_comes_from_latchclient_and_no_dates_are_computed():
    assert "GetCommitmentsAsync" in LATCH
    assert "GetAccessAsync" in LATCH
    assert "GetReviewProposalsAsync" in LATCH
    assert "GetActionPlansAsync" in LATCH
    # Renewal/cancel-by instants are rendered from Latch fields verbatim;
    # HAVEN must not compute them.
    assert 'TryString(temporal, "renewal_at")' in PROJECTION
    assert 'TryString(temporal2, "cancel_before")' in PROJECTION
    assert "AddDays" not in LATCH
    assert "AddDays" not in PROJECTION
    assert "AddMonths" not in LATCH
    assert "AddMonths" not in PROJECTION
    # No fabricated dashboard rows: no provider/content constants.
    for fake in ("Netflix", "Adobe", "Spotify", "com_provider_"):
        assert fake not in LATCH
        assert fake not in _latch_xaml_section()


def test_client_configuration_env_and_demo_defaults():
    # Base URL override, loopback default and demo labeling live in the
    # LatchClient adapter (client track); the page consumes them via
    # CreateDefault()/IsDemoMode and must not re-implement or bypass them.
    client_path = NATIVE / "Services" / "LatchClient.cs"
    if not client_path.exists():
        return  # client track not landed yet; page-side contracts above still hold
    client = client_path.read_text(encoding="utf-8")
    assert "http://127.0.0.1:8787" in client
    assert "HAVEN_LATCH_BASE_URL" in client
    assert "HAVEN_LATCH_DEMO" in client
    assert "PlanCancellationAsync" in client
    # The handoff probe requires a served HTML UI, not just a live API.
    assert "IsHandoffAvailableAsync" in client
    assert "text/html" in client
    for forbidden in ("AuthorizeAsync", "ExecuteAsync", "VerifyAsync"):
        assert forbidden not in client


def test_navigation_wires_the_latch_page_without_touching_other_pages():
    assert 'LatchPanel.Visibility = tag == "latch"' in NAV
    assert '"Latch"' in NAV or '"latch" =>' in NAV
    assert "LoadLatchAsync" in NAV
    assert 'case "latch":' in NAV
