"""Typed external-agent denials (spec Appendix A reason codes).

A denial carries a stable machine code plus a short human message that is
safe to hand to an external client: no stack traces, paths, tokens, or
household secrets.
"""

from __future__ import annotations

CONNECTION_UNKNOWN = "external.connection_unknown"
CONNECTION_DISABLED = "external.connection_disabled"
AUTHENTICATION_FAILED = "external.authentication_failed"
SUBJECT_UNBOUND = "external.subject_unbound"
SCOPE_MISSING = "external.scope_missing"
BINDING_EXPIRED = "external.binding_expired"
CROSS_HOUSEHOLD = "external.cross_household"
PRINCIPAL_UNAVAILABLE = "external.principal_unavailable"
RATE_LIMITED = "external.rate_limited"
PENDING_UNKNOWN = "external.pending_unknown"
PENDING_EXPIRED = "external.pending_expired"
PENDING_CONSUMED = "external.pending_consumed"
PENDING_PRINCIPAL_MISMATCH = "external.pending_principal_mismatch"
NOT_AUTHORIZED = "external.not_authorized"
INVALID_ARGUMENTS = "tool.invalid_arguments"

# Codes that indicate a possible attack rather than ordinary setup friction;
# the gateway records these as security events in the audit log.
SECURITY_CODES = frozenset({CROSS_HOUSEHOLD, AUTHENTICATION_FAILED, PENDING_PRINCIPAL_MISMATCH})


class ExternalDenied(Exception):
    """An external request refused before (or instead of) application dispatch."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


__all__ = [
    "AUTHENTICATION_FAILED",
    "BINDING_EXPIRED",
    "CONNECTION_DISABLED",
    "CONNECTION_UNKNOWN",
    "CROSS_HOUSEHOLD",
    "ExternalDenied",
    "INVALID_ARGUMENTS",
    "NOT_AUTHORIZED",
    "PENDING_CONSUMED",
    "PENDING_EXPIRED",
    "PENDING_PRINCIPAL_MISMATCH",
    "PENDING_UNKNOWN",
    "PRINCIPAL_UNAVAILABLE",
    "RATE_LIMITED",
    "SCOPE_MISSING",
    "SECURITY_CODES",
    "SUBJECT_UNBOUND",
]
