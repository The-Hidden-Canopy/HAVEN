"""Contract fixture only: one host-owned session shared by multiple clients."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

CONTRACT_DIR=Path(__file__).parents[1] / 'native' / 'Haven.Android' / 'session_contract'
sys.path.insert(0,str(CONTRACT_DIR))
from contract import AuthContext, SessionDenied, Conflict, READ, SEND, CANCEL, APPROVE
from fixture import SessionFixture

ALL=frozenset({READ,SEND,CANCEL,APPROVE})
OWNER=AuthContext('house-1','owner-1',ALL)

@pytest.fixture
def host(tmp_path):
    fixture=SessionFixture(tmp_path / 'fixture.db',clock=lambda:100)
    fixture.seed_session('shared','house-1','owner-1')
    return fixture

def test_close_reopen_and_cross_interface_replay_while_job_continues(host):
    desktop=host.submit(OWNER,'shared','request-1','Summarize the fixture')
    job=desktop['job_id'];host.simulate_job('shared',job,'running')
    mobile=host.replay(OWNER,'shared',0,limit=2)
    cursor=mobile['next_cursor']
    # Closing a view does not signal cancellation or create another agent.
    assert host.snapshot(OWNER,'shared')['jobs'][0]['state']=='running'
    host.simulate_job('shared',job,'completed','Synthetic answer')
    web=host.replay(OWNER,'shared',cursor)
    assert web['events'][-1]['payload']['text']=='Synthetic answer'
    assert host.snapshot(OWNER,'shared')['jobs'][0]['state']=='completed'
    assert host.command_status(OWNER,'shared','request-1')['result']==desktop
    assert host.command_status(OWNER,'shared','unknown')['state']=='unknown'
    assert host.submit(OWNER,'shared','request-1','Summarize the fixture') == desktop
    with pytest.raises(Conflict): host.submit(OWNER,'shared','request-1','different')
    with pytest.raises(Conflict): host.replay(OWNER,'shared',999)

def test_cancel_is_request_not_claimed_stopped(host):
    queued=host.submit(OWNER,'shared','q','One')['job_id']
    assert host.cancel(OWNER,'shared','c1',queued)['state']=='cancelled'
    running=host.submit(OWNER,'shared','r','Two')['job_id'];host.simulate_job('shared',running,'running')
    result=host.cancel(OWNER,'shared','c2',running)
    assert result['state']=='cancel_requested'
    assert host.cancel(OWNER,'shared','c2',running)==result
    host.recover_after_host_restart()
    assert next(j for j in host.snapshot(OWNER,'shared')['jobs'] if j['job_id']==running)['state']=='interrupted_cancel_requested'

def test_approval_requires_fresh_revision_and_expiry_and_is_idempotent(host):
    host.seed_approval('shared','approval-1',101)
    with pytest.raises(Conflict): host.decide(OWNER,'shared','a0','approval-1',0,True)
    result=host.decide(OWNER,'shared','a1','approval-1',1,True)
    assert result['status']=='approved' and host.decide(OWNER,'shared','a1','approval-1',1,True)==result
    with pytest.raises(Conflict): host.decide(OWNER,'shared','a2','approval-1',1,True)
    host.seed_approval('shared','expired',100)
    with pytest.raises(Conflict): host.decide(OWNER,'shared','a3','expired',1,True)

def test_scope_and_session_isolation(host):
    host.submit(OWNER,'shared','msg','Private fixture')
    other=AuthContext('house-1','other',ALL)
    cross=AuthContext('house-2','owner-1',ALL)
    read_only=AuthContext('house-1','owner-1',frozenset({READ}))
    assert other and host.sessions(other)['sessions']==[]
    for identity in (other,cross):
        with pytest.raises(SessionDenied): host.replay(identity,'shared',0)
    with pytest.raises(SessionDenied): host.submit(read_only,'shared','x','test')
    with pytest.raises(SessionDenied): host.cancel(read_only,'shared','x','job')
    with pytest.raises(SessionDenied): host.decide(read_only,'shared','x','a',1,True)

def test_actual_fixture_process_restart_keeps_session_and_marks_job_interrupted(tmp_path):
    db=tmp_path/'restart.db'; env=os.environ.copy();env['PYTHONPATH']=str(CONTRACT_DIR.resolve())+os.pathsep+str(Path(__file__).resolve().parents[1])
    start="""from fixture import SessionFixture
from contract import AuthContext,READ,SEND
import sys
h=SessionFixture(sys.argv[1]);h.seed_session('shared','h','p');c=AuthContext('h','p',frozenset({READ,SEND}));j=h.submit(c,'shared','r','restart message')['job_id'];h.simulate_job('shared',j,'running');print(j)
"""
    job=subprocess.run([sys.executable,'-c',start,str(db)],env=env,check=True,capture_output=True,text=True).stdout.strip()
    reopen="""from fixture import SessionFixture
from contract import AuthContext,READ
import json,sys
h=SessionFixture(sys.argv[1]);h.recover_after_host_restart();c=AuthContext('h','p',frozenset({READ}));print(json.dumps({'snapshot':h.snapshot(c,'shared'),'replay':h.replay(c,'shared',0),'command':h.command_status(c,'shared','r')}))
"""
    result=json.loads(subprocess.run([sys.executable,'-c',reopen,str(db)],env=env,check=True,capture_output=True,text=True).stdout)
    assert result['command']['state']=='recorded' and result['command']['result']['job_id']==job
    assert result['snapshot']['jobs']==[{'job_id':job,'state':'interrupted'}]
    assert any(e['kind']=='message' and e['payload']['text']=='restart message' for e in result['replay']['events'])


def test_wire_adapter_uses_authenticated_context_and_never_accepts_principal_from_client(host):
    from fixture_transport import FixtureTransport
    desktop=FixtureTransport(host,OWNER); phone=FixtureTransport(host,OWNER)
    status, result=desktop.call('haven.session.submit',{'version':'haven-session-1','session_id':'shared','text':'Hello'},'wire-1')
    assert status==200 and result['ok'] and result['job_id']
    status,replay=phone.call('haven.session.replay',{'version':'haven-session-1','session_id':'shared','after':0,'limit':100},'read-1')
    assert status==200 and replay['events'][0]['payload']['text']=='Hello'
    assert phone.call('haven.session.submit',{'version':'haven-session-1','session_id':'shared','text':'Hello'},'wire-1')[1]['job_id']==result['job_id']
    other=FixtureTransport(host,AuthContext('house-1','stranger',ALL))
    assert other.call('haven.session.snapshot',{'version':'haven-session-1','session_id':'shared'},'read')[0]==403
    assert other.call('haven.session.snapshot',{'version':'haven-session-1','session_id':'shared','principal_id':'owner-1'},'read')[0]==400


def test_theme_fixture_preserves_all_desktop_modes_and_token_sets():
    from theme_fixture import read_catalog
    catalog=read_catalog()
    assert [theme['id'] for theme in catalog['themes']]==['haven','canopy','ember','mono']
    assert catalog['modes']==['system','light','dark']
    assert catalog['densities']==['comfortable','compact'] and catalog['motions']==['system','reduced']
    for theme in catalog['themes']:
        for mode in ('light','dark'):
            tokens=catalog['palettes'][theme['id']][mode]
            assert len(tokens)==32 and tokens['HavenCanvasColor'] and tokens['HavenTextColor']
    assert catalog['palettes']['canopy']['dark']['HavenAccentColor']=='#2DD4BF'


def test_memory_is_optional_provider_data_not_fixture_store(host):
    assert 'memory' not in host.capabilities()['operations']
    host.memory_reader=lambda auth,sid:[{'memory_id':'m1','text':'provider-owned fixture'}]
    assert 'memory' in host.capabilities()['operations']
    assert host.memory(OWNER,'shared')['items'][0]['text']=='provider-owned fixture'
    with pytest.raises(SessionDenied): host.memory(AuthContext('house-1','other',ALL),'shared')
    import sqlite3
    with sqlite3.connect(host.path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory'").fetchone() is None


def test_concurrent_duplicate_commands_create_only_one_job(host):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=5) as pool:
        results=list(pool.map(lambda _:host.submit(OWNER,'shared','same-command','once'),range(5)))
    assert len({r['job_id'] for r in results})==1
    assert len(host.snapshot(OWNER,'shared')['jobs'])==1
