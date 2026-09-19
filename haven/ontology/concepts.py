"""The HAVEN Base Ontology's concept vocabulary -- small, public, extensible.

These are namespaced strings (`"haven:Project"`), not a closed enum: a
community provider is free to declare its own concepts under its own
namespace (`"git:Repository"`, `"home:Room"`) without this module knowing
they exist, the same open-vocabulary discipline `haven.scopes.ScopeRef.kind`
and `haven.resources.ResourceRecord.resource_type` already use. HAVEN Core
never validates a subject or object against this list (see
`haven.ontology.assertions`); `BASE_CONCEPTS` exists so a resolver or UI can
recognize the vocabulary this repo ships without a provider having to
redeclare it.

Deliberately small: this is a starting taxonomy for a household's/person's
own life, not an attempt to model every domain up front. An organization or
industry-specific ontology (compliance, manufacturing, an org chart) is
exactly what a private or community package is expected to layer on top,
under its own namespace, never by editing this file.
"""

from __future__ import annotations

NAMESPACE = "haven"

# -- actors --------------------------------------------------------------
ACTOR = "haven:Actor"
PERSON = "haven:Person"
ORGANIZATION = "haven:Organization"
TEAM = "haven:Team"

# -- scope ------------------------------------------------------------------
SCOPE = "haven:Scope"
WORKSPACE = "haven:Workspace"
PROJECT = "haven:Project"

# -- resources ----------------------------------------------------------
RESOURCE = "haven:Resource"
DOCUMENT = "haven:Document"
FILE = "haven:File"
REPOSITORY = "haven:Repository"
APPLICATION = "haven:Application"
DEVICE = "haven:Device"
SERVICE = "haven:Service"

# -- activity -----------------------------------------------------------
ACTIVITY = "haven:Activity"
TASK = "haven:Task"
MEETING = "haven:Meeting"
CONVERSATION = "haven:Conversation"
ACTION = "haven:Action"
DECISION = "haven:Decision"

# -- knowledge ----------------------------------------------------------
KNOWLEDGE = "haven:Knowledge"
CLAIM = "haven:Claim"
EVIDENCE = "haven:Evidence"
SOURCE = "haven:Source"

# -- place --------------------------------------------------------------
PLACE = "haven:Place"
ROOM = "haven:Room"

# -- handoff --------------------------------------------------------------
HANDOFF = "haven:Handoff"

BASE_CONCEPTS = frozenset(
    {
        ACTOR,
        PERSON,
        ORGANIZATION,
        TEAM,
        SCOPE,
        WORKSPACE,
        PROJECT,
        RESOURCE,
        DOCUMENT,
        FILE,
        REPOSITORY,
        APPLICATION,
        DEVICE,
        SERVICE,
        ACTIVITY,
        TASK,
        MEETING,
        CONVERSATION,
        ACTION,
        DECISION,
        KNOWLEDGE,
        CLAIM,
        EVIDENCE,
        SOURCE,
        PLACE,
        ROOM,
        HANDOFF,
    }
)

__all__ = [
    "ACTION",
    "ACTIVITY",
    "ACTOR",
    "APPLICATION",
    "BASE_CONCEPTS",
    "CLAIM",
    "CONVERSATION",
    "DECISION",
    "DEVICE",
    "DOCUMENT",
    "EVIDENCE",
    "FILE",
    "HANDOFF",
    "KNOWLEDGE",
    "MEETING",
    "NAMESPACE",
    "ORGANIZATION",
    "PERSON",
    "PLACE",
    "PROJECT",
    "REPOSITORY",
    "RESOURCE",
    "ROOM",
    "SCOPE",
    "SERVICE",
    "SOURCE",
    "TASK",
    "TEAM",
    "WORKSPACE",
]
