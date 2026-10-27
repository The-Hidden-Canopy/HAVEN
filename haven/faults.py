"""Versioned, public error vocabulary; private evidence never crosses it.

Codes describe confirmed failures. Causes are explicitly hypotheses, not
conclusions inferred from exception prose. No adjacent private implementation
is used. The host supplies operations and journal roots, never request data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
from threading import RLock
from uuid import uuid4

SCHEMA_VERSION = 1
SUBSYSTEMS = (
    "actions", "alerts", "application", "attention", "audit", "authority",
    "automation", "backup", "browser", "cameras", "comms", "config", "core",
    "credentials", "decisions", "desktop", "devices", "discovery", "domains",
    "execution", "export", "extensions", "external_agents", "graph", "handoffs", "history",
    "identity", "intelligence", "integrations", "ipc", "knowledge", "marketplace",
    "memory", "models", "network", "ontology", "perception", "plugins", "providers",
    "projects", "recovery", "resources", "rooms", "people", "contexts", "scheduler", "scopes", "search", "speech",
    "storage", "sync", "tasks", "today", "trace", "web", "windows",
)


@dataclass(frozen=True)
class ErrorDefinition:
    code: str
    subsystem: str
    message: str
    recovery: str
    retryable: bool = False
    severity: str = "error"
    possible_cause: str = "The available evidence does not establish the underlying cause."


REGISTRY = {
    f"HAVEN.{name.upper()}.OPERATION_FAILED": ErrorDefinition(
        f"HAVEN.{name.upper()}.OPERATION_FAILED", name,
        f"The {name} operation could not complete.",
        "Inspect current state and the local diagnostic record before retrying.")
    for name in SUBSYSTEMS
}


def _register(suffix, subsystem, message, recovery, retryable=False, cause=None):
    code = "HAVEN." + suffix
    REGISTRY[code] = ErrorDefinition(code, subsystem, message, recovery, retryable,
                                    possible_cause=cause or "The underlying cause has not been confirmed.")


_register("AUTHORITY.SCOPE_DENIED", "authority", "The requested scope is not visible to the current principal.", "Select a visible scope or ask its owner to review membership.")
_register("AUTHORITY.OWNER_REQUIRED", "authority", "This operation requires a current owner.", "Use the current owner's session; saved decisions do not grant authority.")
_register("AUTHORITY.UNAUTHENTICATED", "authority", "An authenticated session or connection credential is required.", "Reconnect through the local HAVEN client.")
_register("AUTHORITY.POLICY_DENIED", "authority", "Current policy did not permit the operation.", "Review the operation's current authorization; do not reuse a stale handle.")
_register("IPC.UNKNOWN_OPERATION", "ipc", "This Core does not support the requested operation.", "Use matching native-client and Core versions.")
_register("IPC.INVALID_REQUEST", "ipc", "The request did not satisfy the operation's input contract.", "Review the input and submit a corrected request.")
_register("IPC.AUTHENTICATION_FAILED", "ipc", "Native IPC authentication failed.", "Reconnect using the current Core's token handoff.")
_register("IPC.UNAVAILABLE", "ipc", "The native connection to Core is unavailable.", "Reconnect to the local Core and inspect its startup status.", True, "Core may have stopped or the local pipe may have disconnected.")
_register("CORE.STARTUP_FAILED", "core", "HAVEN Core could not start.", "Inspect the private startup record and verify the selected installation.")
_register("CORE.OUTCOME_UNCERTAIN", "core", "The operation's final outcome is uncertain.", "Inspect durable state and reconcile before retrying; do not repeat an external effect blindly.")
_register("MEMORY.CONFLICT", "memory", "The decision register changed before this write committed.", "Refresh the current generation and review the new record before retrying.", True)
_register("MEMORY.SOURCE_CHANGED", "memory", "The referenced document no longer matches the reviewed evidence.", "Re-observe the document and reconsider the affected decision.")
_register("STORAGE.IO_FAILED", "storage", "A local persistence operation failed.", "Inspect local storage availability and current durable state before retrying.", False, "Storage capacity, permissions or an interrupted write may be involved.")
_register("RECOVERY.PENDING", "recovery", "Recovery requires an explicit current decision.", "Preserve existing files and review the recovery checkpoint before changing data.")
_register("PROVIDERS.UNAVAILABLE", "providers", "The provider did not produce a usable result.", "Review provider health and its current connection configuration.", True, "The provider or its transport may be unavailable.")
_register("PLUGINS.POLICY_DENIED", "plugins", "The extension cannot execute under the current host policy.", "Review static metadata and host support; browsing metadata must not import extension code.")
_register("PLUGINS.UNAVAILABLE", "plugins", "The local feature lifecycle service is unavailable.", "Preserve its state and inspect the private diagnostic record before attempting repair.")
_register("MARKETPLACE.UNAVAILABLE", "marketplace", "The local product lifecycle service is unavailable.", "Preserve the registry and inspect the private diagnostic record before attempting repair.")
_register("MARKETPLACE.VERIFICATION_FAILED", "marketplace", "The catalog or artifact did not pass verification.", "Review the pinned catalog, current trusted public keys and artifact digest; do not install it.")
_register("MARKETPLACE.ACQUISITION_FAILED", "marketplace", "The local product acquisition could not complete.", "Inspect the acquisition checkpoint and selected release before retrying.")
_register("DESKTOP.ACCESS_DENIED", "desktop", "Windows did not permit the startup configuration change.", "Review operating-system permissions; do not bypass the restriction.")
_register("IPC.PUSH_ONLY", "ipc", "The event channel accepts no application requests.", "Use the authenticated request pipe for RPC operations.")
_register("MODELS.BACKEND_UNAVAILABLE", "models", "The selected model backend is unavailable.", "Select a supported installed backend and review model health.")
_register("MODELS.ROLE_MISMATCH", "models", "The selected model cannot serve that role.", "Choose a model compatible with the requested role.")
_register("MODELS.DOWNLOAD_FAILED", "models", "The model download did not complete.", "Inspect the model job and verified artifact before retrying.")
_register("PLUGINS.VERIFICATION_FAILED", "plugins", "Extension catalog metadata did not pass verification.", "Review current trust and static metadata; do not execute the package.")
_register("BACKUP.VALIDATION_FAILED", "backup", "The backup or restore input did not pass validation.", "Keep the original installation unchanged and select a verified backup.")
_register("EXPORT.REDACTION_FAILED", "export", "A shareable diagnostic projection could not be produced.", "Keep the original evidence private and inspect the export contract.")
_register("CONFIG.INVALID", "config", "The supplied configuration is invalid.", "Review the configuration using the documented fields and absolute state paths.")
_register("NETWORK.TIMEOUT", "network", "The network operation timed out.", "Review connection health and inspect whether the operation already completed.", True, "Connectivity or service response time may be involved.")
_register("UNKNOWN.UNEXPECTED", "core", "An unexpected failure interrupted the operation.", "Inspect the private diagnostic record using the correlation ID; the cause is not established.")

ALIASES = {
    "storage_unavailable": "STORAGE.IO_FAILED",
    "only an owner can admit a household relationship": "AUTHORITY.OWNER_REQUIRED",
    "scope_denied": "AUTHORITY.SCOPE_DENIED", "scope_not_visible": "AUTHORITY.SCOPE_DENIED",
    "the requested scopes are not visible to the authenticated principal": "AUTHORITY.SCOPE_DENIED",
    "owner_required": "AUTHORITY.OWNER_REQUIRED", "permission_denied": "AUTHORITY.POLICY_DENIED",
    "authorization_denied": "AUTHORITY.POLICY_DENIED", "unknown credential": "AUTHORITY.UNAUTHENTICATED",
    "HAVEN desktop session required": "AUTHORITY.UNAUTHENTICATED",
    "native IPC authentication failed": "IPC.AUTHENTICATION_FAILED",
    "invalid JSON body": "IPC.INVALID_REQUEST",
    "request_invalid": "IPC.INVALID_REQUEST", "invalid IPC request": "IPC.INVALID_REQUEST",
    "generation_conflict": "MEMORY.CONFLICT", "revision_conflict": "MEMORY.CONFLICT",
    "document_changed": "MEMORY.SOURCE_CHANGED", "source_changed": "MEMORY.SOURCE_CHANGED",
    "outcome_uncertain": "CORE.OUTCOME_UNCERTAIN", "service_replaced": "IPC.UNAVAILABLE",
    "in-process provider loading blocked": "PLUGINS.POLICY_DENIED",
    "feature_lifecycle_unavailable": "PLUGINS.UNAVAILABLE", "policy_denied": "PLUGINS.POLICY_DENIED",
    "signature_invalid": "MARKETPLACE.VERIFICATION_FAILED", "fixture_catalog_invalid": "MARKETPLACE.VERIFICATION_FAILED",
    "digest_mismatch": "MARKETPLACE.VERIFICATION_FAILED", "catalog_expired": "MARKETPLACE.VERIFICATION_FAILED",
    "download_failed": "MARKETPLACE.ACQUISITION_FAILED", "product_lifecycle_unavailable": "MARKETPLACE.UNAVAILABLE",
    "no provider attached": "PROVIDERS.UNAVAILABLE", "provider_unavailable": "PROVIDERS.UNAVAILABLE",
    "backup_invalid": "BACKUP.VALIDATION_FAILED", "recovery_pending": "RECOVERY.PENDING",
    "core_startup_failed": "CORE.STARTUP_FAILED",
    "export_redaction_failed": "EXPORT.REDACTION_FAILED",
    "startup_access_denied": "DESKTOP.ACCESS_DENIED",
    "the events pipe is push-only": "IPC.PUSH_ONLY",
    "model backend unavailable": "MODELS.BACKEND_UNAVAILABLE",
    "model cannot serve role": "MODELS.ROLE_MISMATCH",
    "provider connection failed": "PROVIDERS.UNAVAILABLE",
    "unknown person": "PEOPLE.NOT_FOUND", "unknown context": "CONTEXTS.NOT_FOUND",
    "unknown project": "PROJECTS.NOT_FOUND", "unknown task": "TASKS.NOT_FOUND",
    "unknown room": "ROOMS.NOT_FOUND", "unknown job": "MODELS.NOT_FOUND",
    "config_invalid": "CONFIG.INVALID", "not found": "IPC.UNKNOWN_OPERATION", "unknown IPC method": "IPC.UNKNOWN_OPERATION",
}

# Static compatibility literals are safe; interpolated exception text is not.
SAFE_MESSAGES = frozenset(ALIASES) | frozenset(("'enabled' must be a boolean",
 "'voice' and 'intelligence' must be booleans",
 'HAVEN desktop session required',
 'I could not find the focused automation',
 'a JSON body is required',
 'a declared owner is required for knowledge changes',
 "a non-empty 'action' is required",
 "a non-empty 'app' is required",
 "a non-empty 'base_url' is required",
 "a non-empty 'body' is required",
 "a non-empty 'browser' is required",
 "a non-empty 'candidate_id' is required",
 "a non-empty 'card_id' is required",
 "a non-empty 'conflict_id' is required",
 "a non-empty 'device_type' is required",
 "a non-empty 'entry_point_name' is required",
 "a non-empty 'event_id' is required",
 "a non-empty 'id' is required",
 "a non-empty 'imap_host' is required",
 "a non-empty 'justification' is required",
 "a non-empty 'message_id' is required",
 "a non-empty 'path' is required",
 "a non-empty 'proposition' is required",
 "a non-empty 'provider_id' is required",
 "a non-empty 'q' query parameter is required",
 "a non-empty 'request_id' is required",
 "a non-empty 'resource_id' is required",
 "a non-empty 'service' is required",
 "a non-empty 'source_ref' is required",
 "a non-empty 'source_text' is required",
 "a non-empty 'subject' is required",
 "a non-empty 'target_device_id' is required",
 "a non-empty 'title' is required",
 "a non-empty 'token' is required",
 "a non-empty 'url' is required",
 "a non-empty 'username' is required",
 'a non-empty calendar secret is required',
 'a non-empty email secret is required',
 'a writable device capability is required',
 'add at least one allowed folder before enabling computer access',
 'alias keys and values must be strings',
 'aliases must be a mapping',
 'an event_id query parameter is required',
 "an integer 'brightness_pct' is required",
 'approved automations are immutable; revoke it and create a new proposal',
 'at least one recipient is required',
 'auth_key must be a non-empty string',
 "auth_mode must be 'bearer' or 'basic'",
 'authority items cannot be snoozed',
 'automatic relationship admission is disabled; review and admit explicitly',
 'automation deletion requires a justification',
 'automation editing requires a non-empty justification',
 'browser close requires a non-empty justification',
 'browser focus requires a non-empty justification',
 'browser open requires a non-empty justification',
 'calendar create requires a non-empty justification',
 'calendar credential could not be stored',
 'calendar delete requires a non-empty justification',
 'calendar update requires a non-empty justification',
 'cannot rotate a key while outbound events are pending',
 'cc must be a list of non-empty strings',
 "choice must be 'local', 'remote', or 'merge'",
 'computer access is not enabled, or no allowed folder is reachable',
 'conflict resolution requires a non-empty justification',
 'conflict resolution scope is not visible',
 'credential storage is unavailable',
 'declare a household owner before enrolling devices',
 'declare a household owner before finishing setup',
 'email delete requires a non-empty justification',
 'email send requires a non-empty justification',
 'entity_id and room_id must be given together',
 'feature_lifecycle_unavailable',
 'full-body indexing is available only for the personal scope',
 'imap_port must be between 1 and 65535',
 'include_body must be a boolean',
 'installation content cannot be safely moved',
 'interpretation must be a string or null',
 'invalid JSON body',
 'invalid limit',
 'label must contain at least one letter or digit',
 'limit must be a positive integer',
 'limit must be an integer from 1 to 200',
 'mailbox must be a non-empty string',
 "merge requires a non-empty 'merge_fields' object",
 'name must contain at least one letter or digit',
 'native folder picker is available in HAVEN Desktop',
 'native folder picker is unavailable',
 'no calendar source is configured',
 'no household owner declared yet',
 'no pending action with that request_id',
 'no provider attached',
 'no resource store is attached to this installation',
 'no sync transport is configured',
 'not found',
 'only an owner can admit a household relationship',
 'only approved automations can be enabled or disabled',
 'only proposed automations can be edited; revoke and add a new one',
 'operation_failed',
 'outbox contains unsigned or invalid sync events',
 'outcome_uncertain',
 'owner_required',
 'parameters must be an object',
 'principal mismatch',
 'product_lifecycle_unavailable',
 'recipients must be non-empty strings',
 'relationship admission governance is not configured',
 'relationship admission household scope does not match',
 'relationship admission requires a non-empty justification',
 'remote calendar events are read-only',
 'request_invalid',
 'revoke approved automations before demoting the last household owner',
 'revoke approved automations before removing the last household owner',
 'scope is not visible',
 'scope_denied',
 'service_replaced',
 'smtp_port must be between 1 and 65535',
 'storage_unavailable',
 'sync is disabled',
 'sync requires a protected shared transport key',
 'target device does not expose that writable service',
 'that automation already skips the requested day',
 'that change would leave the automation with no scheduled weekdays',
 "the candidate's scope is not visible",
 'the focused automation has no time schedule to edit',
 'the named resource is not a browser tab',
 'the named resource is not a live window',
 'the named tab is unknown, closed, or not a browser tab',
 "the named window does not declare the 'window.focus' capability",
 'the named window is unknown or no longer current',
 'the requested scope is not visible to this HAVEN installation',
 'the selected capability is not writable',
 'the service returned an invalid response envelope',
 'the tab is already closed',
 'the tab is no longer open',
 'the target contains existing HAVEN state; choose an empty installation directory',
 'the target installation directory cannot be safely inspected',
 'this item cannot be dismissed',
 'time_of_day must be an HH:MM or HH:MM:SS value',
 'timeout must be between 0 and 60 seconds',
 'unknown IPC method',
 'unknown automation',
 'unknown claim',
 'unknown device',
 'unknown or already-resolved attention item',
 'unknown request',
 'unknown rule',
 'unknown target device',
 'unknown target device capability',
 'unknown workflow action',
 'unsupported service',
 'username is required for basic authentication',
 'username must be a string',
 'weekdays must contain integers from 0 (Monday) through 6 (Sunday)',
 'window focus requires a non-empty justification'))


# Existing service exception codes, frozen as a versioned compatibility set.
CODE_NAMES = frozenset(('action_invalid', 'activation_failed', 'artifact_invalid', 'busy', 'cancelled', 'clock_invalid', 'dependency_in_use', 'dependency_missing', 'digest_mismatch', 'disabled', 'download_required', 'expired', 'fixture_catalog_invalid', 'forbidden', 'hb_context_create', 'hb_gatt_read', 'interrupted', 'invalid_payload', 'not_configured', 'not_enabled', 'not_installed', 'not_staged', 'operation_invalid', 'platform', 'platform_mismatch', 'registry_invalid', 'release_invalid', 'release_not_approved', 'release_revoked', 'removal_pending', 'replay', 'request_invalid', 'response_too_large', 'rollback_unavailable', 'schema_invalid', 'scope_denied', 'service_unavailable', 'signature_invalid', 'signature_missing', 'signature_untrusted', 'stale_handle', 'state_invalid', 'timeout', 'unknown_command', 'unreachable', 'unsafe_path', 'untrusted_release', 'update_required', 'verification_unavailable', 'version_mismatch'))
SAFE_MESSAGES = SAFE_MESSAGES | CODE_NAMES | frozenset(('external.authentication_failed', 'external.binding_expired', 'external.connection_disabled', 'external.connection_unknown', 'external.cross_household', 'external.not_authorized', 'external.pending_consumed', 'external.pending_expired', 'external.pending_principal_mismatch', 'external.pending_unknown', 'external.principal_unavailable', 'external.rate_limited', 'external.scope_missing', 'external.subject_unbound'))
SOURCE_CODES = {'integrations': ('hb_context_create', 'hb_gatt_read', 'timeout'),
 'marketplace': ('artifact_invalid',
                 'busy',
                 'cancelled',
                 'clock_invalid',
                 'dependency_in_use',
                 'dependency_missing',
                 'digest_mismatch',
                 'download_required',
                 'expired',
                 'fixture_catalog_invalid',
                 'forbidden',
                 'interrupted',
                 'invalid_payload',
                 'not_configured',
                 'not_enabled',
                 'not_installed',
                 'operation_invalid',
                 'platform_mismatch',
                 'registry_invalid',
                 'release_invalid',
                 'release_not_approved',
                 'release_revoked',
                 'removal_pending',
                 'replay',
                 'request_invalid',
                 'response_too_large',
                 'rollback_unavailable',
                 'schema_invalid',
                 'signature_invalid',
                 'signature_missing',
                 'signature_untrusted',
                 'unreachable',
                 'unsafe_path',
                 'verification_unavailable',
                 'version_mismatch'),
 'plugins': ('activation_failed',
             'disabled',
             'expired',
             'invalid_payload',
             'not_installed',
             'not_staged',
             'response_too_large',
             'rollback_unavailable',
             'schema_invalid',
             'scope_denied',
             'service_unavailable',
             'signature_invalid',
             'signature_missing',
             'stale_handle',
             'state_invalid',
             'unknown_command',
             'unreachable',
             'untrusted_release',
             'update_required',
             'verification_unavailable'),
 'speech': ('platform',),
 'web': ('action_invalid',)}
for _subsystem, _names in SOURCE_CODES.items():
    for _name in _names:
        _code = f"HAVEN.{_subsystem.upper()}.{_name.upper()}"
        REGISTRY.setdefault(_code, ErrorDefinition(_code, _subsystem,
            "The operation failed: " + _name.replace("_", " ") + ".",
            "Inspect current state and the local diagnostic record before retrying."))

# Prefix recognizers return only fixed public labels, never the supplied suffix.
PUBLIC_PREFIXES = (
    ("the events pipe is push-only", "the events pipe is push-only"),
    ("unknown external provider", "unknown external provider"),
    ("email provider is not configured", "email is not configured"),
    ("email read provider is not configured", "email is not configured"),
    ("no email provider is configured", "email is not configured"),
    ("candidate is already enrolled", "candidate is already enrolled"),
    ("device is already enrolled", "device is already enrolled"),
    ("data dir exists as a file", "data dir exists as a file"),
    ("home assistant connection failed", "provider connection failed"),
    ("no email reader is configured", "email is not configured"),
    ("provider connection failed", "provider connection failed"),
    ("could not connect", "provider connection failed"),
    ("no backend registered", "model backend unavailable"),
    ("unknown person:", "unknown person"), ("unknown context:", "unknown context"),
    ("unknown project:", "unknown project"), ("unknown task:", "unknown task"),
    ("unknown room:", "unknown room"), ("unknown job:", "unknown job"),
    ("unknown connection", "unknown connection"), ("unknown household principal", "unknown household principal"),
    ("unknown external scope", "unknown external scope"), ("credential already in use", "credential already in use"),
    ("unsupported device_type", "unsupported device_type"), ("candidate is not enrolled:", "candidate is not enrolled"),
    ("unsupported provider kind", "unsupported provider kind"), ("in-process provider loading blocked", "in-process provider loading blocked"),
    ("could not find a light", "could not find a light"),
    ("i could not find a light", "could not find a light"), ("unknown role", "unknown role"),
    ("unknown model", "unknown model"), ("already enrolled", "already enrolled"),
    ("cannot fetch", "download_failed"),
    ("could not reach the home assistant provider", "provider connection failed"),
    ("unknown candidate:", "unknown candidate"),
    ("unknown provider:", "unknown external provider"),
    ("context already declared with a different label", "context already declared with a different label"),
    ("person already declared with a different name", "person already declared with a different name"),
    ("room already declared with a different name", "room already declared with a different name"),
    ("stale revision:", "stale revision"),
    ("read-only local .eml adapter", "local email adapter is read-only"),
)
for _label in ("name", "entity_id", "device_id", "display_name", "role"):
    PUBLIC_PREFIXES += ((_label + " must be", _label + " is invalid"),)
SAFE_MESSAGES = SAFE_MESSAGES | frozenset(label for _, label in PUBLIC_PREFIXES) | {
    "a non-empty 'role' is required", "a non-empty 'room_id' is required",
    "a non-empty 'device_id' is required", "email is not configured",
    "a non-empty 'display_name' is required", "a non-empty 'provider' is required",
    "the event channel is push-only", "unknown external provider",
    "display_name must be non-empty", "display_name must be a non-empty string",
    "a non-negative integer 'revision' is required",
    "due_at must be an ISO datetime string or null", "due_at must be an ISO datetime string",
    "due_at must be timezone-aware",
}


def public_error_name(raw):
    if not isinstance(raw, str): return "operation_failed"
    if raw in SAFE_MESSAGES: return raw
    normalized = raw.strip("\"'").replace("'", "").replace("\"", "")
    for prefix, label in PUBLIC_PREFIXES:
        if normalized.lower().startswith(prefix): return label
    if "cannot serve role" in raw: return "model cannot serve role"
    if raw == "ERROR: Access is denied.": return "startup_access_denied"
    if "not a valid ExternalProvider" in raw or "unsupported external provider" in raw: return "unknown external provider"
    return "operation_failed"


def subsystem_for(operation: str) -> str:
    parts = re.split(r"[./]", operation.lower())
    if "diagnostics" in parts and "export" in parts:
        return "export"
    if "backups" in parts or "backup" in parts:
        return "backup"
    names = {"features": "plugins", "products": "marketplace", "decisions": "memory", "computer": "resources", "external-agents": "authority", "external_agents": "authority", "relationships": "graph", "composer": "intelligence", "requests": "authority", "service": "desktop", "calendar": "comms", "email": "comms", "host": "ipc", "setup": "config"}
    for part in parts:
        part = names.get(part, part)
        if part in SUBSYSTEMS and part not in {"web", "core", "application"}:
            return part
    return "core"


PUBLIC_ACTIONS = frozenset({"request", "get", "list", "create", "update", "delete", "load", "unload", "register", "download", "install", "enable", "disable", "run", "inspect", "stage", "rollback", "uninstall", "propose", "review", "context", "snapshot", "erase", "export", "restore", "probe", "authenticate", "connect", "configure", "scan", "resolve", "approve", "deny", "cancel", "finish", "preferences", "data_dir", "status", "refresh", "admit", "send", "read"})


def operation_for(operation):
    family = subsystem_for(operation)
    parts = re.split(r"[./]", operation.lower().replace("-", "_"))
    action = parts[-1] if parts and parts[-1] in PUBLIC_ACTIONS else "request"
    return family + "." + action


for _family in SUBSYSTEMS:
    for _suffix, _message in (("NOT_FOUND", "The requested item was not found."),
                               ("INVALID_INPUT", "The supplied input does not satisfy this operation's contract."),
                               ("NOT_SUPPORTED", "This operation does not support the selected input."),
                               ("NOT_CONFIGURED", "The subsystem has not been configured for this operation."),
                               ("CONFLICT", "The current record conflicts with the requested change.")):
        _register(_family.upper() + "." + _suffix, _family, _message,
                  "Review the current subsystem state and documented inputs before retrying.")


class PublicFault(dict):
    """Host-minted marker; a caller-supplied JSON fault is never trusted."""


def describe(error, operation, *, status=None, exception=None, correlation_id=None):
    raw = error if isinstance(error, str) else ""
    family = subsystem_for(operation)
    suffix = ALIASES.get(raw)
    if raw in {"policy_denied", "permission_denied", "authorization_denied"}:
        suffix = "AUTHORITY.POLICY_DENIED"
    if raw == "download_failed" and family == "models":
        suffix = "MODELS.DOWNLOAD_FAILED"
    if raw in {"signature_invalid", "digest_mismatch", "catalog_expired"} and family == "plugins":
        suffix = "PLUGINS.VERIFICATION_FAILED"
    if suffix:
        code = "HAVEN." + suffix
    elif raw in CODE_NAMES and f"HAVEN.{family.upper()}.{raw.upper()}" in REGISTRY:
        code = f"HAVEN.{family.upper()}.{raw.upper()}"
    elif raw in SAFE_MESSAGES and (raw.startswith("unknown ") or raw == "candidate is not enrolled"):
        code = f"HAVEN.{family.upper()}.NOT_FOUND"
    elif raw in {"stale revision", "context already declared with a different label",
                 "person already declared with a different name", "room already declared with a different name"}:
        code = f"HAVEN.{family.upper()}.CONFLICT"
    elif raw.endswith(" is invalid") or raw.endswith(" is required") or raw == "conflict resolution requires a non-empty justification":
        code = f"HAVEN.{family.upper()}.INVALID_INPUT"
    elif raw.startswith("unsupported "):
        code = f"HAVEN.{family.upper()}.NOT_SUPPORTED"
    elif raw.endswith("not configured"):
        code = f"HAVEN.{family.upper()}.NOT_CONFIGURED"
    elif status in {401, 403}:
        code = "HAVEN.AUTHORITY." + ("UNAUTHENTICATED" if status == 401 else "POLICY_DENIED")
    elif isinstance(exception, TimeoutError):
        code = "HAVEN.NETWORK.TIMEOUT"
    elif isinstance(exception, OSError):
        code = "HAVEN.STORAGE.IO_FAILED"
    elif isinstance(exception, (ValueError, KeyError, TypeError)):
        code = "HAVEN.IPC.INVALID_REQUEST"
    elif isinstance(exception, Exception):
        code = "HAVEN.UNKNOWN.UNEXPECTED"
    else:
        code = f"HAVEN.{family.upper()}.OPERATION_FAILED"
    definition = REGISTRY[code]
    # Never put a request path, ID, argument or exception in the public fields.
    op = operation_for(operation)
    correlation_id = correlation_id if isinstance(correlation_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", correlation_id) else uuid4().hex
    return PublicFault({"schema_version": SCHEMA_VERSION, "code": code, "subsystem": definition.subsystem,
            "operation": op, "severity": definition.severity, "retryable": definition.retryable,
            "message": definition.message, "recovery_action": definition.recovery,
            "correlation_id": correlation_id, "evidence": {"confirmed": "operation_failed"},
            "probable_causes": [{"explanation": definition.possible_cause, "confidence": "possible"}]})


class FaultJournal:
    """Bounded local grouping and original evidence, never a permission grant."""
    def __init__(self, root):
        self.root = Path(root)
        self._lock = RLock()

    def _load(self):
        path = self.root / "incidents.json"
        self._safe_path(path)
        if not path.exists():
            return []
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema_version") != SCHEMA_VERSION or not isinstance(value.get("incidents"), list):
            raise ValueError("unsupported incident journal")
        rows = value["incidents"]
        for row in rows:
            definition = REGISTRY.get(row.get("code"))
            if definition is None or type(row.get("count")) is not int or row["count"] < 0:
                raise ValueError("invalid incident journal")
            if not isinstance(row.get("operation"), str) or row["operation"] != operation_for(row["operation"]):
                raise ValueError("invalid incident operation")
            if row.get("subsystem") != definition.subsystem or row.get("severity") != definition.severity or row.get("retryable") is not definition.retryable:
                raise ValueError("invalid incident metadata")
            for key in ("first_seen", "last_seen"):
                value = datetime.fromisoformat(row[key])
                if value.tzinfo is None:
                    raise ValueError("incident timestamps require timezone")
                row[key] = value.astimezone(timezone.utc).isoformat()
        return rows

    def record(self, fault, original):
        with self._lock:
            self._safe_path(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
            if self.root.is_symlink():
                raise OSError("diagnostic root cannot be a link")
            rows = self._load()
            now = datetime.now(timezone.utc).isoformat()
            row = next((r for r in rows if r["code"] == fault["code"] and r["operation"] == fault["operation"]), None)
            if row is None:
                row = {k: fault[k] for k in ("code", "operation", "subsystem", "severity", "retryable")}
                row.update(count=0, first_seen=now, first_correlation=fault["correlation_id"], correlations=[])
                rows.append(row)
            row["count"] += 1
            row["last_seen"] = now
            row["last_correlation"] = fault["correlation_id"]
            row["correlations"] = (row["correlations"] + [fault["correlation_id"]])[-16:]
            self._write("incidents.json", {"schema_version": SCHEMA_VERSION, "incidents": rows})
            # Original text stays owner-local, separate from public summaries.
            private = self.root / "private-evidence.json"
            self._safe_path(private)
            evidence = json.loads(private.read_text()) if private.exists() else []
            if not isinstance(evidence, list):
                raise ValueError("invalid private diagnostic evidence")
            evidence.append({"correlation_id": fault["correlation_id"], "at": now, "original": str(original)[:8192]})
            self._write("private-evidence.json", evidence[-64:])

    def _safe_path(self, path):
        for candidate in (path, *path.parents):
            if candidate.exists():
                info = candidate.lstat()
                if candidate.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                    raise OSError("diagnostic path contains an unsupported link")

    def _write(self, name, value):
        target = self.root / name
        self._safe_path(target)
        temporary = self.root / (".write-" + uuid4().hex)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def summary(self):
        with self._lock:
            return [{k: row[k] for k in ("code", "operation", "subsystem", "severity", "retryable", "count", "first_seen", "last_seen")}
                    for row in self._load()]


def public_failure(payload, operation, *, journal=None, status=None, exception=None, correlation_id=None):
    """One adapter for HTTP, IPC, native UI and support incident summaries."""
    if not isinstance(payload, dict) or not (payload.get("ok") is False or "error" in payload):
        return payload
    original_error = payload.get("error", payload.get("message", "operation_failed"))
    raw = public_error_name(original_error)
    existing = payload.get("fault")
    fault = existing if isinstance(existing, PublicFault) else describe(
        raw, operation, status=status, exception=exception, correlation_id=correlation_id)
    if journal is not None and not isinstance(existing, PublicFault):
        try:
            journal.record(fault, payload)
        except (OSError, ValueError, KeyError, TypeError):
            # Diagnostics failure cannot bypass the original refusal.
            pass
    # Preserve only static compatibility error names, never interpolated detail.
    result = {key: value for key, value in payload.items() if key not in {"error", "message", "detail", "reason", "fault", "code", "_private_evidence"}}
    result.update(ok=False, error=raw, fault=fault)
    if isinstance(payload.get("code"), str) and payload["code"] in SAFE_MESSAGES:
        result["code"] = payload["code"]
    return result


__all__ = ["SCHEMA_VERSION", "SUBSYSTEMS", "REGISTRY", "ALIASES", "FaultJournal", "describe", "public_failure", "operation_for", "public_error_name"]
