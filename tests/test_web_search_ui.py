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
    assert 'Choose folder' in app
    assert "/api/desktop/pick-folder" in app
    assert "setComposerMode" in app
    assert "runSearch" in app
    assert "'/api/search?q=' + encodeURIComponent(text)" in app
    assert "matched_claims" in app
