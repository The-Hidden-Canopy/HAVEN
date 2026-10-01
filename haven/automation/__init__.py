"""Domain-independent automation: `schema.py`'s `AutomationSpec` contract,
`lifecycle.py`'s propose/approve/enable/revoke state machine for it,
`events.py`'s `AutomationEvent` vocabulary, `emitters.py`' production
observation adapters, and `resource_scheduler.py`'s
entry point that dispatches a due (TIME or DEADLINE) or matched (EVENT)
automation through its domain's own existing governed path. Evidence and
external-condition values enter through typed, publisher-stamped emitters and
remain fail-closed when evidence is degraded.
"""

from .deadlines import AutomationDeadline
from .events import AutomationEvent, AutomationEventFeed, AutomationEventPublisher
from .persistence import ResourceAutomationSnapshot, ResourceAutomationStore
from .service import ResourceAutomationService
from .emitters import (
    ComputerResourceAutomationEmitter,
    EvidenceAutomationEmitter,
    EmailAutomationEmitter,
    ExternalConditionAutomationEmitter,
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
    "AutomationDeadline",
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
    "EvidenceAutomationEmitter",
    "EmailAutomationEmitter",
    "ExternalConditionAutomationEmitter",
    "ProviderHealthAutomationEmitter",
    "TaskAutomationEmitter",
]
