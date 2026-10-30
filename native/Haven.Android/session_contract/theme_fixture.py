"""Fixture for a proposed read-only theme catalog component; no server route."""
import json
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[1] / "theme-catalog.json"

def read_catalog():
    payload=json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if payload.get("schema") != "haven-theme-catalog-1": raise ValueError("unsupported catalog")
    return payload
