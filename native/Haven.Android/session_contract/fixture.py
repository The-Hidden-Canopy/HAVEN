"""Synthetic session-provider fixture. Never starts an agent, owns model memory, or executes actions."""
from __future__ import annotations
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contract import AuthContext, SessionDenied, Conflict, CONTRACT, READ, SEND, CANCEL, APPROVE

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, household TEXT NOT NULL, principal TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(session TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(session,seq));
CREATE TABLE IF NOT EXISTS jobs(session TEXT NOT NULL, id TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(session,id));
CREATE TABLE IF NOT EXISTS approvals(session TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL, expires INTEGER NOT NULL, description TEXT NOT NULL, PRIMARY KEY(session,id));
CREATE TABLE IF NOT EXISTS commands(session TEXT NOT NULL, principal TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(session,principal,id));
"""

class SessionFixture:
    def __init__(self, path, clock=None, memory_reader=None):
        self.path=str(path); self.clock=clock or time.time; self.memory_reader=memory_reader
        with sqlite3.connect(self.path) as db: db.executescript(SCHEMA)
    @contextmanager
    def tx(self):
        db=sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("BEGIN IMMEDIATE"); yield db; db.commit()
        except BaseException:
            db.rollback(); raise
        finally: db.close()
    def capabilities(self):
        ops=["sessions","snapshot","replay","command_status","submit","cancel","decide"]
        if self.memory_reader is not None: ops.append("memory")
        return {"contract":CONTRACT,"operations":ops,"guarantees":["durable_replay","idempotent_commands"]}
    def seed_session(self, session_id, household, principal):
        with self.tx() as db: db.execute("INSERT INTO sessions VALUES (?,?,?)", (session_id,household,principal))
    def _auth(self, db, auth, sid, scope):
        if not isinstance(auth, AuthContext) or scope not in auth.scopes: raise SessionDenied("session unavailable")
        row=db.execute("SELECT household,principal FROM sessions WHERE id=?", (sid,)).fetchone()
        if row != (auth.household_id,auth.principal_id): raise SessionDenied("session unavailable")
    def _event(self, db, sid, kind, payload):
        seq=db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE session=?",(sid,)).fetchone()[0]
        db.execute("INSERT INTO events VALUES (?,?,?,?)",(sid,seq,kind,json.dumps(payload,sort_keys=True)))
    def _command(self, db, auth, sid, request_id, operation, args, fn):
        if not isinstance(request_id,str) or not 1<=len(request_id)<=128: raise ValueError("invalid request id")
        digest=hashlib.sha256(json.dumps([operation,args],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        row=db.execute("SELECT digest,result FROM commands WHERE session=? AND principal=? AND id=?",(sid,auth.principal_id,request_id)).fetchone()
        if row:
            if row[0]!=digest: raise Conflict("request id reused with different payload")
            return json.loads(row[1])
        result=fn(); db.execute("INSERT INTO commands VALUES (?,?,?,?,?)",(sid,auth.principal_id,request_id,digest,json.dumps(result)))
        return result
    def sessions(self, auth):
        if not isinstance(auth,AuthContext) or READ not in auth.scopes: raise SessionDenied("session unavailable")
        with self.tx() as db:
            rows=db.execute("SELECT id FROM sessions WHERE household=? AND principal=? ORDER BY id",(auth.household_id,auth.principal_id)).fetchall()
            return {"contract":CONTRACT,"sessions":[{"session_id":r[0]} for r in rows]}
    def snapshot(self, auth, sid):
        with self.tx() as db:
            self._auth(db,auth,sid,READ)
            jobs=[{"job_id":i,"state":s} for i,s in db.execute("SELECT id,state FROM jobs WHERE session=? ORDER BY id",(sid,))]
            approvals=[{"approval_id":i,"revision":v,"status":s,"expires_at":e,"description":d} for i,v,s,e,d in db.execute("SELECT id,revision,status,expires,description FROM approvals WHERE session=? ORDER BY id",(sid,))]
            return {"contract":CONTRACT,"session_id":sid,"jobs":jobs,"approvals":approvals}
    def replay(self, auth, sid, after, limit=100):
        if not isinstance(after,int) or isinstance(after,bool) or after<0 or not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=100: raise ValueError("invalid cursor")
        with self.tx() as db:
            self._auth(db,auth,sid,READ)
            latest=db.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE session=?", (sid,)).fetchone()[0]
            if after > latest: raise Conflict("cursor is ahead of this session")
            rows=db.execute("SELECT seq,kind,payload FROM events WHERE session=? AND seq>? ORDER BY seq LIMIT ?",(sid,after,limit)).fetchall()
            events=[{"seq":seq,"kind":kind,"payload":json.loads(payload)} for seq,kind,payload in rows]
            return {"contract":CONTRACT,"session_id":sid,"events":events,"next_cursor":events[-1]["seq"] if events else after}
    def command_status(self, auth, sid, command_id):
        with self.tx() as db:
            self._auth(db,auth,sid,READ)
            row=db.execute("SELECT result FROM commands WHERE session=? AND principal=? AND id=?",(sid,auth.principal_id,command_id)).fetchone()
            return {"contract":CONTRACT,"session_id":sid,"command_id":command_id,"state":"recorded" if row else "unknown", "result":json.loads(row[0]) if row else None}
    def memory(self, auth, sid):
        with self.tx() as db:
            self._auth(db,auth,sid,READ)
            if self.memory_reader is None: raise ValueError("provider memory unavailable")
            return {"contract":CONTRACT,"session_id":sid,"items":self.memory_reader(auth,sid)}
    def submit(self, auth, sid, request_id, text):
        if not isinstance(text,str) or not text.strip() or len(text)>4096: raise ValueError("invalid message")
        with self.tx() as db:
            self._auth(db,auth,sid,SEND)
            def apply():
                job='job_'+uuid.uuid4().hex
                db.execute("INSERT INTO jobs VALUES (?,?,?)",(sid,job,'queued'))
                self._event(db,sid,'message',{'sender':'user','text':text})
                self._event(db,sid,'job',{'job_id':job,'state':'queued'})
                return {'contract':CONTRACT,'session_id':sid,'job_id':job,'state':'queued'}
            return self._command(db,auth,sid,request_id,'submit',{'text':text},apply)
    def cancel(self, auth, sid, request_id, job_id):
        with self.tx() as db:
            self._auth(db,auth,sid,CANCEL)
            def apply():
                row=db.execute("SELECT state FROM jobs WHERE session=? AND id=?",(sid,job_id)).fetchone()
                if row is None or row[0] not in ('queued','running','interrupted'): raise Conflict("job not cancellable")
                state='cancelled' if row[0]=='queued' else 'cancel_requested'
                db.execute("UPDATE jobs SET state=? WHERE session=? AND id=?",(state,sid,job_id));self._event(db,sid,'job',{'job_id':job_id,'state':state})
                return {'contract':CONTRACT,'session_id':sid,'job_id':job_id,'state':state}
            return self._command(db,auth,sid,request_id,'cancel',{'job_id':job_id},apply)
    def decide(self, auth, sid, request_id, approval_id, revision, allow):
        if not isinstance(revision,int) or isinstance(revision,bool) or not isinstance(allow,bool): raise ValueError("invalid approval")
        with self.tx() as db:
            self._auth(db,auth,sid,APPROVE)
            def apply():
                row=db.execute("SELECT revision,status,expires FROM approvals WHERE session=? AND id=?",(sid,approval_id)).fetchone()
                if row is None or row[0]!=revision or row[1]!='pending' or row[2]<=int(self.clock()): raise Conflict("stale approval")
                status='approved' if allow else 'denied'
                db.execute("UPDATE approvals SET revision=revision+1,status=? WHERE session=? AND id=?",(status,sid,approval_id));self._event(db,sid,'approval',{'approval_id':approval_id,'status':status,'revision':revision+1})
                return {'contract':CONTRACT,'session_id':sid,'approval_id':approval_id,'status':status,'revision':revision+1}
            return self._command(db,auth,sid,request_id,'decide',{'approval_id':approval_id,'revision':revision,'allow':allow},apply)
    # The following controls are fixture-only, not client methods.
    def simulate_job(self,sid,job_id,state,text=None):
        if state not in ('running','completed','cancelled'): raise ValueError('unsupported fixture state')
        with self.tx() as db:
            changed=db.execute("UPDATE jobs SET state=? WHERE session=? AND id=?",(state,sid,job_id)).rowcount
            if changed!=1: raise ValueError('unknown job')
            self._event(db,sid,'job',{'job_id':job_id,'state':state})
            if text: self._event(db,sid,'message',{'sender':'haven','text':text})
    def seed_approval(self,sid,approval_id,expires,description="Synthetic fixture approval"):
        with self.tx() as db:
            db.execute("INSERT INTO approvals VALUES (?,?,1,'pending',?,?)",(sid,approval_id,expires,description));self._event(db,sid,'approval',{'approval_id':approval_id,'status':'pending','revision':1})
    def recover_after_host_restart(self):
        # A real worker must explicitly prove resume safety; never claim it ran.
        with self.tx() as db:
            rows=db.execute("SELECT session,id,state FROM jobs WHERE state IN ('running','cancel_requested')").fetchall()
            for sid,job,previous in rows:
                state='interrupted_cancel_requested' if previous=='cancel_requested' else 'interrupted'
                db.execute("UPDATE jobs SET state=? WHERE session=? AND id=?",(state,sid,job));self._event(db,sid,'job',{'job_id':job,'state':state})
