"""Durable references, grants and replay for existing host-supplied providers.

No provider, model, worker or memory is constructed here. Provider factories and
session/grant provisioning are trusted host actions, never wire operations.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from .contract import CONTRACT, READ, SEND, CANCEL, APPROVE, Conflict, SessionDenied
from .gateway import OPERATIONS, SessionUnavailable, identifier, projection

SCOPES = frozenset({READ, SEND, CANCEL, APPROVE})


class DurableSessionBinding:
    def __init__(self, path: Path, *, member, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.member, self.clock = member, clock
        self.lock = RLock()
        self.providers = {}
        self.closed = False
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (
                  id TEXT PRIMARY KEY, household TEXT NOT NULL, scope TEXT NOT NULL,
                  provider TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS grants (
                  session TEXT NOT NULL, principal TEXT NOT NULL, permission TEXT NOT NULL,
                  expires REAL NOT NULL, PRIMARY KEY(session,principal,permission));
                CREATE TABLE IF NOT EXISTS commands (
                  session TEXT NOT NULL, principal TEXT NOT NULL, id TEXT NOT NULL,
                  digest TEXT NOT NULL, operation TEXT NOT NULL, result TEXT,
                  PRIMARY KEY(session,principal,id));
                CREATE TABLE IF NOT EXISTS events (
                  session TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL,
                  PRIMARY KEY(session,seq));
            ''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def register_provider(self, provider_id, provider):
        """Host review is required before registering executable provider code."""
        identifier(provider_id)
        manifest = provider.capabilities()
        if (manifest.get('contract') != CONTRACT or not isinstance(manifest.get('operations'), list)
                or any(op not in OPERATIONS for op in manifest['operations'])
                or not isinstance(manifest.get('guarantees'), list)):
            raise ValueError('incompatible provider')
        with self.lock:
            if self.closed or provider_id in self.providers:
                raise ValueError('provider already registered or closed')
            self.providers[provider_id] = provider

    def register_session(self, session_id, *, household_id, scope_id, provider_id):
        for value in (session_id, household_id, scope_id, provider_id): identifier(value)
        with self.lock, self._db() as db:
            row = db.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
            binding = (household_id, scope_id, provider_id)
            if row is not None and tuple(row[k] for k in ('household','scope','provider')) != binding:
                raise Conflict('session binding is immutable')
            db.execute('INSERT OR IGNORE INTO sessions(id,household,scope,provider) VALUES(?,?,?,?)', (session_id,*binding))

    def grant(self, session_id, principal_id, permissions, *, expires):
        """Explicit host provisioning; expiry and current membership both apply."""
        identifier(session_id); identifier(principal_id)
        if (not isinstance(permissions, (set, frozenset, list, tuple)) or not permissions
                or any(p not in SCOPES for p in permissions)
                or isinstance(expires, bool) or not isinstance(expires, (int,float))
                or not self.clock() < expires < float('inf')):
            raise ValueError('invalid session grant')
        with self.lock, self._db() as db:
            if not db.execute('SELECT 1 FROM sessions WHERE id=? AND active=1',(session_id,)).fetchone():
                raise ValueError('session unavailable')
            for permission in permissions:
                db.execute('INSERT OR REPLACE INTO grants VALUES(?,?,?,?)', (session_id,principal_id,permission,expires))

    def revoke(self, session_id, principal_id=None):
        with self.lock, self._db() as db:
            if principal_id is None:
                db.execute('UPDATE sessions SET active=0 WHERE id=?',(session_id,))
                db.execute('DELETE FROM grants WHERE session=?',(session_id,))
            else:
                db.execute('DELETE FROM grants WHERE session=? AND principal=?',(session_id,principal_id))

    def _rows(self, auth, permission, session_id=None):
        if self.closed or permission not in auth.scopes: return []
        with self._db() as db:
            rows = db.execute('''SELECT s.* FROM sessions s JOIN grants g ON s.id=g.session
                WHERE s.household=? AND s.active=1 AND g.principal=? AND g.permission=? AND g.expires>?''',
                (auth.household_id,auth.principal_id,permission,self.clock())).fetchall()
        return [r for r in rows if (session_id is None or r['id']==session_id)
                and self.member(auth.principal_id,r['scope']) is True]

    def allowed(self, auth, permission, session_id=None):
        with self.lock:
            try: return bool(self._rows(auth,permission,session_id))
            except Exception: return False

    def _resolve(self, auth, session_id, permission):
        rows = self._rows(auth,permission,session_id)
        if len(rows)!=1: raise SessionDenied('session denied')
        provider=self.providers.get(rows[0]['provider'])
        if provider is None: raise SessionUnavailable('provider unavailable')
        return provider

    def capabilities(self):
        # Advertise the intersection: a negotiated operation must be supported
        # by every configured provider. Each exact session is checked again.
        with self.lock:
            if self.closed or not self.providers: raise SessionUnavailable('provider unavailable')
            manifests=[p.capabilities() for p in self.providers.values()]
            operations=set.intersection(*(set(m['operations']) for m in manifests))
            guarantees=set.intersection(*(set(m['guarantees']) for m in manifests))
            if 'durable_replay' not in guarantees: operations.discard('replay')
            if 'idempotent_commands' not in guarantees: operations-={'submit','cancel','decide'}
            return {'contract':CONTRACT,'operations':sorted(operations|{'sessions'}),
                    'guarantees':sorted(guarantees&{'durable_replay','idempotent_commands'})}

    def sessions(self, auth):
        with self.lock:
            return {'contract':CONTRACT,'sessions':[{'session_id':r['id']} for r in self._rows(auth,READ)][:100]}

    def _read(self, auth, sid, operation, *args):
        with self.lock:
            provider=self._resolve(auth,sid,READ)
            if operation not in provider.capabilities()['operations']: raise SessionUnavailable('operation unavailable')
            value=getattr(provider,operation)(auth,sid,*args)
            # Revocation may happen in a provider callback; do not leak its result.
            self._resolve(auth,sid,READ)
            return projection(operation,value,sid)

    def snapshot(self, auth, sid): return self._read(auth,sid,'snapshot')
    def memory(self, auth, sid): return self._read(auth,sid,'memory')

    def replay(self, auth, sid, after, limit=100):
        with self.lock:
            provider=self._resolve(auth,sid,READ)
            caps=provider.capabilities()
            if 'replay' not in caps['operations'] or 'durable_replay' not in caps['guarantees']:
                raise SessionUnavailable('provider replay unavailable')
            value=projection('replay',provider.replay(auth,sid,after,limit),sid,after)
            self._resolve(auth,sid,READ)
            with self._db() as db:
                for event in value['events']:
                    payload=json.dumps(event,sort_keys=True,separators=(',',':'))
                    row=db.execute('SELECT payload FROM events WHERE session=? AND seq=?',(sid,event['seq'])).fetchone()
                    if row is not None and row[0]!=payload: raise Conflict('provider rewrote durable replay')
                    db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?)',(sid,event['seq'],payload))
            return value

    def _command(self, auth, sid, command_id, operation, payload):
        permission={'submit':SEND,'cancel':CANCEL,'decide':APPROVE}[operation]
        digest=hashlib.sha256(json.dumps([operation,payload],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        with self.lock:
            provider=self._resolve(auth,sid,permission)
            caps=provider.capabilities()
            if operation not in caps['operations'] or 'idempotent_commands' not in caps['guarantees']:
                raise SessionUnavailable('provider command guarantee unavailable')
            with self._db() as db:
                row=db.execute('SELECT * FROM commands WHERE session=? AND principal=? AND id=?', (sid,auth.principal_id,command_id)).fetchone()
                if row is not None:
                    if row['digest']!=digest: raise Conflict('command ID reused with different payload')
                    if row['result'] is not None: return json.loads(row['result'])
                    raise RuntimeError('prior command outcome uncertain; query status')
                db.execute('INSERT INTO commands VALUES(?,?,?,?,?,NULL)', (sid,auth.principal_id,command_id,digest,operation))
        # Provider owns work/cancel/approval semantics. Persist intent before dispatch,
        # and never replay an incomplete command after a crash or lost response.
        try:
            result=projection(operation,getattr(provider,operation)(auth,sid,command_id,*payload),sid)
        except (Conflict,SessionDenied):
            with self.lock, self._db() as db:
                db.execute('DELETE FROM commands WHERE session=? AND principal=? AND id=? AND result IS NULL',(sid,auth.principal_id,command_id))
            raise
        with self.lock:
            # A revoked in-flight write is uncertain, even if the provider completed.
            if not self.allowed(auth,permission,sid): raise RuntimeError('command outcome uncertain after revocation')
            with self._db() as db:
                db.execute('UPDATE commands SET result=? WHERE session=? AND principal=? AND id=?',
                    (json.dumps(result),sid,auth.principal_id,command_id))
            return result

    def submit(self, auth, sid, command_id, text): return self._command(auth,sid,command_id,'submit',[text])
    def cancel(self, auth, sid, command_id, job_id): return self._command(auth,sid,command_id,'cancel',[job_id])
    def decide(self, auth, sid, command_id, approval_id, revision, allow):
        return self._command(auth,sid,command_id,'decide',[approval_id,revision,allow])

    def command_status(self, auth, sid, command_id):
        with self.lock:
            provider=self._resolve(auth,sid,READ)
            with self._db() as db:
                row=db.execute('SELECT * FROM commands WHERE session=? AND principal=? AND id=?',(sid,auth.principal_id,command_id)).fetchone()
            result=json.loads(row['result']) if row is not None and row['result'] is not None else None
            if row is not None and result is None:
                # Safe read-only reconciliation. Unknown is never non-execution.
                value=projection('command_status',provider.command_status(auth,sid,command_id),sid)
                if value['command_id']!=command_id: raise ValueError('wrong command result')
                if value['state']=='recorded':
                    result=projection(row['operation'],{'contract':CONTRACT,'session_id':sid,**value['result']},sid)
                    self._resolve(auth,sid,READ)
                    with self._db() as db:
                        db.execute('UPDATE commands SET result=? WHERE session=? AND principal=? AND id=?',(json.dumps(result),sid,auth.principal_id,command_id))
            self._resolve(auth,sid,READ)
            return {'contract':CONTRACT,'session_id':sid,'command_id':command_id,
                    'state':'recorded' if result is not None else 'unknown','result':result}

    def close(self):
        with self.lock:
            self.closed=True
            self.providers.clear()
