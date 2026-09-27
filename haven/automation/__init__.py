"""Domain-independent automation schema (native product-consolidation plan,
P1 "Cross-Domain Automation"). See `schema.py`'s module docstring for what
is and is not built yet -- this is a contract, not a running scheduler.
"""

from .schema import ActionTarget, AutomationSpec, Selector, Trigger, TriggerKind

__all__ = ["ActionTarget", "AutomationSpec", "Selector", "Trigger", "TriggerKind"]
