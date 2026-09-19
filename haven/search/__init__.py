"""Search: the "life search bar"'s query/result contract and service.

`query.py` is the contract; `service.py`'s `HavenSearchService` is the
first working implementation -- direct text match plus ontology-relationship
expansion, no index or vector infrastructure yet (see its own docstring).
"""

from .query import SearchHit, SearchQuery
from .service import HavenSearchService

__all__ = ["HavenSearchService", "SearchHit", "SearchQuery"]
