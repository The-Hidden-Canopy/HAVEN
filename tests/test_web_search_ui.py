"""The universal HAVEN composer exposes the life-search path in the shell."""

from pathlib import Path


STATIC_ROOT = Path(__file__).parents[1] / "haven" / "web" / "static"


def test_composer_exposes_ask_and_find_modes_with_search_results_surface():
    index = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    app = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'id="chat-input"' in index
    assert 'placeholder="Ask HAVEN or find anything' in index
    assert 'id="search-mode"' in index
    assert 'id="search-results-panel"' in index
    assert "3: { title: 'You'" in app
    assert "4: { title: 'This computer'" in app
    assert "5: { title: 'Connections'" in app
    assert "6: { title: 'Intelligence & voice'" in app
    assert "renderSetupConnections" in app
    assert 'Choose folder' in app
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
