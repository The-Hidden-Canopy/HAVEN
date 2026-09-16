"""Deterministic alert policy: combine signals before deciding to notify.

Camera/vision/thermal/sensor observations already land in Haven as
`PresenceState`, `ContextState`, and `Prediction` -- the same evidence
`AuthorityEngine` uses to gate actuation. `AlertEngine` is the read-only
sibling: it gates *notification* the same deterministic way, combining
several conditions before deciding something is worth surfacing, instead of
one detector firing one notification. Nothing in this repo produces camera
events yet; this is the policy layer those observations feed once they do.
"""

from .models import Alert, AlertRule, AlertSeverity, ContextCondition, PresenceCondition
from .engine import AlertEngine

__all__ = [
    "Alert",
    "AlertEngine",
    "AlertRule",
    "AlertSeverity",
    "ContextCondition",
    "PresenceCondition",
]
