"""Read bounded dist-info JSON without importing an extension's Python code."""

from __future__ import annotations

import json
from importlib import metadata


def entry_metadata(entry_point: metadata.EntryPoint, filename: str, schema: str) -> dict:
    dist = entry_point.dist
    try:
        raw = dist.read_text(filename) if dist is not None else None
    except OSError as exc:
        raise ValueError(f"static metadata unavailable: {filename}") from exc
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65536:
        raise ValueError(f"static metadata unavailable: {filename}; in-process inspection blocked")
    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"invalid static metadata: {filename}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != schema:
        raise ValueError(f"invalid static metadata schema: {filename}")
    entries = payload.get("entries")
    row = entries.get(entry_point.name) if isinstance(entries, dict) else None
    if not isinstance(row, dict):
        raise ValueError(f"static metadata missing entry: {entry_point.name}")
    return row
