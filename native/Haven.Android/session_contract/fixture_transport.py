"""Synthetic transport adapter for the proposed shared agent contract.

`auth` is supplied by the test host, never decoded from client arguments.
It is not attached to the current production HTTP or IPC server.
"""
from contract import SessionDenied, Conflict, CONTRACT

class FixtureTransport:
    def __init__(self, backend, auth): self.backend,self.auth=backend,auth
    def call(self, tool, arguments, request_id):
        if not isinstance(arguments,dict) or arguments.get("version") != CONTRACT: return 400, {"ok":False,"code":"not_executed"}
        arguments={key:value for key,value in arguments.items() if key!="version"}
        shapes={
            "haven.session.capabilities":set(),
            "haven.session.sessions":set(),
            "haven.session.snapshot":{"session_id"},
            "haven.session.replay":{"session_id","after","limit"},
            "haven.session.memory":{"session_id"},
            "haven.session.command_status":{"session_id","command_id"},
            "haven.session.submit":{"session_id","text"},
            "haven.session.cancel":{"session_id","job_id"},
            "haven.session.decide":{"session_id","approval_id","revision","allow"},
        }
        if tool not in shapes or set(arguments)!=shapes[tool]: return 400, {"ok":False,"code":"not_executed"}
        try:
            if tool=="haven.session.capabilities":
                self.backend.sessions(self.auth)  # require the read scope even for metadata
                value=self.backend.capabilities()
            elif tool=="haven.session.sessions": value=self.backend.sessions(self.auth)
            elif tool=="haven.session.snapshot": value=self.backend.snapshot(self.auth,arguments["session_id"])
            elif tool=="haven.session.replay": value=self.backend.replay(self.auth,arguments["session_id"],arguments["after"],arguments["limit"])
            elif tool=="haven.session.command_status": value=self.backend.command_status(self.auth,arguments["session_id"],arguments["command_id"])
            elif tool=="haven.session.memory": value=self.backend.memory(self.auth,arguments["session_id"])
            elif tool=="haven.session.submit": value=self.backend.submit(self.auth,arguments["session_id"],request_id,arguments["text"])
            elif tool=="haven.session.cancel": value=self.backend.cancel(self.auth,arguments["session_id"],request_id,arguments["job_id"])
            else: value=self.backend.decide(self.auth,arguments["session_id"],request_id,arguments["approval_id"],arguments["revision"],arguments["allow"])
            return 200, {"ok":True,**value}
        except SessionDenied:
            return 403,{"ok":False,"code":"access_denied"}
        except Conflict:
            return 409,{"ok":False,"code":"stale_approval" if tool=="haven.session.decide" else "not_executed"}
        except ValueError:
            return 400,{"ok":False,"code":"not_executed"}
        except Exception:
            return 500,{"ok":False,"code":"outcome_uncertain"}
