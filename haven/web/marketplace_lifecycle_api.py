"""Narrow callable composition surface for local product HTTP and IPC clients."""

from __future__ import annotations

from typing import Any, Mapping

from ..marketplace.lifecycle import LocalProductManager, ProductError


def product_action(manager: LocalProductManager, action: str, params: Mapping[str, Any], *, principal_id: str) -> dict[str, Any]:
    """The caller supplies authenticated identity; request identity is ignored."""
    try:
        product_id = params.get("product_id")
        if action == "download":
            result = manager.download(product_id, params.get("version"), principal_id=principal_id, operation_id=params.get("operation_id"))
        elif action in {"install", "update", "rollback"}:
            method = manager.rollback if action == "rollback" else manager.install
            result = method(product_id, params.get("version"), principal_id=principal_id)
        elif action in {"enable", "disable"}:
            result = manager.set_enabled(product_id, action == "enable", principal_id=principal_id)
        elif action == "launch":
            result = manager.launch(product_id, principal_id=principal_id)
        elif action == "uninstall":
            result = manager.uninstall(product_id, principal_id=principal_id, data_policy=params.get("data_policy", "preserve"))
        elif action == "cancel":
            result = manager.cancel(params.get("operation_id"), principal_id=principal_id)
        else:
            raise ProductError("action_invalid", "unknown local product action")
        return {"ok": True, "result": result}
    except ProductError as exc:
        return {"ok": False, "error": exc.code, "message": str(exc)}
    except (TypeError, ValueError):
        return {"ok": False, "error": "request_invalid", "message": "invalid local product request"}
    except OSError:
        return {"ok": False, "error": "storage_unavailable", "message": "local product storage is unavailable; previous installation is preserved"}


__all__ = ["product_action"]
