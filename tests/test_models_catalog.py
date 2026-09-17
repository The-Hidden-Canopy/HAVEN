"""Catalog files are curated pointers to manifest URLs; HAVEN ships none.
"""

import json
import tempfile
from pathlib import Path

import pytest

from haven.models import CatalogEntry, CatalogError, ModelKind, default_catalog, load_catalog


def _write(tmp: str, payload) -> Path:
    path = Path(tmp) / "catalog.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


VALID = {
    "entries": [
        {
            "id": "wake-tiny",
            "kind": "speech",
            "manifest_url": "https://models.example.com/wake-tiny/haven-model.json",
            "description": "Tiny wake-word model",
            "size_hint_mb": 2,
            "license": "Apache-2.0",
        }
    ]
}


def test_load_catalog_parses_entries():
    with tempfile.TemporaryDirectory() as tmp:
        entries = load_catalog(_write(tmp, VALID))
        assert entries == (
            CatalogEntry(
                id="wake-tiny",
                kind=ModelKind.SPEECH,
                manifest_url="https://models.example.com/wake-tiny/haven-model.json",
                description="Tiny wake-word model",
                size_hint_mb=2,
                license="Apache-2.0",
            ),
        )


def test_load_catalog_accepts_a_bare_list():
    with tempfile.TemporaryDirectory() as tmp:
        entries = load_catalog(_write(tmp, VALID["entries"]))
        assert len(entries) == 1


def test_non_http_manifest_url_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        bad = json.loads(json.dumps(VALID))
        bad["entries"][0]["manifest_url"] = "ftp://example.com/m.json"
        with pytest.raises(CatalogError, match="http"):
            load_catalog(_write(tmp, bad))


def test_unknown_kind_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        bad = json.loads(json.dumps(VALID))
        bad["entries"][0]["kind"] = "robotics"
        with pytest.raises(CatalogError, match="robotics"):
            load_catalog(_write(tmp, bad))


def test_malformed_catalog_raises_typed_error():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "catalog.json"
        path.write_text("{ nope", encoding="utf-8")
        with pytest.raises(CatalogError, match="JSON"):
            load_catalog(path)
        with pytest.raises(CatalogError, match="not found"):
            load_catalog(Path(tmp) / "missing.json")


def test_default_catalog_is_empty():
    assert default_catalog() == ()
