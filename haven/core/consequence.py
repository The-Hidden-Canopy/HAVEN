"""Consequence classes: what kind of outcome an action can have, independent
of which domain page it came from (native product-consolidation plan, P1
"Cross-Domain Automation" §6.3 and P1 "Providers" §5.2).

This is a different axis from `haven.core.domain.RiskTier`. `RiskTier` is
`AuthorityEngine`'s own closed vocabulary for *deciding* whether a specific
`ActionRequest`/`ResourceActionRequest` may proceed right now (safe-
automatic / conditional / confirmation-required / forbidden). A
`ConsequenceClass` is a *description* a provider manifest or an automation
target declares about the kind of outcome it can reach at all -- "turning
off a lamp and sending an email are different actions with different
consequence classes" (plan §6.3) -- so the authority layer, an automation
engine, or a generic Settings provider card can reason about severity
without hardcoding "this came from Home so it must be low-risk."

Neither this module nor its callers grant authority by declaring a class;
`ConsequenceClass` is metadata an `AuthorityEngine`/`ResourceAuthorityEngine`
implementation MAY use to help decide a `RiskTier`, the same way a device's
`ControlClass` already does for `haven.authority.policy.CONTROL_CLASS_RISK`.
"""

from __future__ import annotations

from enum import Enum


class ConsequenceClass(str, Enum):
    READ_ONLY = "read_only"
    REVERSIBLE_LOCAL = "reversible_local"
    REVERSIBLE_EXTERNAL = "reversible_external"
    HIGH_IMPACT = "high_impact"
    DESTRUCTIVE = "destructive"
    SECURITY_SENSITIVE = "security_sensitive"


__all__ = ["ConsequenceClass"]
