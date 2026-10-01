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
from .features import (
    DiscoveredFeatureModule,
    FeatureLoadError,
    FeatureModule,
    FeatureModulePlugin,
    FeatureServiceCatalog,
    build_feature_module,
    discover_feature_modules,
    inspect_feature_module,
)
from .taxonomy import (
    CLASS_BOUNDARIES,
    ExtensionClass,
    ExtensionDescriptor,
    ExtensionRegistry,
)

__all__ = [
    "CLASS_BOUNDARIES",
    "DiscoveredFeatureModule",
    "BoundedContext",
    "EchoIntelligenceService",
    "ExtensionClass",
    "ExtensionDescriptor",
    "ExtensionRegistry",
    "FeatureLoadError",
    "FeatureModule",
    "FeatureModulePlugin",
    "FeatureServiceCatalog",
    "IntelligenceBoundary",
    "IntelligenceResponse",
    "IntelligenceService",
    "build_feature_module",
    "discover_feature_modules",
    "inspect_feature_module",
]
