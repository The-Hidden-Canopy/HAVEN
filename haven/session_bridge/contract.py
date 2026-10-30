"""Provider-neutral, host-owned session boundary. No model or runtime is defined."""
from dataclasses import dataclass
from typing import Protocol, Mapping, Any

CONTRACT = "haven-session-1"
READ = "session.read"
SEND = "session.send"
CANCEL = "session.cancel"
APPROVE = "session.approve"

@dataclass(frozen=True)
class AuthContext:
    household_id: str
    principal_id: str
    scopes: frozenset[str]

class SessionDenied(PermissionError): pass
class Conflict(ValueError): pass

class SessionProvider(Protocol):
    """A host supplies a provider for an existing session; no provider is required."""
    def capabilities(self) -> Mapping[str, Any]: ...
    def sessions(self, auth: AuthContext) -> Mapping[str, Any]: ...
    def snapshot(self, auth: AuthContext, session_id: str) -> Mapping[str, Any]: ...
    def replay(self, auth: AuthContext, session_id: str, after: int, limit: int = 100) -> Mapping[str, Any]: ...
    def memory(self, auth: AuthContext, session_id: str) -> Mapping[str, Any]: ...
    def command_status(self, auth: AuthContext, session_id: str, command_id: str) -> Mapping[str, Any]: ...
    def submit(self, auth: AuthContext, session_id: str, request_id: str, text: str) -> Mapping[str, Any]: ...
    def cancel(self, auth: AuthContext, session_id: str, request_id: str, job_id: str) -> Mapping[str, Any]: ...
    def decide(self, auth: AuthContext, session_id: str, request_id: str, approval_id: str,
               revision: int, allow: bool) -> Mapping[str, Any]: ...
