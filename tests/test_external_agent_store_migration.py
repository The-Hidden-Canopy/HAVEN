"""ExternalAgentStore schema transitions with a *populated* database.

`test_external_agent_store.py` already covers version refusal on an empty
file; these tests cover the scenarios WP1 left open: a database holding real
connections/bindings/subjects/audit rows must survive a close/reopen, must be
adopted (not discarded) when it predates the schema_version marker, and a
downgrade refusal must leave every row byte-for-byte intact. There is no v2
schema yet, so the "upgrade" scenario available today is adoption of a
pre-versioning file; a future v2 migration test belongs here too.
"""

import sqlite3
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
        credential_hash="hash-abc",
        unbound_scopes=frozenset({ExternalScope.WORLD_READ}),
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
        scopes=frozenset({ExternalScope.WORLD_READ, ExternalScope.ACTIONS_REQUEST}),
        created_by="owner-1",
        created_at=NOW,
        expires_at=NOW + timedelta(days=30),
    )
    defaults.update(overrides)
    return PrincipalBinding(**defaults)


def _populate(store: ExternalAgentStore) -> None:
    """One row in every table, with every optional field exercised."""
    store.save_connection(_connection())
    store.save_connection(_connection(connection_id="extconn-2", credential_hash=None, revoked_at=NOW))
    store.save_binding(_binding())
    store.save_binding(_binding(binding_id="bind-2", subject_key="subj-hash-2", revoked_at=NOW))
    store.observe_subject("extconn-1", "subj-hash-1", "Gerron's voice profile", NOW)
    store.append_audit(
        audit_id="a1",
        household_id="household-1",
        kind="connection.created",
        connection_id="extconn-1",
        actor="owner-1",
        detail={"provider": "alexa_plus"},
        occurred_at=NOW,
    )


def _raw_counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(path))
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("connections", "bindings", "observed_subjects", "audit")
        }
    finally:
        conn.close()


def _schema_version(path: Path) -> str | None:
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _assert_populated_rows_readable(store: ExternalAgentStore) -> None:
    connections = store.list_connections("household-1")
    assert [c.connection_id for c in connections] == ["extconn-1", "extconn-2"]
    assert connections[0] == _connection()
    assert connections[1].revoked_at == NOW
    assert store.connection_for_credential("hash-abc").connection_id == "extconn-1"

    assert store.get_binding("bind-1") == _binding()
    assert [b.binding_id for b in store.list_bindings("extconn-1")] == ["bind-1"]
    assert {b.binding_id for b in store.list_bindings("extconn-1", include_revoked=True)} == {"bind-1", "bind-2"}
    assert store.active_binding("extconn-1", "subj-hash-1").binding_id == "bind-1"
    assert store.active_binding("extconn-1", "subj-hash-2") is None  # revoked stays revoked

    subjects = store.observed_subjects("extconn-1")
    assert [row["subject_key"] for row in subjects] == ["subj-hash-1"]

    audit = store.audit("household-1")
    assert [row["audit_id"] for row in audit] == ["a1"]
    assert audit[0]["detail"] == {"provider": "alexa_plus"}


def test_populated_database_survives_close_and_reopen() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "external_agents.db"
        _populate(ExternalAgentStore(path))

        reopened = ExternalAgentStore(path)

        _assert_populated_rows_readable(reopened)
        assert _schema_version(path) == str(SCHEMA_VERSION)


def test_pre_versioning_populated_database_is_adopted_and_stamped() -> None:
    """A file written before the version marker existed upgrades in place.

    Simulated by populating a current-schema database and then deleting the
    meta row: the next open must stamp the current version and read every
    pre-existing row back unchanged -- adoption, never re-initialization.
    """

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "external_agents.db"
        _populate(ExternalAgentStore(path))
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("DELETE FROM meta WHERE key = 'schema_version'")
            conn.commit()
        finally:
            conn.close()
        assert _schema_version(path) is None

        adopted = ExternalAgentStore(path)

        assert _schema_version(path) == str(SCHEMA_VERSION)
        _assert_populated_rows_readable(adopted)


def test_newer_schema_refusal_leaves_a_populated_database_untouched() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "external_agents.db"
        _populate(ExternalAgentStore(path))
        before = _raw_counts(path)
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION + 1),))
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(RuntimeError, match="newer than this HAVEN supports"):
            ExternalAgentStore(path)

        # Fail-closed means refused, not truncated: every row survives so a
        # newer HAVEN can still open the file later.
        assert _raw_counts(path) == before
        assert _schema_version(path) == str(SCHEMA_VERSION + 1)


def test_newer_store_opens_a_refused_database_with_data_intact(monkeypatch) -> None:
    """The other half of downgrade safety: the file the current build refused
    is still a valid database for the version that wrote it."""

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "external_agents.db"
        _populate(ExternalAgentStore(path))
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION + 1),))
            conn.commit()
        finally:
            conn.close()
        with pytest.raises(RuntimeError):
            ExternalAgentStore(path)

        monkeypatch.setattr("haven.external_agents.store.SCHEMA_VERSION", SCHEMA_VERSION + 1)
        upgraded = ExternalAgentStore(path)

        _assert_populated_rows_readable(upgraded)
