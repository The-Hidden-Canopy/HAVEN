"""Inference-backed speech provider adapters.

Thin HTTP seams from the speech protocols to the household's own inference
stack (see `http_inference` for the wire contract and the architectural
rule). These adapters are constructed at deployment time -- they require an
endpoint -- and registered by the deployer; they are deliberately NOT part
of `haven/providers/defaults.py`, which stays zero-config fixtures.
"""

from .http_inference import (
    InferenceAsrProvider,
    InferenceEndpointConfig,
    InferenceSynthesizer,
    InferenceUnavailableError,
    InferenceWakeDetector,
)

__all__ = [
    "InferenceAsrProvider",
    "InferenceEndpointConfig",
    "InferenceSynthesizer",
    "InferenceUnavailableError",
    "InferenceWakeDetector",
]
