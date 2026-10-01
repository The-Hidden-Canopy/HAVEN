"""Domain-independent automation: `schema.py`'s `AutomationSpec` contract,
`lifecycle.py`'s propose/approve/enable/revoke state machine for it,
`events.py`'s `AutomationEvent` vocabulary, `emitters.py`' production
observation adapters, and `resource_scheduler.py`'s
entry point that dispatches a due (TIME) or matched (EVENT) automation
through its domain's own existing governed path. See each module's own
docstring for exact scope -- in particular, evidence/deadline/
external-condition triggers, and scheduler boot/restart durability, remain
separate follow-on boundaries.
"""

from .events import AutomationEvent, AutomationEventFeed, AutomationEventPublisher
from .persistence import ResourceAutomationSnapshot, ResourceAutomationStore
from .service import ResourceAutomationService
from .emitters import (
    ComputerResourceAutomationEmitter,
    EmailAutomationEmitter,
    ProviderHealthAutomationEmitter,
    TaskAutomationEmitter,
)
from .lifecycle import (
    AutomationEventSink,
    AutomationLifecycleEvent,
    AutomationRule,
    RuleTransitionResult,
    approve,
    propose,
    rehydrate,
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
    "AutomationEventFeed",
    "AutomationEventPublisher",
    "AutomationEventSink",
    "AutomationLifecycleEvent",
    "AutomationRule",
    "AutomationSpec",
    "DEFAULT_COOLDOWN",
    "ResourceActionScheduler",
    "ResourceAutomationSnapshot",
    "ResourceAutomationStore",
    "ResourceAutomationService",
    "ResourceScheduleOutcome",
    "ResourceScheduleStatus",
    "RuleTransitionResult",
    "Selector",
    "Trigger",
    "TriggerKind",
    "approve",
    "propose",
    "rehydrate",
    "revoke",
    "set_enabled",
    "ComputerResourceAutomationEmitter",
    "EmailAutomationEmitter",
    "ProviderHealthAutomationEmitter",
    "TaskAutomationEmitter",
]
