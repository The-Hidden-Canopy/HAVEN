"""Identity: who is acting, and what scopes they belong to.

Contract-only package (see `contracts.py`'s docstring) -- no
`LocalIdentityProvider` implementation yet.
"""

from .contracts import IdentityProvider, ScopeMembership

__all__ = ["IdentityProvider", "ScopeMembership"]
