"""Model Manager API payloads: ModelManager results -> wire shapes.

Handlers stay thin: each endpoint calls one or two ModelManager methods and
these helpers turn records, inspections, and discovery results into the
ok-envelope JSON the UI consumes. Operational failures are reported as
``{"ok": false, "error": ...}`` with HTTP 200 -- never a traceback.
"""

from __future__ import annotations

from typing import Any

from ..models import (
    CatalogEntry,
    DiscoveryResult,
    ModelManager,
    ModelManagerError,
    ModelRecord,
    UrlInspection,
)


def model_row(record: ModelRecord) -> dict[str, Any]:
    descriptor = record.descriptor
    return {
        "id": record.id,
        "kind": record.kind.value,
        "state": record.state.value,
        "source": record.source_type.value,
        "capabilities": sorted(descriptor.capabilities),
        "backend": descriptor.backend,
        "architecture": descriptor.architecture,
        "version": descriptor.version,
        "license": descriptor.license,
        "languages": sorted(descriptor.languages),
        "registered_at": record.registered_at.isoformat(),
        "loaded_backend": record.loaded_backend,
    }


def _model_rows(manager: ModelManager) -> list[dict[str, Any]]:
    return [model_row(record) for record in manager.list_models()]


def _root_strings(manager: ModelManager) -> list[str]:
    return [str(root) for root in manager.roots()]


def _backend_rows(manager: ModelManager) -> list[dict[str, Any]]:
    return [
        {"backend": item.backend, "available": item.available, "detail": item.detail}
        for item in manager.backend_availability()
    ]


def models_payload(manager: ModelManager) -> dict[str, Any]:
    return {
        "ok": True,
        "models": _model_rows(manager),
        "roots": _root_strings(manager),
        "backends": _backend_rows(manager),
        "assignments": manager.assignments(),
    }


def assign_payload(manager: ModelManager, role, model_id) -> dict[str, Any]:
    """POST /api/models/assign: role -> model_id (None clears).

    A rejected assignment answers ok:false with the reason (unknown role,
    unknown model, or a model whose kind/capabilities cannot serve the
    role); a successful one answers with the fresh models+backends payload,
    the same refetch shape every other mutation returns.
    """

    try:
        manager.assign(role, model_id)
    except ModelManagerError as exc:
        return {"ok": False, "error": str(exc)}
    return models_payload(manager)


def overview_payload(manager: ModelManager) -> dict[str, Any]:
    payload = models_payload(manager)
    payload["catalog"] = [catalog_row(entry) for entry in manager.search()]
    return payload


def catalog_row(entry: CatalogEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "kind": entry.kind.value,
        "manifest_url": entry.manifest_url,
        "description": entry.description,
        "size_hint_mb": entry.size_hint_mb,
        "license": entry.license,
    }


def inspection_payload(inspection: UrlInspection) -> dict[str, Any]:
    manifest = inspection.manifest
    return {
        "id": manifest.id,
        "kind": manifest.kind.value,
        "backend": inspection.backend,
        "architecture": manifest.architecture,
        "version": manifest.version,
        "license": inspection.license,
        "languages": sorted(inspection.languages),
        "file_count": inspection.file_count,
        "hash_verification": inspection.hash_verification,
        "remote_code": inspection.remote_code,
    }


def discovered_row(result: DiscoveryResult) -> dict[str, Any]:
    return {
        "path": str(result.path),
        "state": result.state.value,
        "problems": list(result.problems),
        "id": result.manifest.id if result.manifest else None,
        "kind": result.manifest.kind.value if result.manifest else None,
    }


def scan_payload(manager: ModelManager, discovered: list[DiscoveryResult]) -> dict[str, Any]:
    payload = models_payload(manager)
    payload["discovered"] = [discovered_row(result) for result in discovered]
    return payload


__all__ = [
    "assign_payload",
    "catalog_row",
    "discovered_row",
    "inspection_payload",
    "model_row",
    "models_payload",
    "overview_payload",
    "scan_payload",
]
