"""ModelSource: failed downloads and blocked model jobs (spec 6)."""

from __future__ import annotations

from datetime import datetime

from ..domain import AttentionActionRef, AttentionItem, AttentionKind, AttentionSeverity, Dismissibility, RouteRef


class ModelSource:
    def __init__(self, *, model_jobs, identity) -> None:
        self._model_jobs = model_jobs
        self._identity = identity

    def collect(self, *, now: datetime, visible_scope_ids: tuple[str, ...] | None = None) -> list[AttentionItem]:
        items: list[AttentionItem] = []
        for job in self._model_jobs.list():
            if job.state.value != "failed":
                continue
            label = job.manifest_id or job.url or job.job_id
            why_now = job.error or f"The download for {label} failed and needs your decision."
            items.append(
                AttentionItem(
                    attention_id=f"failure:model:{job.job_id}",
                    kind=AttentionKind.FAILURE,
                    severity=AttentionSeverity.NORMAL,
                    title=f"Model download failed: {label}",
                    summary=why_now,
                    why_now=why_now,
                    source_domain="models",
                    source_ref=f"job:{job.job_id}",
                    scope_id=self._identity.personal_scope_id,
                    created_at=job.updated_at,
                    evidence_refs=(job.job_id,),
                    route=RouteRef(page="models", action_hint="repair", entity_id=job.job_id),
                    available_actions=(
                        AttentionActionRef(action="route", label="Open models"),
                        AttentionActionRef(action="snooze", label="Snooze"),
                    ),
                    dismissibility=Dismissibility.SNOOZE,
                )
            )
        return items


__all__ = ["ModelSource"]
