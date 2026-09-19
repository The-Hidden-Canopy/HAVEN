"""Knowledge: believed propositions with provenance and a belief state.

`claims.py` is the contract, `admission.py` is the write boundary, and
`store.py` is its SQLite-backed persistence. `service.py` connects provider
resources to deterministic extraction and admission.
"""

from .admission import AdmissionResult, AdmissionStatus, ClaimAdmissionPolicy, ClaimAdmissionService
from .candidates import CandidateClaim
from .claims import Claim, ClaimProvenance, ClaimState, is_stale
from .service import KnowledgeIngestResult, KnowledgeService
from .store import ClaimStore

__all__ = [
    "AdmissionResult",
    "AdmissionStatus",
    "CandidateClaim",
    "Claim",
    "ClaimAdmissionPolicy",
    "ClaimAdmissionService",
    "ClaimProvenance",
    "ClaimState",
    "ClaimStore",
    "KnowledgeIngestResult",
    "KnowledgeService",
    "is_stale",
]
