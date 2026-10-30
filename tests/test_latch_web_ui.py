"""Source contract tests for the Latch WebUI compatibility page.

The native Latch page is WinUI and cannot run on this host (the Windows App
SDK shell crashes before any page renders), so this WebUI page is the real
browser verification surface for the same contracts. Like
test_latch_plugin.py does for the native XAML/C#, these checks pin the
high-risk contracts at the source level: shared themed hasp mark, shell
theme tokens with shared persistence, keyboard access, honest state
surfaces, monotonic refresh-race handling, the read/propose-only action
boundary, handoff honesty and the no-fabricated-data rule. They read only
this track's files: the WebUI shell page, its script, its styles and the
hasp asset.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "haven" / "web" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
HASP = (STATIC / "latch-hasp.svg").read_text(encoding="utf-8")
DOCS = (ROOT / "docs" / "latch-plugin.md").read_text(encoding="utf-8")


def _latch_section() -> str:
    start = HTML.index('class="view view-latch"')
    end = HTML.index('class="view view-memory"')
    return HTML[start:end]


def _latch_app_section() -> str:
    start = APP.index("/* ---------- latch view: read + propose only ----------")
    end = APP.index("/* ---------- nav views ---------- */")
    return APP[start:end]


def _appearance_app_section() -> str:
    start = APP.index("/* ---------- appearance mode (System / Light / Dark) ----------")
    end = APP.index("/* ---------- latch hasp mark ----------")
    return APP[start:end]


def test_hasp_mark_draws_the_full_hinged_gate_latch_geometry():
    # Recognizable gate-latch/hasp: base plate, hinge barrel, strap, staple,
    # T-handle - explicitly not a door/bolt/padlock shape vocabulary.
    for part in ("hasp-base", "hasp-hinge", "hasp-strap", "hasp-staple", "hasp-handle"):
        assert part in HASP
    assert 'class="hasp-pin"' in HASP
    assert 'class="hasp-screw"' in HASP
    # The staple is a U-shaped receiver (open path), the handle a T lever.
    assert 'd="M 28.5 10.5 L 37.5 10.5 L 37.5 21.5 L 28.5 21.5"' in HASP
    for wrong in ("padlock", "shackle", "keyhole", "doorknob", "deadbolt", "bolt-head"):
        assert wrong not in HASP.lower()
    # Simplified mode is a first-class switch on the same vector geometry.
    assert ".simplified .hasp-full-only" in HASP


def test_hasp_is_themed_from_shell_tokens_not_a_competing_palette():
    # Every paint reference resolves against the shell's CSS custom
    # properties, in both themes.
    assert "var(--teal)" in HASP
    assert "var(--text)" in HASP
    assert "var(--bg-raise-2)" in HASP
    for line in HASP.splitlines():
        stripped = line.strip()
        if stripped.startswith(("fill:", "stroke:")) and "var(" not in stripped:
            raise AssertionError(f"hardcoded paint in hasp asset: {stripped}")


def test_same_hasp_serves_heading_full_and_navigation_simplified():
    assert 'data-latch-hasp="simplified"' in HTML
    assert 'data-latch-hasp="full"' in HTML
    assert HTML.index('data-latch-hasp="simplified"') < HTML.index('data-latch-hasp="full"')
    # One shared asset, injected by the shell; nav keeps its accessible name.
    assert "/latch-hasp.svg" in APP
    assert "injectLatchHasp" in APP
    assert 'svg.classList.add(\'simplified\')' in APP
    assert 'aria-label="Latch"' in HTML


def test_latch_added_to_existing_navigation_and_view_switching():
    assert 'data-view="latch"' in HTML
    assert ".center[data-view=\"latch\"] .view-latch" in CSS
    assert "if (view === 'latch') loadLatch();" in APP
    # The view is a real shell page: same center region, same card/tokens.
    section = _latch_section()
    assert 'role="tablist"' in section
    assert 'aria-label="Latch records"' in section


def test_appearance_control_uses_shared_persistence_only():
    # Light/Dark/System segmented control backed exclusively by the shared
    # appearance endpoint - no competing localStorage theme store, and the
    # WebUI never touches the settings file directly (the backend adapter
    # owns %LOCALAPPDATA%/Haven.Desktop/settings.json).
    assert 'data-appearance-mode="system"' in HTML
    assert 'data-appearance-mode="light"' in HTML
    assert 'data-appearance-mode="dark"' in HTML
    assert 'role="radiogroup"' in HTML
    assert "requestJSON('/api/latch/appearance'" in APP
    assert "method: 'PUT'" in APP
    assert "matchMedia('(prefers-color-scheme: light)')" in APP
    assert "applyAppearanceMode('system')" in APP  # system re-resolves on change
    # The appearance module itself must not touch a competing store (the
    # pre-existing notification opt-in elsewhere is unrelated to theme).
    appearance = _appearance_app_section()
    for forbidden in ("localStorage", "settings.json", "Haven.Desktop"):
        assert forbidden not in appearance
    # CSS: manual pins ride the SAME token blocks as the media query.
    assert ':root[data-appearance="light"]' in CSS
    assert ':root:not([data-appearance="dark"])' in CSS
    light_block = CSS[CSS.index(':root[data-appearance="light"]'):]
    light_block = light_block[: light_block.index("}") + 1]
    assert "--bg:" in light_block and "--text:" in light_block and "--teal:" in light_block


def test_keyboard_access_tabs_refresh_and_list_roving():
    for key, tab in (("1", "ledger"), ("2", "access"), ("3", "proposals")):
        assert f"{key}: '{tab}'" in APP
    assert "e.key === 'F5'" in APP
    assert "refreshLatch()" in APP
    assert "ArrowDown" in APP and "ArrowUp" in APP
    assert "selectLatchTab" in APP
    # Accessible names on the three tabs, mirroring the native page.
    section = _latch_section()
    assert 'id="latch-tab-ledger"' in section
    assert 'id="latch-tab-access"' in section
    assert 'id="latch-tab-proposals"' in section
    assert section.count('role="tab"') == 3


def test_state_surfaces_are_distinct_and_honest():
    section = _latch_section()
    for surface in (
        "latch-loading",
        "latch-empty",
        "latch-stale-banner",
        "latch-disconnected",
        "latch-error",
    ):
        assert surface in section
    assert "Loading Latch data" in section
    assert "No access currently needs review." in APP
    assert "No commitments recorded in Latch yet." in APP
    assert "No review proposals or action plans from Latch yet." in APP
    # Stale last-good: a failed refresh never blanks good data.
    assert "Showing last successful data from" in APP
    assert "Disconnected - showing last successful data from" in APP
    # Synthetic demo chip, driven only by the adapter config.
    assert "latch-demo-chip" in section
    assert "Synthetic demo" in section
    assert "els.latchDemoChip.hidden = !config.demo;" in APP
    assert "GET /api/latch" not in APP  # routes only, no fabricated fallback data


def test_refresh_and_details_races_are_monotonic_latest_wins():
    assert "refreshSeq: 0" in APP
    assert "detailsSeq: 0" in APP
    assert "const token = ++latchState.refreshSeq;" in APP
    assert "const token = ++latchState.detailsSeq;" in APP
    # A stale response is discarded untouched, never rendered over newer data.
    assert APP.count("token !== latchState.refreshSeq") >= 3
    assert "token !== latchState.detailsSeq" in APP


def test_action_boundary_is_read_and_propose_only():
    # The only mutating call the page makes is the idempotent proposal.
    assert "postJSONDetailed('/api/latch/actions/plan'" in APP
    latch = _latch_app_section()
    for forbidden in (
        "AuthorizeAsync", "ExecuteAsync", "VerifyAsync",
        '"/authorize"', '"/execute"', '"/verify"',
        "'/authorize'", "'/execute'", "'/verify'",
        "actions.authorize", "actions.execute", "actions.verify",
    ):
        assert forbidden not in latch
    # Repeated-click guard while a proposal is in flight, re-enabled after.
    assert "planInFlight: false" in APP
    assert "latchState.planInFlight = true;" in APP
    assert "button.disabled = false;" in APP
    # The response is shown with its ACTUAL returned status (idempotent plans
    # may already have advanced); the page never assumes "Proposed". The
    # adapter wraps the plan ({ok, plan}) so its status survives the shell's
    # HTTP envelope, and the page reads the wrapped record.
    assert "Say what Latch says, never assume" in APP
    assert "latchActionPresentation(latchTryString(plan, 'status'))" in APP
    assert "resp.plan && typeof resp.plan === 'object' ? resp.plan : resp" in APP


def test_action_states_stay_distinct_and_transport_success_is_not_verification():
    for state in ("proposed", "authorized", "executed", "needs_verification", "succeeded", "failed"):
        assert f"case '{state}'" in APP or f"case '{state}': return" in APP
    assert "'Executed — awaiting verification', complete: false" in APP
    assert "'Verified', complete: true" in APP
    assert "transport succeeded; verification pending - not complete" in APP
    assert "transport success is not verification - " in APP
    # Distinct lifecycle stages in action details.
    assert "latchActionStages" in APP
    assert "latch-stage-row" in APP
    # Status re-read on window return (focus / visibility), like the native
    # window-reactivation refresh.
    assert "visibilitychange" in APP
    assert "window.addEventListener('focus', onLatchWindowReturn)" in APP


def test_handoff_uses_real_availability_and_is_honest_when_unavailable():
    assert "requestJSON('/api/latch/handoff?plan_id=' + encodeURIComponent(planId))" in APP
    # Popup-blocker-safe handoff: the window opens synchronously from the
    # click (about:blank, no opener), is navigated to Latch's deep link only
    # after the real probe succeeds, and is closed when unavailable; a
    # blocked initial open is reported honestly.
    assert "window.open('about:blank', '_blank')" in APP
    assert "win.opener = null;" in APP
    assert "win.location.replace(resp.url)" in APP
    assert "win.close()" in APP
    assert "The browser blocked the handoff window" in APP
    # The probe must still run before any navigation, and only Latch's
    # ?plan_id= deep link is ever opened - never an authorize/execute/verify.
    assert "window.open(resp.url" not in APP
    assert "Latch UI unavailable" in APP
    assert "--web-root apps/desktop (plus --http-port 8787)" in APP
    # Proactive probe disables the visible button before the click.
    assert "probeLatchHandoff" in APP
    assert "latchState.handoffAvailable" in APP
    assert "?plan_id=" in DOCS


def test_real_latch_shapes_are_projected_not_flat_fields():
    # Commitments: economics.{amount_minor,currency,cadence},
    # temporal.{renewal_at,cancel_before}, confidence.overall, facts - read
    # from the record, never flattened into UI constants.
    assert "latchTryString(temporal, 'renewal_at')" in APP
    assert "latchTryString(temporal, 'cancel_before')" in APP
    assert "latchTryString(confidence, 'overall')" in APP
    assert "const economics = commitment && commitment.economics;" in APP
    # Access: grantee/resource entity labels, stale/stale_reason, connector fields.
    for field in ("stale_reason", "source_connector", "source_object_id", "last_used_at"):
        assert field in APP
    # Scheduler proposals: nested targets (access_grant_id/commitment_id) and
    # an evidence ARRAY of objects.
    assert "access_grant_id" in APP
    assert "commitment_id" in APP
    assert "evidenceId: latchTryString(entry, 'evidence_id')" in APP
    # The review screen asks Latch for what actually needs review.
    assert "requestJSON('/api/latch/access?stale_only=1')" in APP


def test_no_dates_are_computed_and_no_rows_are_fabricated():
    # Renewal/cancel-by instants render from Latch fields verbatim.
    assert "Latch-computed instants, passed through verbatim" in APP
    for computed in ("setDate(", "setMonth(", "setFullYear(", "Date.now() +", "addDays"):
        assert computed not in APP
    # No fabricated dashboard rows: no provider/content constants anywhere
    # in the track's files.
    for fake in ("Netflix", "Spotify", "Streamflix", "Adobe", "com_provider_"):
        for text in (APP, HTML, HASP):
            assert fake not in text
    # Connector-observation grants must not block on or invent evidence.
    assert "No backing evidence record - this grant is a connector observation." in APP
    assert "unavailable" in APP
    for fake in ("observed via", "Source: synthetic", "demo connector"):
        assert fake not in APP


def test_evidence_loading_is_bounded_and_honest():
    assert "?content=1" in APP
    assert "previewLimit = 1200" in APP
    assert "ids.slice(0, 25)" in APP
    assert "evidenceIds.slice(0, 10)" in APP
    # Unavailable evidence records say so with Latch's error, never dropped.
    assert "unavailable (" in APP


def test_startup_guidance_uses_the_full_latchd_command():
    # Every startup hint must carry the full form: a bare "--web-root" with
    # no path does not serve Latch's desktop UI.
    full_form = "--http-port 8787 --web-root apps/desktop"
    section = _latch_section()
    assert full_form in section
    assert full_form in APP
    assert "--web-root." not in section
    assert "--web-root)" not in section


def test_all_data_comes_from_the_adapter_routes():
    for route in (
        "/api/latch/config",
        "/api/latch/commitments",
        "/api/latch/access?stale_only=1",
        "/api/latch/schedules/proposals",
        "/api/latch/actions",
        "/api/latch/evidence/",
    ):
        assert route in APP
    # The list/inspector only renders what Latch served; a missing record
    # clears the selection instead of synthesizing a placeholder.
    assert "latchState.records.has(" in APP


def test_docs_distinguish_native_production_and_webui_verification():
    assert "WebUI" in DOCS
    assert "production" in DOCS.lower()
    assert "compatibility" in DOCS.lower()
    assert "HAVEN_LATCH_SETTINGS_PATH" in DOCS
    assert "/api/latch/appearance" in DOCS
    assert "--web-root apps/desktop" in DOCS
    assert "settings.json" in DOCS


def test_responsive_layout_and_contrast_tokens():
    assert "@media (max-width: 1100px)" in CSS
    assert "grid-template-columns: 1.5fr 1fr" in CSS
    # The latch view paints only from tokens (chips included).
    latch_css = CSS[CSS.index("/* ---------- latch view ---------- */"):]
    for line in latch_css.splitlines():
        stripped = line.strip()
        if ("color:" in stripped or "background:" in stripped) and "#" in stripped and "--" not in stripped:
            raise AssertionError(f"hardcoded color in latch styles: {stripped}")


def test_latch_view_borrows_the_rail_column_on_desktop_only():
    # At desktop widths the Latch view's ledger + inspector get the column
    # the conversation rail occupies on other views: the grid becomes
    # nav+center and the rail hides - scoped to the latch view via :has so
    # every other view keeps the rail.
    assert '.app:has(.center[data-view="latch"])' in CSS
    rail_borrow = '.app:has(.center[data-view="latch"]) .haven-rail'
    assert rail_borrow in CSS
    rail_rule = CSS[CSS.index(rail_borrow):]
    rail_rule = rail_rule[: rail_rule.index("}") + 1]
    assert "display: none" in rail_rule
    # Desktop-only: the phone single-column layout (rail as its own row)
    # must not be affected, and the 1280px icon-rail variant is preserved.
    borrow_start = CSS.index("/* ---------- latch view borrows the right rail's column")
    borrow_block = CSS[borrow_start:]
    assert "@media (min-width: 901px)" in borrow_block
    assert "@media (max-width: 1280px) and (min-width: 901px)" in borrow_block
    # The rail is never hidden unconditionally (other views keep it).
    for media_block in CSS.split("@media"):
        for chunk in media_block.split("}"):
            selector, _, body = chunk.partition("{")
            if "haven-rail" in selector and ":has(" not in selector and "display: none" in body:
                raise AssertionError(f"unconditional rail hide: {selector.strip()}")
    # The desktop template is two columns: nav + center, top/status span.
    assert '"nav    center"' in borrow_block
