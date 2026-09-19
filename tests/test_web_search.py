"""`GET /api/search` -- the life search bar's HTTP surface."""

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from haven.knowledge import ClaimState
from haven.ontology import OntologyAssertion, predicates
from haven.resources import ResourceRecord
from haven.web.server import make_server

UTC = timezone.utc
NOW = datetime(2026, 9, 18, 20, 0, tzinfo=UTC)


@contextmanager
def _boot(data_dir: str):
    instance, _ = make_server(0, data_dir=data_dir, clock=lambda: NOW)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance, instance.server_address[1]
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _get_json(port: int, path: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    body = json.loads(response.read().decode("utf-8"))
    status = response.status
    connection.close()
    return status, body


def test_search_requires_a_query_parameter():
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(str(Path(tmp) / "data")) as (_, port):
            status, body = _get_json(port, "/api/search")
    assert status == 200
    assert body["ok"] is False
    assert "q" in body["error"]


def test_search_with_no_resources_returns_no_hits():
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(str(Path(tmp) / "data")) as (_, port):
            status, body = _get_json(port, "/api/search?q=nasa")
    assert status == 200
    assert body["ok"] is True
    assert body["hits"] == []


def test_search_finds_a_saved_resource_and_its_related_resource():
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(str(Path(tmp) / "data")) as (instance, port):
            instance.resources.save(
                ResourceRecord(
                    resource_id="doc:proposal-v7",
                    resource_type="document",
                    scope_id="project:haven",
                    provider_id="local_computer",
                    title="NASA LIVEI proposal v7",
                    locator=None,
                    capabilities=(),
                    observed_at=NOW,
                )
            )
            instance.resources.save(
                ResourceRecord(
                    resource_id="repo:haven",
                    resource_type="repository",
                    scope_id="project:haven",
                    provider_id="local_computer",
                    title="haven",
                    locator=None,
                    capabilities=(),
                    observed_at=NOW,
                )
            )
            instance.ontology.save(
                OntologyAssertion(
                    assertion_id="a1",
                    subject="repo:haven",
                    predicate=predicates.BELONGS_TO,
                    object="doc:proposal-v7",
                    scope_id="project:haven",
                    state=ClaimState.OBSERVED,
                    created_at=NOW,
                )
            )

            status, body = _get_json(port, "/api/search?q=nasa")

    assert status == 200
    assert body["ok"] is True
    ids = {hit["resource_id"] for hit in body["hits"]}
    assert ids == {"doc:proposal-v7", "repo:haven"}
    direct = next(h for h in body["hits"] if h["resource_id"] == "doc:proposal-v7")
    assert direct["score"] == 1.0
    related = next(h for h in body["hits"] if h["resource_id"] == "repo:haven")
    assert related["score"] < 1.0


def test_search_survives_a_restart():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = str(Path(tmp) / "data")
        with _boot(data_dir) as (instance, _):
            instance.resources.save(
                ResourceRecord(
                    resource_id="doc:a",
                    resource_type="document",
                    scope_id="project:haven",
                    provider_id="local_computer",
                    title="nasa report",
                    locator=None,
                    capabilities=(),
                    observed_at=NOW,
                )
            )

        with _boot(data_dir) as (_, port):
            status, body = _get_json(port, "/api/search?q=nasa")
    assert status == 200
    assert [h["resource_id"] for h in body["hits"]] == ["doc:a"]
