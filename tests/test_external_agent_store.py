"""ExternalAgentStore: durable connections/bindings, revocation, audit retention."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from haven.external_agents.domain import ExternalAgentConnection, ExternalProvider, ExternalScope, PrincipalBinding
from haven.external_agents.store import SCHEMA_VERSION, ExternalAgentStore

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def _connection(**overrides) -> ExternalAgentConnection:
    defaults = dict(
        connection_id="extconn-1",
        household_id="household-1",
        provider=ExternalProvider.ALEXA_PLUS,
        display_name="Alexa+",
        enabled=True,
        created_at=NOW,
        created_by="owner-1",
    )
    defaults.update(overrides)
    return ExternalAgentConnection(**defaults)


def _binding(**overrides) -> PrincipalBinding:
    defaults = dict(
        binding_id="bind-1",
        connection_id="extconn-1",
        subject_key="subj-hash-1",
        subject_label="Gerron's voice profile",
        principal_id="person-gerron",
        scopes=frozenset({ExternalScope.WORLD_READ}),
        created_by="owner-1",
        created_at=NOW,
    )
    defaults.update(overrides)
    return PrincipalBinding(**defaults)


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ExternalAgentStore(Path(tmp) / "external_agents.db")


def test_save_and_get_connection_round_trips(store):
    store.save_connection(_connection())

    loaded = store.get_connection("extconn-1")

    assert loaded == _connection()


def test_get_unknown_connection_returns_none(store):
    assert store.get_connection("nope") is None


def test_list_connections_is_household_scoped_and_ordered(store):
    store.save_connection(_connection(connection_id="a", household_id="h1", created_at=NOW))
    store.save_connection(_connection(connection_id="b", household_id="h1", created_at=NOW + timedelta(seconds=1)))
    store.save_connection(_connection(connection_id="c", household_id="h2"))

    found = store.list_connections("h1")

    assert [c.connection_id for c in found] == ["a", "b"]


def test_save_connection_upserts_by_id(store):
    store.save_connection(_connection(enabled=True))
    store.save_connection(_connection(enabled=False))

    assert store.get_connection("extconn-1").enabled is False


def test_connection_for_credential_finds_by_hash(store):
    store.save_connection(_connection(credential_hash="hash-abc"))

    found = store.connection_for_credential("hash-abc")

    assert found is not None
    assert found.connection_id == "extconn-1"


def test_connection_for_credential_returns_none_when_absent(store):
    store.save_connection(_connection(credential_hash=None))

    assert store.connection_for_credential("hash-abc") is None


def test_two_connections_cannot_share_a_credential_hash(store):
    store.save_connection(_connection(connection_id="a", credential_hash="dupe"))

    with pytest.raises(Exception):
        store.save_connection(_connection(connection_id="b", credential_hash="dupe"))


def test_save_and_get_binding_round_trips(store):
    store.save_connection(_connection())
    store.save_binding(_binding())

    loaded = store.get_binding("bind-1")

    assert loaded == _binding()


def test_active_binding_returns_none_when_revoked(store):
    store.save_connection(_connection())
    store.save_binding(_binding(revoked_at=NOW))

    assert store.active_binding("extconn-1", "subj-hash-1") is None


def test_active_binding_returns_the_newest_unrevoked_row(store):
    store.save_connection(_connection())
    store.save_binding(_binding(binding_id="old", created_at=NOW))
    store.save_binding(_binding(binding_id="new", created_at=NOW + timedelta(minutes=1)))

    found = store.active_binding("extconn-1", "subj-hash-1")

    assert found.binding_id == "new"


def test_list_bindings_excludes_revoked_by_default(store):
    store.save_connection(_connection())
    store.save_binding(_binding(binding_id="live"))
    store.save_binding(_binding(binding_id="dead", subject_key="other", revoked_at=NOW))

    assert [b.binding_id for b in store.list_bindings("extconn-1")] == ["live"]
    assert {b.binding_id for b in store.list_bindings("extconn-1", include_revoked=True)} == {"live", "dead"}


def test_observe_subject_tracks_first_and_last_seen(store):
    store.save_connection(_connection())
    store.observe_subject("extconn-1", "subj-hash-1", "Guest voice", NOW)
    store.observe_subject("extconn-1", "subj-hash-1", "Guest voice", NOW + timedelta(minutes=5))

    rows = store.observed_subjects("extconn-1")

    assert len(rows) == 1
    assert rows[0]["first_seen_at"] == NOW.isoformat()
    assert rows[0]["last_seen_at"] == (NOW + timedelta(minutes=5)).isoformat()


def test_audit_append_and_read_newest_first(store):
    store.append_audit(audit_id="a1", household_id="h1", kind="connection.created", occurred_at=NOW)
    store.append_audit(
        audit_id="a2", household_id="h1", kind="cross_household", occurred_at=NOW + timedelta(seconds=1), security=True
    )

    rows = store.audit("h1")

    assert [r["audit_id"] for r in rows] == ["a2", "a1"]
    assert rows[0]["security"] is True
    assert rows[1]["security"] is False


def test_audit_is_household_scoped(store):
    store.append_audit(audit_id="a1", household_id="h1", kind="x", occurred_at=NOW)
    store.append_audit(audit_id="a2", household_id="h2", kind="x", occurred_at=NOW)

    assert [r["audit_id"] for r in store.audit("h1")] == ["a1"]


def test_audit_filters_by_connection(store):
    store.append_audit(audit_id="a1", household_id="h1", kind="x", occurred_at=NOW, connection_id="c1")
    store.append_audit(audit_id="a2", household_id="h1", kind="x", occurred_at=NOW, connection_id="c2")

    assert [r["audit_id"] for r in store.audit("h1", connection_id="c1")] == ["a1"]


def test_audit_retention_prunes_oldest_rows_per_household():
    with tempfile.TemporaryDirectory() as tmp:
        store = ExternalAgentStore(Path(tmp) / "external_agents.db")
        from haven.external_agents.store import AUDIT_RETENTION

        for i in range(AUDIT_RETENTION + 5):
            store.append_audit(
                audit_id=f"a{i}", household_id="h1", kind="x", occurred_at=NOW + timedelta(seconds=i)
            )

        rows = store.audit("h1", limit=AUDIT_RETENTION + 10)

        assert len(rows) == AUDIT_RETENTION
        assert rows[0]["audit_id"] == f"a{AUDIT_RETENTION + 4}"  # newest kept


def test_reopening_the_store_preserves_the_schema_version(store):
    path = store.path

    reopened = ExternalAgentStore(path)

    conn = reopened._connect()  # noqa: SLF001 -- verifying the meta row directly
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    finally:
        conn.close()
    assert int(row[0]) == SCHEMA_VERSION


def test_a_newer_schema_version_is_refused_not_overwritten():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "external_agents.db"
        store = ExternalAgentStore(path)
        conn = store._connect()  # noqa: SLF001
        try:
            conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION + 1),))
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(RuntimeError):
            ExternalAgentStore(path)
