"""The HAVEN scheduler: another requester, never a privileged bypass.

A due schedule runs its rule through the ordinary governed path
(``HavenRuntime.run_rule``) -- household scope, role, approved-rule,
evidence freshness/confidence, human override, risk, and confirmation all
apply unchanged. A blocked run records the ordinary BLOCKED receipt and
event; history shows the scheduler was told no. The engine only answers
WHICH approved rules are due to be asked right now.
"""

from .engine import ScheduleStatus, SchedulerEngine

__all__ = ["ScheduleStatus", "SchedulerEngine"]
