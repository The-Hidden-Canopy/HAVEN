"""Domain-independent automation: `schema.py`'s `AutomationSpec` contract,
`lifecycle.py`'s propose/approve/enable/revoke state machine for it,
`events.py`'s `AutomationEvent` vocabulary, and `resource_scheduler.py`'s
entry point that dispatches a due (TIME) or matched (EVENT) automation
through its domain's own existing governed path. See each module's own
docstring for exact scope -- in particular, evidence/deadline/
external-condition triggers, and any real event emitter, are still not
built.
"""

from .events import AutomationEvent, AutomationEventPublisher
from .lifecycle import (
    AutomationEventSink,
    AutomationLifecycleEvent,
    AutomationRule,
    RuleTransitionResult,
    approve,
    propose,
    revoke,
    set_enabled,
)
from .resource_scheduler import (
    DEFAULT_COOLDOWN,
    ResourceActionScheduler,
    ResourceScheduleOutcome,
    ResourceScheduleStatus,
)
from .schema import ActionTarget, AutomationSpec, Selector, Trigger, TriggerKind

__all__ = [
    "ActionTarget",
    "AutomationEvent",
    "AutomationEventPublisher",
    "AutomationEventSink",
    "AutomationLifecycleEvent",
    "AutomationRule",
    "AutomationSpec",
    "DEFAULT_COOLDOWN",
    "ResourceActionScheduler",
    "ResourceScheduleOutcome",
    "ResourceScheduleStatus",
    "RuleTransitionResult",
    "Selector",
    "Trigger",
    "TriggerKind",
    "approve",
    "propose",
    "revoke",
    "set_enabled",
]
