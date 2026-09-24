"""Format-specific document adapters: bytes/text in, statement lines out.

Spec page 22's extraction order: DOCX, PDF, HTML, structured
JSON/YAML/TOML, CSV. Every adapter is optional-by-construction: DOCX,
HTML, JSON, TOML (tomllib) and CSV are stdlib; PDF (pypdf) and YAML
(PyYAML) are soft imports whose absence is reported through
`extraction_capabilities()` as an explicit NOMAD-style capability state --
never a silent skip and never a crash (spec section 18).

Adapters take already-read bytes/text: opening locators stays the
provider's job, exactly like the text pipeline in `content.py`. The
normalized statement lines flow into the existing `ExtractedContent` ->
`DocumentStatementExtractor` -> admission path unchanged; extraction
proposes candidates only.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable

_MAX_STATEMENTS = 400
_MAX_LINE_LENGTH = 500

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover - house floor is 3.12
    tomllib = None  # type: ignore[assignment]

try:
    import pypdf
except ImportError:
    pypdf = None  # type: ignore[assignment]

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


@dataclass(frozen=True)
class FormatExtraction:
    """One adapter's answer: statement lines plus an explicit capability."""

    format: str
    statements: tuple[str, ...]
    available: bool
    detail: str


def _unavailable(format: str, detail: str) -> FormatExtraction:
    return FormatExtraction(format=format, statements=(), available=False, detail=detail)


def _statements(format: str, lines) -> FormatExtraction:
    cleaned = [
        line.strip()
        for line in lines
        if isinstance(line, str) and line.strip() and len(line.strip()) <= _MAX_LINE_LENGTH
    ]
    return FormatExtraction(
        format=format, statements=tuple(cleaned[:_MAX_STATEMENTS]), available=True, detail="ok"
    )


# -- DOCX (stdlib: zipfile + ElementTree) ---------------------------------------


def extract_docx(data: bytes) -> FormatExtraction:
    """Paragraphs from word/document.xml; runs joined within a paragraph."""

    import xml.etree.ElementTree as ET

    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            raw = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        return _unavailable("docx", f"not a readable docx: {exc}")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return _unavailable("docx", f"document.xml is not well-formed: {exc}")
    lines = []
    for paragraph in root.iter(f"{namespace}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
        if text.strip():
            lines.append(text)
    return _statements("docx", lines)


# -- PDF (optional pypdf) ---------------------------------------------------------


def extract_pdf(data: bytes) -> FormatExtraction:
    if pypdf is None:
        return _unavailable("pdf", "the optional 'pypdf' package is not installed")
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        lines = []
        for page in reader.pages:
            lines.extend((page.extract_text() or "").splitlines())
    except Exception as exc:  # pypdf raises several parse-specific types
        return _unavailable("pdf", f"not a readable pdf: {exc}")
    return _statements("pdf", lines)


# -- HTML (stdlib html.parser) ------------------------------------------------------


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skipped_depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skipped_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skipped_depth > 0:
            self._skipped_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skipped_depth == 0 and data.strip():
            self.chunks.append(data)


def extract_html(text: str) -> FormatExtraction:
    parser = _VisibleTextParser()
    try:
        parser.feed(text)
    except Exception as exc:  # HTMLParser is lenient; this is defensive
        return _unavailable("html", f"not readable html: {exc}")
    return _statements("html", parser.chunks)


# -- structured JSON / YAML / TOML: key-path statements -------------------------------


def _walk_scalar_paths(value: Any, prefix: str, lines: list[str]) -> None:
    if isinstance(value, dict):
        for key in sorted(value, key=str):
            child = f"{prefix}.{key}" if prefix else str(key)
            _walk_scalar_paths(value[key], child, lines)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _walk_scalar_paths(item, f"{prefix}[{index}]", lines)
    elif value is None:
        lines.append(f"{prefix}: null")
    elif isinstance(value, bool):
        lines.append(f"{prefix}: {str(value).lower()}")
    elif isinstance(value, (int, float)):
        lines.append(f"{prefix}: {value}")
    else:
        lines.append(f"{prefix}: {value}")


def extract_json(text: str) -> FormatExtraction:
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        return _unavailable("json", f"not readable json: {exc}")
    lines: list[str] = []
    _walk_scalar_paths(parsed, "", lines)
    return _statements("json", lines)


def extract_yaml(text: str) -> FormatExtraction:
    if yaml is None:
        return _unavailable("yaml", "the optional 'PyYAML' package is not installed")
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return _unavailable("yaml", f"not readable yaml: {exc}")
    lines: list[str] = []
    _walk_scalar_paths(parsed, "", lines)
    return _statements("yaml", lines)


def extract_toml(text: str) -> FormatExtraction:
    if tomllib is None:
        return _unavailable("toml", "the stdlib 'tomllib' module is unavailable on this Python")
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return _unavailable("toml", f"not readable toml: {exc}")
    lines: list[str] = []
    _walk_scalar_paths(parsed, "", lines)
    return _statements("toml", lines)


# -- CSV (stdlib csv): header + one statement per row ----------------------------------


def extract_csv(text: str) -> FormatExtraction:
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        return _unavailable("csv", f"not readable csv: {exc}")
    if not rows:
        return _statements("csv", [])
    header = [column.strip() for column in rows[0]]
    lines = [", ".join(header)]
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        pairs = []
        for index, cell in enumerate(row):
            name = header[index] if index < len(header) and header[index] else f"column {index + 1}"
            pairs.append(f"{name}: {cell.strip()}")
        lines.append(", ".join(pairs))
    return _statements("csv", lines)


# -- the registry: explicit capability availability (NOMAD honesty) ----------------------


_FORMAT_SUFFIXES = {
    ".docx": ("docx", extract_docx),
    ".pdf": ("pdf", extract_pdf),
    ".htm": ("html", extract_html),
    ".html": ("html", extract_html),
    ".json": ("json", extract_json),
    ".yaml": ("yaml", extract_yaml),
    ".yml": ("yaml", extract_yaml),
    ".toml": ("toml", extract_toml),
    ".csv": ("csv", extract_csv),
}

_TEXT_FORMATS = frozenset({"html", "json", "yaml", "toml", "csv"})


def probe_format(suffix: str, *, data: bytes, text: str | None) -> FormatExtraction:
    """Run one adapter; binary formats use `data`, text formats use `text`."""

    entry = _FORMAT_SUFFIXES.get(suffix)
    if entry is None:
        return _unavailable(suffix.lstrip(".") or "unknown", f"no extractor for {suffix!r}")
    name, adapter = entry
    if name in _TEXT_FORMATS:
        if text is None:
            return _unavailable(name, "no text reader supplied for a text format")
        return adapter(text)
    return adapter(data)


def extraction_capabilities() -> tuple[dict, ...]:
    """Every adapter's availability, soft dependencies resolved by import."""

    capabilities = []
    for suffix in sorted(set(_FORMAT_SUFFIXES)):
        name = _FORMAT_SUFFIXES[suffix][0]
        if any(existing["format"] == name for existing in capabilities):
            continue
        if name == "pdf":
            available, detail = (pypdf is not None), (
                "pypdf available" if pypdf is not None else "the optional 'pypdf' package is not installed"
            )
        elif name == "yaml":
            available, detail = (yaml is not None), (
                "PyYAML available" if yaml is not None else "the optional 'PyYAML' package is not installed"
            )
        elif name == "toml":
            available, detail = (tomllib is not None), (
                "stdlib tomllib available" if tomllib is not None else "tomllib unavailable on this Python"
            )
        else:
            available, detail = True, "stdlib"
        capabilities.append({"format": name, "available": available, "detail": detail})
    return tuple(capabilities)


__all__ = [
    "FormatExtraction",
    "extraction_capabilities",
    "extract_csv",
    "extract_docx",
    "extract_html",
    "extract_json",
    "extract_pdf",
    "extract_toml",
    "extract_yaml",
    "probe_format",
]
