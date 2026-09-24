"""The Today attention projection (spec page 25).

Candidate signals come from the stores HAVEN already owns: pending
authority decisions, due/overdue tasks and approaching commitments, active
projects with unfinished next steps, blocked work, model/download jobs
needing a decision, and model suggestions (PROPOSED tasks). Every card
carries TITLE / WHY NOW / SCOPE / EVIDENCE / NEXT ACTION and its reason is
always exposed.

Ranking is deliberately conservative: authority decisions and hard
deadlines outrank commitments; commitments outrank suggestions. Today may
prioritize -- it may not fabricate commitments. Suggestions are visually
distinct and dismissible without touching authoritative state; suggested
work becomes a task only through explicit acceptance (the task update path).
"""

from .service import TodayService

__all__ = ["TodayService"]
