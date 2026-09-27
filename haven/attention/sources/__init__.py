"""Needs You source adapters (spec section 6).

Each adapter maps one domain's existing state into `AttentionItem`
candidates. An adapter never introduces new authority state of its own --
it only reads stores/services HAVEN already has and reports what belongs
in the Needs You eligibility test (spec 3.1).
"""

from .authority import AuthoritySource
from .knowledge import KnowledgeSource
from .models import ModelSource
from .projects import ProjectSource
from .tasks import TaskSource

__all__ = [
    "AuthoritySource",
    "KnowledgeSource",
    "ModelSource",
    "ProjectSource",
    "TaskSource",
]
