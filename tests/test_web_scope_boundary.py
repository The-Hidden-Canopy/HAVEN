"""The compatibility HTTP surface cannot turn a scope filter into access."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from haven.knowledge import Claim, ClaimProvenance, ClaimState
from haven.resources import ResourceRecord
from haven.web.server import make_server

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


@contextmanager
def _boot(data_dir: Path):
    server, _ = make_server(0, data_dir=data_dir, clock=lambda: NOW)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(port: int, path: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    body = json.loads(response.read().decode("utf-8"))
    status = response.status
    connection.close()
    return status, body


def _post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request(
        "POST",
        path,
        body=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    response = connection.getresponse()
    body = json.loads(response.read().decode("utf-8"))
    status = response.status
    connection.close()
    return status, body


def test_http_search_rejects_an_invisible_scope_instead_of_returning_secret_hits():
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(Path(tmp) / "data") as (server, port):
            server.resources.save(
                ResourceRecord(
                    resource_id="file:visible",
                    resource_type="file",
                    scope_id=server.director.household_id,
                    provider_id="local_computer",
                    title="visible notes",
                    locator=None,
                    capabilities=(),
                    observed_at=NOW,
                )
            )
            server.resources.save(
                ResourceRecord(
                    resource_id="file:secret",
                    resource_type="file",
                    scope_id="secret-project",
                    provider_id="local_computer",
                    title="secret notes",
                    locator=None,
                    capabilities=(),
                    observed_at=NOW,
                )
            )

            status, body = _get(port, "/api/search?q=notes&scope=secret-project")

            assert status == 403
            assert body["ok"] is False
            assert "not visible" in body["error"]


def test_http_knowledge_cannot_read_or_mutate_an_invisible_claim():
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(Path(tmp) / "data") as (server, port):
            secret_id = "claim:secret"
            server.claims.save(
                Claim(
                    claim_id=secret_id,
                    scope_id="secret-project",
                    proposition="secret proposition",
                    state=ClaimState.REPORTED,
                    source_refs=(),
                    evidence_refs=(),
                    created_at=NOW,
                    provenance=ClaimProvenance.USER_REPORTED,
                )
            )

            status, body = _get(port, "/api/knowledge/claims?scope=secret-project")
            assert status == 403
            assert body["ok"] is False

            status, body = _get(port, f"/api/knowledge/claims/{quote(secret_id, safe='')}")
            assert status == 404
            assert body["ok"] is False

            status, body = _post(
                port,
                f"/api/knowledge/claims/{quote(secret_id, safe='')}/stale",
                {},
            )
            assert status == 404
            assert body["ok"] is False
            assert server.claims.get(secret_id).state is ClaimState.REPORTED
