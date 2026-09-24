"""TodayService: ranked attention cards computed from existing stores.

Card contract (spec page 25): title / why_now / scope / evidence refs /
next action. Groups, in ranking order: authority (pending decisions,
failed jobs) < deadline (due/overdue, approaching commitments) <
commitment (active-project focus, blocked work) < suggestion (proposed
tasks). Dismissals persist per card id; a dismissed suggestion stays gone
until its underlying signal changes identity.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

_GROUP_RANK = {"authority": 0, "deadline": 1, "commitment": 2, "suggestion": 3}

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731


class TodayService:
    def __init__(
        self,
        *,
        director,
        identity,
        tasks_store,
        projects_store,
        comms,
        model_jobs,
        dismiss_path: str | Path,
        clock=_DEFAULT_CLOCK,
    ) -> None:
        self._director = director
        self._identity = identity
        self._tasks = tasks_store
        self._projects = projects_store
        self._comms = comms
        self._model_jobs = model_jobs
        self._dismiss_path = Path(dismiss_path)
        self._clock = clock
        self._lock = threading.Lock()
        self._dismissed = self._load_dismissed()

    def set_director(self, director) -> None:
        self._director = director

    def _load_dismissed(self) -> set[str]:
        try:
            data = json.loads(self._dismiss_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        if not isinstance(data, dict):
            return set()
        return {str(item) for item in data.get("dismissed", [])}

    def _save_dismissed(self) -> None:
        self._dismiss_path.parent.mkdir(parents=True, exist_ok=True)
        self._dismiss_path.write_text(
            json.dumps({"dismissed": sorted(self._dismissed)}, indent=2), encoding="utf-8"
        )

    # -- the projection ------------------------------------------------------------

    def cards(self) -> dict:
        visible = self._identity.visible_scope_ids()
        now = self._clock()
        cards: list[dict] = []
        cards.extend(self._authority_cards(now))
        cards.extend(self._deadline_cards(visible, now))
        cards.extend(self._commitment_cards(visible, now))
        cards.extend(self._suggestion_cards(visible, now))
        live = [card for card in cards if card["card_id"] not in self._dismissed]
        live.sort(key=lambda card: (_GROUP_RANK[card["group"]], card["at"] or "", card["card_id"]))
        return {"ok": True, "cards": live, "dismissed_count": len(self._dismissed)}

    def dismiss(self, *, card_id: str | None) -> dict:
        if not isinstance(card_id, str) or not card_id.strip():
            return {"ok": False, "error": "a non-empty 'card_id' is required"}
        with self._lock:
            self._dismissed.add(card_id.strip())
            self._save_dismissed()
        return {"ok": True, "dismissed": card_id.strip()}

    # -- signal collectors ------------------------------------------------------------

    def _authority_cards(self, now: datetime) -> list[dict]:
        cards = []
        try:
            pending = self._director.state().get("pending", [])
        except Exception:
            pending = []
        for request in pending:
            cards.append(
                {
                    "card_id": f"pending:{request['request_id']}",
                    "group": "authority",
                    "title": request.get("title", "A decision is waiting"),
                    "why_now": request.get("detail") or "Your approval is required before anything changes.",
                    "scope_id": self._identity.personal_scope_id,
                    "evidence_refs": [request["request_id"]],
                    "next_action": {"label": "Review in Home", "route": "home"},
                    "suggestion": False,
                    "at": request.get("expires_at") or "",
                }
            )
        for job in self._model_jobs.list():
            if job.state.value != "failed":
                continue
            cards.append(
                {
                    "card_id": f"job:{job.job_id}",
                    "group": "authority",
                    "title": f"Download failed: {job.manifest_id or job.url}",
                    "why_now": job.error or "The model download failed and needs your decision.",
                    "scope_id": self._identity.personal_scope_id,
                    "evidence_refs": [job.job_id],
                    "next_action": {"label": "Open models", "route": "models"},
                    "suggestion": False,
                    "at": job.updated_at.isoformat(),
                }
            )
        return cards

    def _deadline_cards(self, visible: tuple[str, ...], now: datetime) -> list[dict]:
        cards = []
        end_of_today = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        for scope_id in visible:
            for task in self._tasks.list_by_scope(scope_id):
                if task.is_terminal or task.due_at is None or task.due_at > end_of_today:
                    continue
                overdue = task.due_at < now
                cards.append(
                    {
                        "card_id": f"task-due:{task.task_id}",
                        "group": "deadline",
                        "title": task.title,
                        "why_now": "Overdue" if overdue else "Due today",
                        "scope_id": scope_id,
                        "evidence_refs": [f"task:{task.task_id}"],
                        "next_action": {"label": "Open task", "route": "tasks"},
                        "suggestion": False,
                        "at": task.due_at.isoformat(),
                    }
                )
        events = self._comms.list_events().get("events", [])
        for event in events:
            start = datetime.fromisoformat(event["start_at"])
            if start < now or start > now + timedelta(hours=48):
                continue
            cards.append(
                {
                    "card_id": f"event:{event['event_id']}",
                    "group": "deadline",
                    "title": event["title"],
                    "why_now": f"Coming up: {event['start_at'][:16].replace('T', ' ')}"
                    + (f" · {event['location']}" if event.get("location") else ""),
                    "scope_id": self._identity.personal_scope_id,
                    "evidence_refs": [event["resource_id"]],
                    "next_action": {"label": "Open calendar", "route": "communications"},
                    "suggestion": False,
                    "at": event["start_at"],
                }
            )
        return cards

    def _commitment_cards(self, visible: tuple[str, ...], now: datetime) -> list[dict]:
        cards = []
        for scope_id in visible:
            for task in self._tasks.list_by_scope(scope_id):
                if task.state != "blocked" or task.is_terminal:
                    continue
                cards.append(
                    {
                        "card_id": f"task-blocked:{task.task_id}",
                        "group": "commitment",
                        "title": task.title,
                        "why_now": f"Blocked by {len(task.dependency_ids)} unfinished dependency(ies).",
                        "scope_id": scope_id,
                        "evidence_refs": [f"task:{task.task_id}"],
                        "next_action": {"label": "Open task", "route": "tasks"},
                        "suggestion": False,
                        "at": task.updated_at.isoformat(),
                    }
                )
            for project in self._projects.list_by_scope(scope_id):
                if project.status != "active":
                    continue
                tasks = [
                    task
                    for task in self._tasks.list_by_scope(scope_id)
                    if task.project_id == project.project_id
                ]
                open_tasks = [task for task in tasks if not task.is_terminal]
                if not open_tasks:
                    continue
                cards.append(
                    {
                        "card_id": f"project:{project.project_id}",
                        "group": "commitment",
                        "title": project.title,
                        "why_now": f"{len(open_tasks)} open task(s), "
                        f"{sum(1 for task in tasks if task.state == 'done')} done.",
                        "scope_id": scope_id,
                        "evidence_refs": [f"project:{project.project_id}"],
                        "next_action": {"label": "Open project", "route": "projects"},
                        "suggestion": False,
                        "at": project.updated_at.isoformat(),
                    }
                )
        return cards

    def _suggestion_cards(self, visible: tuple[str, ...], now: datetime) -> list[dict]:
        cards = []
        for scope_id in visible:
            for task in self._tasks.list_by_scope(scope_id):
                if task.state != "proposed":
                    continue
                cards.append(
                    {
                        "card_id": f"task-proposed:{task.task_id}",
                        "group": "suggestion",
                        "title": task.title,
                        "why_now": "Suggested by HAVEN — accept to make it a commitment.",
                        "scope_id": scope_id,
                        "evidence_refs": list(task.source_refs) or [f"task:{task.task_id}"],
                        "next_action": {"label": "Review in Tasks", "route": "tasks"},
                        "suggestion": True,
                        "at": task.created_at.isoformat(),
                    }
                )
        return cards


__all__ = ["TodayService"]
