"""Format extractors: statements become DOCUMENT_STATED candidates; optional
libraries degrade to explicit unavailable states, never crashes."""

from __future__ import annotations

import io
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.knowledge import KnowledgeService
from haven.knowledge.claims import ClaimProvenance
from haven.knowledge.extraction import extraction_capabilities, extract_document_content
from haven.knowledge.extraction import formats
from haven.knowledge.store import ClaimStore
from haven.resources import ResourceRecord
from haven.resources.store import ResourceStore

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)

DOCX_XML = """<?xml version="1.0"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>The launch date is October 1.</w:t></w:r></w:p>
    <w:p><w:r><w:t>The proposal concerns lunar site preparation.</w:t></w:r></w:p>
  </w:body>
</w:document>"""

HTML = """<html><head><title>t</title><style>body { color: red }</style>
<script>var secret = 1;</script></head>
<body><h1>UNLV Proposal</h1><p>The review board meets on Friday.</p></body></html>"""

JSON_DOC = '{"project": {"name": "UNLV Proposal", "budget": 120000, "lead": "Gerron"}}'
TOML_DOC = '[project]\nname = "VANTA Engine"\nbudget = 90000\n'
CSV_DOC = "name,role\nGerron,lead\nAda,reviewer\n"


def _docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", DOCX_XML)
    return buffer.getvalue()


def _resource(tmp_path: Path, name: str, scope_id: str = "scope:personal") -> ResourceRecord:
    return ResourceRecord(
        resource_id=f"file:{name}",
        resource_type="file",
        scope_id=scope_id,
        provider_id="local_filesystem",
        title=name,
        locator=str(tmp_path / name),
        capabilities=(),
        observed_at=NOW,
    )


def _write(tmp_path: Path, name: str, data) -> Path:
    path = tmp_path / name
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    return path


def _service(tmp_path: Path):
    resources = ResourceStore(Path(tmp_path) / "resources.db")
    claims = ClaimStore(Path(tmp_path) / "claims.db")
    service = KnowledgeService(resources=resources, claims=claims, clock=lambda: NOW)
    return service, claims


@pytest.mark.parametrize(
    "name,data,needle",
    (
        ("notes.docx", _docx_bytes(), "lunar site preparation"),
        ("page.html", HTML, "review board meets"),
        ("data.json", JSON_DOC, "UNLV Proposal"),
        ("config.toml", TOML_DOC, "VANTA Engine"),
        ("people.csv", CSV_DOC, "Ada"),
    ),
)
def test_format_statements_become_document_stated_candidates(tmp_path, name, data, needle) -> None:
    _write(tmp_path, name, data)
    service, claims = _service(tmp_path)
    resource = _resource(tmp_path, name)

    result = service.ingest_resource(
        resource,
        reader=lambda record: Path(record.locator).read_text(encoding="utf-8", errors="replace")
        if not record.locator.endswith((".docx", ".pdf"))
        else None,
        bytes_reader=lambda record: Path(record.locator).read_bytes()
        if record.locator.endswith((".docx", ".pdf"))
        else None,
    )

    assert result.unavailable == ()
    assert result.admitted > 0
    propositions = [claim.proposition for claim in claims.list_all()]
    assert any(needle in proposition for proposition in propositions)
    assert all(claim.provenance is ClaimProvenance.DOCUMENT_STATED for claim in claims.list_all())
    # Structured statements keep their key paths as line evidence.
    if name == "data.json":
        claim = next(c for c in claims.list_all() if "UNLV Proposal" in c.proposition)
        assert any("#line:" in ref for ref in claim.evidence_refs)


def test_pdf_reports_unavailable_without_pypdf(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(formats, "pypdf", None)
    _write(tmp_path, "paper.pdf", b"%PDF-1.4 not really a pdf")
    service, _claims = _service(tmp_path)

    result = service.ingest_resource(
        _resource(tmp_path, "paper.pdf"),
        reader=lambda record: None,
        bytes_reader=lambda record: Path(record.locator).read_bytes(),
    )

    assert result.admitted == 0
    assert len(result.unavailable) == 1
    assert "pypdf" in result.unavailable[0]


def test_yaml_reports_unavailable_without_pyyaml(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(formats, "yaml", None)
    _write(tmp_path, "config.yaml", "name: VANTA\n")
    service, _claims = _service(tmp_path)

    result = service.ingest_resource(
        _resource(tmp_path, "config.yaml"),
        reader=lambda record: Path(record.locator).read_text(encoding="utf-8"),
    )

    assert result.admitted == 0
    assert "PyYAML" in result.unavailable[0]


def test_capability_registry_reports_soft_dependencies() -> None:
    capabilities = {item["format"]: item for item in extraction_capabilities()}
    assert set(capabilities) == {"csv", "docx", "html", "json", "pdf", "toml", "yaml"}
    assert capabilities["docx"]["available"] is True
    assert capabilities["html"]["available"] is True
    # Whatever the host has installed, the state is explicit, never absent.
    assert isinstance(capabilities["pdf"]["available"], bool)
    assert capabilities["pdf"]["detail"]
    assert isinstance(capabilities["yaml"]["available"], bool)


def test_extract_document_content_stays_behind_the_reader_boundary(tmp_path) -> None:
    _write(tmp_path, "data.json", JSON_DOC)
    resource = _resource(tmp_path, "data.json")

    def refusing_reader(record):
        return None

    outcome = extract_document_content(
        resource, reader=refusing_reader, bytes_reader=lambda record: None, now=NOW
    )
    assert outcome is not None
    # Nothing crossed the boundary without the provider: no content, and the
    # capability state names why, instead of silently skipping.
    assert outcome.content is None
    assert outcome.extraction.available is False
    assert "text reader" in outcome.extraction.detail


def test_stdlib_core_operates_with_optional_libs_absent(tmp_path, monkeypatch) -> None:
    """Blocking both soft imports must not break text/json/toml/csv/html/docx."""
    monkeypatch.setattr(formats, "pypdf", None)
    monkeypatch.setattr(formats, "yaml", None)
    _write(tmp_path, "notes.toml", TOML_DOC)
    _write(tmp_path, "plain.txt", "The plain text pipeline still works today.")
    service, claims = _service(tmp_path)
    reader = lambda record: Path(record.locator).read_text(encoding="utf-8")  # noqa: E731

    toml_result = service.ingest_resource(_resource(tmp_path, "notes.toml"), reader=reader)
    text_result = service.ingest_resource(_resource(tmp_path, "plain.txt"), reader=reader)

    assert toml_result.admitted > 0
    assert text_result.admitted > 0
    assert not any("pypdf" in item for item in toml_result.unavailable)
