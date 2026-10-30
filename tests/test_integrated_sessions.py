"""Durability, explicit grants, interrupted commands and real host dispatch."""
from dataclasses import replace
from threading import Event, Thread
import sqlite3
import pytest

from haven.session_bridge.contract import CONTRACT, AuthContext, READ, SEND, CANCEL, APPROVE, Conflict, SessionDenied
from haven.session_bridge.durable import DurableSessionBinding
from haven.web.server import make_server
from haven.ipc import request_message

AUTH=AuthContext('house','owner',frozenset({READ,SEND,CANCEL,APPROVE}))


class Provider:
    def __init__(self): self.calls=0; self.results={}; self.fail=False; self.hook=lambda:None; self.message='hello'
    def capabilities(self): return {'contract':CONTRACT,'operations':['sessions','snapshot','replay','command_status','submit','cancel','decide'], 'guarantees':['durable_replay','idempotent_commands']}
    def snapshot(self,a,s): return {'contract':CONTRACT,'session_id':s,'jobs':[],'approvals':[]}
    def submit(self,a,s,key,text):
        self.calls+=1; self.hook()
        self.results[key]={'contract':CONTRACT,'session_id':s,'job_id':'job','state':'queued'}
        if self.fail: raise RuntimeError('lost acknowledgement')
        return self.results[key]
    def command_status(self,a,s,key): return {'contract':CONTRACT,'session_id':s,'command_id':key,'state':'recorded' if key in self.results else 'unknown','result':self.results.get(key)}
    def replay(self,a,s,after,limit):
        events=[{'seq':1,'kind':'message','payload':{'sender':'provider','text':self.message}}] if after==0 else []
        return {'contract':CONTRACT,'session_id':s,'events':events,'next_cursor':after+len(events)}


def binding(tmp_path, provider=None):
    b=DurableSessionBinding(tmp_path/'sessions.db',member=lambda p,s:(p,s)==('owner','scope'),clock=lambda:100)
    b.register_provider('existing',provider or Provider())
    b.register_session('session',household_id='house',scope_id='scope',provider_id='existing')
    return b


def test_explicit_grants_expiry_scope_and_restart(tmp_path):
    b=binding(tmp_path)
    assert not b.allowed(AUTH,READ,'session')
    b.grant('session','owner',{READ,SEND},expires=101)
    assert b.allowed(AUTH,READ,'session')
    assert not b.allowed(replace(AUTH,household_id='foreign'),READ,'session')
    assert not b.allowed(replace(AUTH,principal_id='foreign'),READ,'session')
    assert not b.allowed(AUTH,APPROVE,'session')
    b.submit(AUTH,'session','stable','hello'); b.close()
    resumed=binding(tmp_path)
    assert resumed.allowed(AUTH,READ,'session')
    assert resumed.submit(AUTH,'session','stable','hello')['job_id']=='job'
    assert resumed.providers['existing'].calls==0
    with pytest.raises(Conflict): resumed.submit(AUTH,'session','stable','different')
    resumed.clock=lambda:102
    assert not resumed.allowed(AUTH,READ,'session')
    with pytest.raises(SessionDenied): resumed.snapshot(AUTH,'session')


def test_crash_marker_never_redispatches_until_status_reconciles(tmp_path):
    p=Provider(); b=binding(tmp_path,p); b.grant('session','owner',{READ,SEND},expires=200)
    p.fail=True
    with pytest.raises(RuntimeError): b.submit(AUTH,'session','stable','hello')
    b.close(); b=binding(tmp_path,p)
    with pytest.raises(RuntimeError): b.submit(AUTH,'session','stable','hello')
    assert p.calls==1
    assert b.command_status(AUTH,'session','stable')['state']=='recorded'
    assert b.submit(AUTH,'session','stable','hello')['job_id']=='job'
    assert p.calls==1
    assert b.command_status(AUTH,'session','unseen')['state']=='unknown'


def test_concurrent_duplicate_and_revoked_inflight_result(tmp_path):
    p=Provider(); b=binding(tmp_path,p); b.grant('session','owner',{READ,SEND},expires=200)
    entered,release=Event(),Event(); errors=[]
    p.hook=lambda:(entered.set(),release.wait(5))
    def dispatch():
        try: b.submit(AUTH,'session','stable','hello')
        except Exception as exc: errors.append(exc)
    thread=Thread(target=dispatch); thread.start(); assert entered.wait(5)
    with pytest.raises(RuntimeError): b.submit(AUTH,'session','stable','hello')
    b.revoke('session','owner'); release.set(); thread.join(5)
    assert len(errors)==1 and p.calls==1
    assert not b.allowed(AUTH,READ,'session')


def test_replay_journal_rejects_provider_rewrite(tmp_path):
    p=Provider(); b=binding(tmp_path,p); b.grant('session','owner',{READ},expires=200)
    assert b.replay(AUTH,'session',0)['next_cursor']==1
    b.close(); b=binding(tmp_path,p); p.message='rewritten'
    with pytest.raises(Conflict): b.replay(AUTH,'session',0)
    with sqlite3.connect(b.path) as db:
        assert 'hello' in db.execute('SELECT payload FROM events').fetchone()[0]


def test_capabilities_intersect_actual_provider_guarantees(tmp_path):
    b=binding(tmp_path)
    class Limited(Provider):
        def capabilities(self):return {'contract':CONTRACT,'operations':['snapshot'],'guarantees':[]}
    b.register_provider('limited',Limited())
    caps=b.capabilities()
    assert caps['operations']==['sessions','snapshot'] and caps['guarantees']==[]


def test_same_wire_validator_through_real_ipc_and_local_owner(tmp_path):
    server,_=make_server(0,data_dir=tmp_path,models_root=tmp_path/'models',session_token='synthetic')
    try:
        principal=server.identity.current_principal().actor_id; scope=server.identity.personal_scope_id
        b=server.local_workflows.sessions
        b.register_provider('existing',Provider())
        b.register_session('session',household_id=server.director.household_id,scope_id=scope,provider_id='existing')
        b.grant('session',principal,{READ,SEND},expires=2**40)
        dispatcher=server.build_ipc_dispatcher()
        def call(op,**args):
            response=dispatcher(request_message('ipc-'+op,'sessions.call',{'tool':'haven.session.'+op,'request_id':'stable-'+op,'arguments':{'version':CONTRACT,**args}}))
            return response['result']
        assert call('capabilities')['ok']
        assert call('submit',session_id='session',text='hello')['job_id']=='job'
        assert call('replay',session_id='session',after=0,limit=100)['events'][0]['seq']==1
        assert not call('submit',session_id='foreign',text='hello')['ok']
        assert not call('unknown')['ok']
        retained=server.local_workflows.session_routes
        server.rebuild_director()
        assert not retained.call({'tool':'haven.session.sessions','request_id':'closed','arguments':{'version':CONTRACT}})['ok']
    finally: server.server_close()
