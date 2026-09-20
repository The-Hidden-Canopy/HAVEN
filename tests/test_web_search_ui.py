"""The universal HAVEN composer exposes the life-search path in the shell."""

from pathlib import Path


STATIC_ROOT = Path(__file__).parents[1] / "haven" / "web" / "static"


def test_composer_exposes_ask_and_find_modes_with_search_results_surface():
    index = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    app = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'id="chat-input"' in index
    assert 'placeholder="Ask HAVEN or find anything' in index
    assert 'id="search-mode"' in index
    assert 'id="composer-feedback"' in index
    assert 'id="search-results-panel"' in index
    assert "3: { title: 'You'" in app
    assert "4: { title: 'This computer'" in app
    assert "5: { title: 'Connections'" in app
    assert "6: { title: 'Intelligence & voice'" in app
    assert "renderSetupConnections" in app
    assert 'Browse for folder' in app
    assert "/api/host/capabilities" in app
    assert "/api/host/pick-folder" in app
    assert "/api/desktop/pick-folder" not in app
    assert "Allow HAVEN to read/index these folders" in app
    assert "Allow HAVEN to organize files" in app
    assert "setComposerMode" in app
    assert "runSearch" in app
    assert "'/api/search?q=' + encodeURIComponent(text)" in app
    assert "matched_claims" in app
    assert "filesystem.open" in app
    assert "filesystem.reveal" in app
    assert "user selected this resource from HAVEN search" in app
    assert "postJSONDetailed" in app
    assert "runResourceAction" in app
    assert "Could not ' + idleLabel.toLowerCase()" in app
    assert "search-result-action-feedback" in app
    assert "HAVEN could not reach the local service." in app
    assert "button.textContent = idleLabel + 'ed ✓'" in app
    assert "postJSONDetailed('/api/chat'" in app
    assert "showComposerFeedback" in app


def test_main_surface_exposes_authoring_for_rooms_people_contexts_and_automations():
    index = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    app = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    for element_id in ("rooms-add", "people-add", "contexts-add", "automations-add", "authoring-panel"):
        assert f'id="{element_id}"' in index
    for route in ("/api/rooms", "/api/people", "/api/contexts", "/api/automations"):
        assert route in app
    assert "openAuthoringDialog" in app
    assert "patchJSONDetailed" in app
    assert "deleteJSONDetailed" in app
    assert "owner must approve it before the scheduler can act" in app
