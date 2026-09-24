"""Identity: who is acting, and what scopes they belong to.

`contracts.py` is the Protocol; `local.py` is the private local
implementation: a file-backed principal id, a personal-root scope, and
membership-derived visibility (design spec pages 18-19, milestone C).
"""

from .contracts import IdentityProvider, ScopeMembership
from .local import LocalIdentityProvider, provision_identity

__all__ = ["IdentityProvider", "LocalIdentityProvider", "ScopeMembership", "provision_identity"]
