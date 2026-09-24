"""Extensions: the taxonomy, the intelligence boundary, and the registry.

Spec page 41: four extension classes with declared run-location and
access/authority boundaries; intelligence services receive bounded context
and return proposals (never authority-bearing mutations); the current
`haven/plugins` surface is an Export Consumer.
"""

from .intelligence import (
    BoundedContext,
    EchoIntelligenceService,
    IntelligenceBoundary,
    IntelligenceResponse,
    IntelligenceService,
)
from .taxonomy import (
    CLASS_BOUNDARIES,
    ExtensionClass,
    ExtensionDescriptor,
    ExtensionRegistry,
)

__all__ = [
    "CLASS_BOUNDARIES",
    "BoundedContext",
    "EchoIntelligenceService",
    "ExtensionClass",
    "ExtensionDescriptor",
    "ExtensionRegistry",
    "IntelligenceBoundary",
    "IntelligenceResponse",
    "IntelligenceService",
]
