"""The relationship graph: deterministic edges, learned candidates, admission.

Spec page 21: ontology assertions are fed by (a) deterministic projections
(file/folder/repository, window/application, tab/session, project/task,
task/person, claim/resource, correction lineage, device/room) and (b) a
learned pipeline where observations become `CandidateRelationship`s that a
`RelationshipAdmissionPolicy` admits into the OntologyStore or rejects.
A model may suggest an edge; it may never silently create a durable
high-impact relationship.
"""

from .candidates import CandidateRelationship, Correlator
from .deterministic import RelationshipProjector
from .policy import HIGH_IMPACT_PREDICATES, RelationshipAdmissionPolicy
from .service import RelationshipService

__all__ = [
    "HIGH_IMPACT_PREDICATES",
    "CandidateRelationship",
    "Correlator",
    "RelationshipAdmissionPolicy",
    "RelationshipProjector",
    "RelationshipService",
]
