"""Today attention projection: signals, ranking, card contract, dismissal."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from haven.ipc import request_message
from haven.models.jobs import DownloadJobManager
from haven.web.server import make_server

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def server():
    with tempfile.TemporaryDirectory() as tmp:
        instance, _director = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW)
        try:
            yield instance
        finally:
            instance.server_close()


def _dispatch(instance, method: str, params: dict) -> dict:
    return instance.build_ipc_dispatcher()(request_message(f"req-{method}", method, params))


def _seed(instance, tmp_path: Path) -> None:
    instance.setup.declare_person(name="Gerron Smith", role="owner")
    dispatcher = instance.build_ipc_dispatcher()
    project = dispatcher(request_message("p", "projects.create", {"title": "Lunar proposal"}))[
        "result"
    ]["project"]
    overdue = dispatcher(
        request_message(
            "t1",
            "tasks.create",
            {
                "title": "Overdue section draft",
                "project_id": project["project_id"],
                "due_at": (NOW - timedelta(hours=3)).isoformat(),
            },
        )
    )["result"]["task"]
    dispatcher(
        request_message(
            "t2",
            "tasks.create",
            {"title": "Dependent review", "project_id": project["project_id"], "dependency_ids": [overdue["task_id"]]},
        )
    )
    dispatcher(request_message("t3", "tasks.create", {"title": "Suggested outreach", "state": "proposed"}))
    (tmp_path / "cal.ics").write_text(
        "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:evt-1\nSUMMARY:Tomorrow review\n"
        f"DTSTART:{(NOW + timedelta(hours=20)).strftime('%Y%m%dT%H%M%S')}\nEND:VEVENT\nEND:VCALENDAR\n",
        encoding="utf-8",
    )
    dispatcher(request_message("c", "calendar.sources.add", {"path": str(tmp_path / "cal.ics")}))


def test_cards_ranking_and_contract(server, tmp_path) -> None:
    _seed(server, tmp_path)
    cards = _dispatch(server, "today.cards", {})["result"]["cards"]
    groups = [card["group"] for card in cards]
    assert groups, "seeded signals must produce cards"
    rank = {"authority": 0, "deadline": 1, "commitment": 2, "suggestion": 3}
    assert groups == sorted(groups, key=lambda group: rank[group])

    by_title = {card["title"]: card for card in cards}
    assert "Overdue section draft" in by_title
    assert by_title["Overdue section draft"]["why_now"] == "Overdue"
    assert "Tomorrow review" in by_title
    assert "Dependent review" in by_title  # blocked commitment
    assert "Lunar proposal" in by_title  # active project focus
    suggestion = by_title["Suggested outreach"]
    assert suggestion["suggestion"] is True
    assert suggestion["group"] == "suggestion"

    for card in cards:
        assert card["why_now"], "every card exposes its reason"
        assert card["next_action"]["label"] and card["next_action"]["route"]
        assert card["scope_id"]
        assert card["evidence_refs"]


def test_pending_authority_outranks_deadlines(server, tmp_path) -> None:
    _seed(server, tmp_path)
    # A failed download job is an authority-group card and must outrank deadlines.
    instance = server
    failed = DownloadJobManager(instance.models)
    failed.start("http://127.0.0.1:1/dead/model/haven-model.json")
    instance.today._model_jobs = failed  # noqa: SLF001
    cards = _dispatch(instance, "today.cards", {})["result"]["cards"]
    job_card = next(card for card in cards if card["card_id"].startswith("job:"))
    assert job_card["group"] == "authority"
    assert cards[0]["group"] == "authority"


def test_dismissal_hides_cards_and_persists(server, tmp_path) -> None:
    _seed(server, tmp_path)
    cards = _dispatch(server, "today.cards", {})["result"]["cards"]
    suggestion = next(card for card in cards if card["suggestion"])
    dismissed = _dispatch(server, "today.dismiss", {"card_id": suggestion["card_id"]})["result"]
    assert dismissed["ok"] is True
    after = _dispatch(server, "today.cards", {})["result"]["cards"]
    assert all(card["card_id"] != suggestion["card_id"] for card in after)

    data_dir = server.setup_store.path.parent
    server.server_close()
    rebound, _ = make_server(0, data_dir=data_dir, clock=lambda: NOW)
    try:
        cards = rebound.build_ipc_dispatcher()(
            request_message("r", "today.cards", {})
        )["result"]["cards"]
        assert all(card["card_id"] != suggestion["card_id"] for card in cards)
    finally:
        rebound.server_close()


def test_no_signals_no_fabricated_cards(server) -> None:
    cards = _dispatch(server, "today.cards", {})["result"]["cards"]
    assert cards == []
