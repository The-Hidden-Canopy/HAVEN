"""Scoped, opt-in transport adapter for host-supplied session providers.

This module neither starts an agent nor stores conversation or memory.  The
host must authenticate via HAVEN's external-agent gateway and supply a separate
per-session grant callback.  A provider must guarantee durable replay and
idempotent commands before those operations are exposed.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from haven.external_agents.domain import ExternalRequest, ExternalScope
from haven.external_agents.transport import hash_credential
from .contract import CONTRACT, AuthContext, READ, SEND, CANCEL, APPROVE, Conflict, SessionDenied

IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
OPERATIONS = frozenset({"sessions", "snapshot", "replay", "memory", "command_status", "submit", "cancel", "decide"})
SHAPES = {
    "capabilities":{"version"}, "sessions":{"version"}, "snapshot":{"version","session_id"},
    "replay":{"version","session_id","after","limit"}, "memory":{"version","session_id"},
    "command_status":{"version","session_id","command_id"}, "submit":{"version","session_id","text"},
    "cancel":{"version","session_id","job_id"}, "decide":{"version","session_id","approval_id","revision","allow"},
}
MUTATIONS = frozenset({"submit", "cancel", "decide"})

class SessionUnavailable(RuntimeError): pass


def identifier(value):
    if not isinstance(value,str) or not IDENT.fullmatch(value): raise ValueError("invalid identifier")
    return value


def clean_fields(data, fields):
    if not isinstance(data,Mapping): raise ValueError("invalid projection")
    result={}
    for name in fields:
        value=data.get(name)
        if value is None or isinstance(value,(bool,int)):
            result[name]=value
        elif isinstance(value,str) and len(value)<=4096:
            result[name]=value
        else: raise ValueError("invalid projected field")
    return result


def clean_list(value, fields):
    if not isinstance(value,list) or len(value)>100: raise ValueError("invalid projected list")
    return [clean_fields(item,fields) for item in value]


def projection(op, result, sid, after=None):
    if not isinstance(result,Mapping) or result.get("contract") != CONTRACT or result.get("session_id") != sid:
        raise ValueError("incompatible provider response")
    out={"contract":CONTRACT,"session_id":sid}
    if op == "snapshot":
        out["jobs"]=clean_list(result.get("jobs"),("job_id","state"))
        out["approvals"]=clean_list(result.get("approvals"),("approval_id","revision","status","expires_at","description"))
        for job in out["jobs"]:
            identifier(job["job_id"])
            if not isinstance(job["state"],str) or not job["state"]: raise ValueError("invalid job")
        for approval in out["approvals"]:
            identifier(approval["approval_id"])
            if not isinstance(approval["revision"],int) or isinstance(approval["revision"],bool) or approval["revision"]<0 or not isinstance(approval["status"],str) or not approval["status"]: raise ValueError("invalid approval")
            if approval["status"]=="pending" and (not isinstance(approval["description"],str) or not approval["description"].strip() or not isinstance(approval["expires_at"],(str,int)) or isinstance(approval["expires_at"],bool)): raise ValueError("incomplete approval")
    elif op == "memory":
        out["items"]=clean_list(result.get("items"),("memory_id","text"))
        for item in out["items"]:
            identifier(item["memory_id"])
            if not isinstance(item["text"],str): raise ValueError("invalid memory projection")
    elif op == "replay":
        events=result.get("events")
        if not isinstance(events,list) or len(events)>100: raise ValueError("invalid event list")
        cursor=after; cleaned=[]
        fields={"message":("sender","text"), "job":("job_id","state"), "approval":("approval_id","status","revision")}
        for event in events:
            if not isinstance(event,Mapping) or not isinstance(event.get("seq"),int) or isinstance(event.get("seq"),bool) or event["seq"]!=cursor+1 or event.get("kind") not in fields: raise ValueError("invalid event sequence")
            cursor=event["seq"]; payload=clean_fields(event.get("payload"),fields[event["kind"]])
            if event["kind"]=="message" and (not isinstance(payload["sender"],str) or not isinstance(payload["text"],str)): raise ValueError("invalid message")
            if event["kind"]=="job":
                identifier(payload["job_id"])
                if not isinstance(payload["state"],str) or not payload["state"]: raise ValueError("invalid job event")
            if event["kind"]=="approval":
                identifier(payload["approval_id"])
                if not isinstance(payload["status"],str) or not isinstance(payload["revision"],int) or isinstance(payload["revision"],bool): raise ValueError("invalid approval event")
            cleaned.append({"seq":cursor,"kind":event["kind"],"payload":payload})
        if result.get("next_cursor")!=cursor: raise ValueError("invalid event cursor")
        out.update(events=cleaned,next_cursor=cursor)
    elif op == "command_status":
        state=result.get("state")
        if state not in {"recorded","unknown"}: raise ValueError("invalid command status")
        recorded=result.get("result")
        if state=="unknown" and recorded is not None: raise ValueError("unexpected command result")
        out.update(command_id=identifier(result.get("command_id")),state=state,
                   result=clean_fields(recorded,("job_id","state","approval_id","status","revision")) if state=="recorded" else None)
    elif op == "submit" or op == "cancel":
        out.update(clean_fields(result,("job_id","state")))
        identifier(out["job_id"])
        if not isinstance(out["state"],str) or not out["state"]: raise ValueError("invalid job state")
    elif op == "decide":
        out.update(clean_fields(result,("approval_id","status","revision")))
        identifier(out["approval_id"])
        if not isinstance(out["revision"],int) or isinstance(out["revision"],bool) or out["status"] not in {"approved","denied"}: raise ValueError("invalid decision")
    return out


class SessionGateway:
    def __init__(self, *, store, gateway, authorize, provider=None):
        if not callable(authorize): raise ValueError("explicit per-session authorization is required")
        self.store,self.gateway,self.authorize,self.provider=store,gateway,authorize,provider

    def call(self,credential,subject,tool,request_id,args):
        prefix="haven.session."
        if not isinstance(tool,str) or not tool.startswith(prefix): raise ValueError("unknown session method")
        op=tool[len(prefix):]
        if op not in SHAPES or not isinstance(args,dict) or set(args)!=SHAPES[op] or args.get("version")!=CONTRACT: raise ValueError("invalid session envelope")
        identifier(request_id)
        sid=identifier(args["session_id"]) if "session_id" in args else None
        connection=self.store.connection_for_credential(hash_credential(credential))
        if connection is None: raise PermissionError("connection unavailable")
        needed=ExternalScope.ACTIONS_REQUEST if op in MUTATIONS else ExternalScope.WORLD_READ
        admission_request=ExternalRequest(connection_id=connection.connection_id,tool=tool,required_scope=needed,
            external_request_id=request_id,subject=subject,client_metadata={"transport":"android-https-v1"})
        admitted=self.gateway.admit(admission_request)
        scope={"submit":SEND,"cancel":CANCEL,"decide":APPROVE}.get(op,READ)
        if admitted.binding is None or admitted.principal.household_id!=connection.household_id or self.authorize(admitted,scope,sid) is not True:
            raise PermissionError("session access denied")
        auth=AuthContext(connection.household_id,admitted.principal.actor_id,frozenset({scope}))
        result = invoke_provider(self.provider, auth, op, request_id, args,
                                 lambda permission,sid: self.authorize(admitted,permission,sid))
        try:
            fresh=self.gateway.admit(admission_request)
            current=(fresh.binding is not None and fresh.binding.binding_id==admitted.binding.binding_id
                     and self.authorize(fresh,scope,sid) is True)
        except Exception:
            current=False
        if not current:
            if op in MUTATIONS: raise RuntimeError("session outcome uncertain after revocation")
            raise SessionDenied("session revoked")
        return result


def invoke_provider(provider, auth, op, request_id, args, authorize):
    sid = args.get("session_id")
    if provider is None: raise SessionUnavailable("no provider configured")
    manifest=provider.capabilities()
    if not isinstance(manifest,Mapping) or manifest.get("contract")!=CONTRACT: raise SessionUnavailable("incompatible provider")
    ops=manifest.get("operations"); guarantees=manifest.get("guarantees")
    if not isinstance(ops,list) or len(ops)>len(OPERATIONS) or any(not isinstance(x,str) or x not in OPERATIONS for x in ops) or len(set(ops))!=len(ops) or not isinstance(guarantees,list) or len(guarantees)>32 or any(not isinstance(x,str) or len(x)>128 for x in guarantees): raise SessionUnavailable("invalid capabilities")
    if op=="capabilities": return {"ok":True,"contract":CONTRACT,"operations":ops,"guarantees":guarantees}
    if op not in ops or not callable(getattr(provider,op,None)): raise SessionUnavailable("operation unavailable")
    if op=="replay" and "durable_replay" not in guarantees: raise SessionUnavailable("durable replay unavailable")
    if op in MUTATIONS and "idempotent_commands" not in guarantees: raise SessionUnavailable("idempotent commands unavailable")
    # Reject malformed mutations before crossing the provider boundary.
    if op=="submit" and (not isinstance(args["text"],str) or not args["text"].strip() or len(args["text"])>4096):
        raise ValueError("invalid text")
    if op=="cancel": identifier(args["job_id"])
    if op=="decide":
        identifier(args["approval_id"])
        if (not isinstance(args["revision"],int) or isinstance(args["revision"],bool)
                or args["revision"]<0 or not isinstance(args["allow"],bool)): raise ValueError("invalid decision")
    if op=="sessions":
        result=provider.sessions(auth)
        if not isinstance(result,Mapping) or result.get("contract")!=CONTRACT or not isinstance(result.get("sessions"),list) or len(result["sessions"])>100: raise ValueError("invalid session list")
        sessions=[]
        for item in result["sessions"]:
            if not isinstance(item,Mapping): raise ValueError("invalid session")
            item_id=identifier(item.get("session_id"))
            if authorize(READ,item_id) is True: sessions.append({"session_id":item_id})
        return {"ok":True,"contract":CONTRACT,"sessions":sessions}
    if op=="replay":
        after=args["after"];limit=args["limit"]
        if not isinstance(after,int) or isinstance(after,bool) or after<0 or not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=100: raise ValueError("invalid replay cursor")
        result=provider.replay(auth,sid,after,limit)
    elif op in {"snapshot","memory"}: result=getattr(provider,op)(auth,sid)
    elif op=="command_status":
        result=provider.command_status(auth,sid,identifier(args["command_id"]))
        if not isinstance(result,Mapping) or result.get("command_id") != args["command_id"]: raise ValueError("mismatched command")
    else:
        # Once a mutation reaches the provider, an exception or malformed
        # response is uncertain; never label it as not executed.
        try:
            if op=="submit":
                text=args["text"]
                if not isinstance(text,str) or not text.strip() or len(text)>4096: raise ValueError("invalid text")
                result=provider.submit(auth,sid,request_id,text)
            elif op=="cancel": result=provider.cancel(auth,sid,request_id,identifier(args["job_id"]))
            else:
                rev=args["revision"];allow=args["allow"]
                if not isinstance(rev,int) or isinstance(rev,bool) or rev<0 or not isinstance(allow,bool): raise ValueError("invalid decision")
                result=provider.decide(auth,sid,request_id,identifier(args["approval_id"]),rev,allow)
            return {"ok":True,**projection(op,result,sid)}
        except (Conflict, SessionDenied): raise
        except Exception as exc: raise RuntimeError("session outcome uncertain") from exc
    return {"ok":True,**projection(op,result,sid,args.get("after"))}
