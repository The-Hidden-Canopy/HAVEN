"""HTTPS adapter integration exercises the real HAVEN demo authority path."""
import datetime
import http.client
import ipaddress
import json
from pathlib import Path
import ssl
import subprocess
import sys
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from haven.android_bridge import BridgeServer, dispatch, phone_services, pending_belongs_to_connection
from haven.core.domain import Principal, RoleTier
from haven.external_agents.domain import ExternalAgentConnection, ExternalProvider, ExternalScope, PrincipalBinding
from haven.external_agents.gateway import ExternalAgentGateway
from haven.external_agents.reads import ExternalReadTools
from haven.external_agents.store import ExternalAgentStore
from haven.external_agents.transport import TransportBridge, hash_credential
from haven.web.demo import DemoDirector, HOUSEHOLD_ID

TOKEN = "synthetic-test-only"
NOW = datetime.datetime(2026, 9, 16, 20, 0, tzinfo=datetime.timezone.utc)

@pytest.fixture
def composition(tmp_path):
    director = DemoDirector(clock=lambda: NOW)
    store = ExternalAgentStore(tmp_path / "external.db")
    store.save_connection(ExternalAgentConnection(connection_id="test", household_id=HOUSEHOLD_ID,
        provider=ExternalProvider.MCP_CLIENT, display_name="Synthetic", enabled=True,
        credential_hash=hash_credential(TOKEN), created_at=NOW, created_by="owner"))
    store.save_binding(PrincipalBinding(binding_id="b", connection_id="test", subject_key="fixed-subject",
        subject_label="Synthetic", principal_id="ada", scopes=frozenset({ExternalScope.WORLD_READ, ExternalScope.ACTIONS_REQUEST}),
        created_by="owner", created_at=NOW))
    person = Principal(actor_id="ada", household_id=HOUSEHOLD_ID, role_tier=RoleTier.MEMBER)
    gateway = ExternalAgentGateway(store=store, household_id=HOUSEHOLD_ID, resolve_principal={"ada": person}.get, clock=lambda: NOW)
    bridge = TransportBridge(store=store, gateway=gateway, reads=ExternalReadTools(gateway=gateway, reader=director), executor=director)
    return director, store, bridge, {hash_credential(TOKEN): "fixed-subject"}

def body(tool, ident, args=None):
    return {"tool": tool, "request_id": ident, "arguments": args or {}}

def test_real_authority_evidence_confirmation_and_idempotency(composition):
    director, store, bridge, subjects = composition
    status, world = dispatch(bridge, TOKEN, subjects, body("haven.world.get", "read-1"))
    assert status == 200 and world["ok"] and world["state"]["rooms"]
    action = body("haven.action.request", "action-1", {"device_id": "garage_door", "service": "cover.close"})
    status, pending = dispatch(bridge, TOKEN, subjects, action)
    assert status == 200 and pending["status"] == "confirmation_required"
    assert not any(a.status.value == "executed" for a in director.store.state.actions)
    assert dispatch(bridge, TOKEN, subjects, action)[1] == pending
    assert dispatch(bridge, TOKEN, subjects, body("haven.action.request", "action-1", {"device_id": "office_light", "service": "light.turn_off"}))[0] == 403
    status, confirmed = dispatch(bridge, TOKEN, subjects, body("haven.action.confirm", "confirm-1", {"request_id": pending["request_id"]}), pending_checker=lambda token, request_id: pending_belongs_to_connection(store, director, token, request_id))
    assert status == 200 and confirmed["status"] == "confirmed"
    assert director.receipts[-1].external_source
    assert len([a for a in director.store.state.actions if a.status.value == "executed"]) == 1

def test_rejects_spoofed_subject_unknown_credentials_and_scope(composition):
    director, store, bridge, subjects = composition
    initial_actions = tuple(director.store.state.actions)
    forged = body("haven.world.get", "read-2") | {"subject": "someone-else"}
    assert dispatch(bridge, TOKEN, subjects, forged)[0] == 400
    assert dispatch(bridge, "bad-token", subjects, body("haven.world.get", "read-3"))[0] == 401
    assert dispatch(bridge, TOKEN, {hash_credential(TOKEN): None}, body("haven.action.request", "action-2", {"device_id": "office_light", "service": "light.turn_off"}))[0] == 403
    assert director.store.state.actions == initial_actions

def test_failure_does_not_claim_command_was_rolled_back():
    class Broken:
        def call_tool(self, **kwargs): raise RuntimeError("secret detail")
    status, response = dispatch(Broken(), TOKEN, {hash_credential(TOKEN): None}, body("haven.world.get", "failure"))
    assert status == 500 and response == {"ok": False, "code": "outcome_uncertain"}

def test_tls_http_round_trip_and_rejection(composition, tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
        .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); tls.load_cert_chain(cert_path, key_path)
    director, store, bridge, subjects = composition
    from haven.android_bridge.product_host import LatchAdapter, ProductHost, VERSION
    host = ProductHost(store, bridge._gateway, {"latch": LatchAdapter(LatchFixture())}, lambda *args: True, lambda: NOW)
    import sys
    from haven.session_bridge.gateway import SessionGateway
    sys.path.insert(0,str(Path(__file__).parents[1]/'native'/'Haven.Android'/'session_contract'))
    from fixture import SessionFixture
    provider=SessionFixture(tmp_path/'https-session.db');provider.seed_session('shared',HOUSEHOLD_ID,'ada')
    session_host=SessionGateway(store=store,gateway=bridge._gateway,authorize=lambda *args:True,provider=provider)
    server = BridgeServer(("127.0.0.1", 0), bridge, subjects, tls, product_host=host, session_host=session_host)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        client = http.client.HTTPSConnection("127.0.0.1", server.server_port, context=ssl.create_default_context(cafile=str(cert_path)), timeout=5)
        client.request("POST", "/v1/tool", json.dumps(body("haven.world.get", "http-1")), {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
        response = client.getresponse(); result = json.loads(response.read()); client.close()
        assert response.status == 200 and result["ok"] and result["state"]["rooms"]
        client = http.client.HTTPSConnection("127.0.0.1", server.server_port, context=ssl.create_default_context(cafile=str(cert_path)), timeout=5)
        client.request("POST", "/v1/tool", json.dumps(body("haven.world.get", "http-2")), {"Authorization": "Bearer wrong", "Content-Type": "application/json"})
        response = client.getresponse(); response.read(); client.close(); assert response.status == 401
        client = http.client.HTTPSConnection("127.0.0.1", server.server_port, context=ssl.create_default_context(cafile=str(cert_path)), timeout=5)
        client.request("POST", "/v1/tool", json.dumps(body("haven.product.read", "product-http-1", {"id":"latch", "version":VERSION, "section":"commitments"})), {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
        response = client.getresponse(); result = json.loads(response.read()); client.close()
        assert response.status == 200 and result["data"]["items"][0]["provider"] == "Example"
        client = http.client.HTTPSConnection("127.0.0.1", server.server_port, context=ssl.create_default_context(cafile=str(cert_path)), timeout=5)
        client.request("POST", "/v1/tool", json.dumps(body("haven.session.sessions", "session-http-1", {"version":"haven-session-1"})), {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
        response = client.getresponse(); result = json.loads(response.read()); client.close()
        assert response.status == 200 and result["sessions"] == [{"session_id":"shared"}]
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
    # Exercise the runnable synthetic composition as well as the adapter itself.
    proc = subprocess.Popen([sys.executable, "-u", "-m", "haven.android_bridge", "--cert", str(cert_path), "--key", str(key_path), "--port", "0"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert "SYNTHETIC DEMO ONLY" in proc.stdout.readline()
        words = proc.stdout.readline().split()
        port, token = int(words[2]), words[4]
        client = http.client.HTTPSConnection("127.0.0.1", port, context=ssl.create_default_context(cafile=str(cert_path)), timeout=5)
        client.request("POST", "/v1/tool", json.dumps(body("haven.world.get", "demo-http-1")), {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        response = client.getresponse(); result = json.loads(response.read()); client.close()
        assert response.status == 200 and result["state"]["rooms"]
        devices = {d["id"]: d for r in result["state"]["rooms"] for d in r["devices"]}
        assert devices["office_light"]["phone_services"] == ["light.turn_off"]
    finally:
        proc.terminate(); proc.communicate(timeout=10)



def test_phone_capabilities_follow_registry_and_world_read_admission(composition):
    director, store, bridge, subjects = composition
    services = lambda device_id: phone_services(director.registry, device_id)
    status, result = dispatch(bridge, TOKEN, subjects, body("haven.world.get", "phone-world"), services)
    devices = {d["id"]: d for r in result["state"]["rooms"] for d in r["devices"]}
    assert status == 200
    assert devices["office_light"]["phone_services"] == ["light.turn_off"]
    assert devices["garage_door"]["phone_services"] == ["cover.close", "cover.open"]
    assert devices["kitchen_thermostat"]["phone_services"] == []
    assert services("missing") == []
    called = []
    assert dispatch(bridge, "wrong", subjects, body("haven.world.get", "bad-world"), lambda i: called.append(i))[0] == 401
    assert called == []


def test_actions_do_not_leak_full_local_application_state(composition):
    director, store, bridge, subjects = composition
    status, response = dispatch(bridge, TOKEN, subjects, body("haven.action.request", "phone-action", {"device_id": "office_light", "service": "light.turn_off"}))
    assert status == 200 and response["ok"] and response["status"] == "executed"
    assert "state" not in response
    status, pending = dispatch(bridge, TOKEN, subjects, body("haven.action.request", "phone-garage", {"device_id": "garage_door", "service": "cover.close"}))
    assert status == 200 and pending["status"] == "confirmation_required"
    status, confirmed = dispatch(bridge, TOKEN, subjects, body("haven.action.confirm", "phone-confirm", {"request_id": pending["request_id"]}), pending_checker=lambda token, request_id: pending_belongs_to_connection(store, director, token, request_id))
    assert status == 200 and confirmed["status"] == "confirmed" and "state" not in confirmed
    assert confirmed["receipt_id"]

class LatchFixture:
    def __init__(self): self.proposals=0
    def list_interventions(self): return [{"commitment_id":"com_1", "kind":"subscription", "provider":"Example", "next_event_at":"2026-11-01", "message":"Renews soon", "private":"not exported"}]
    def list_access(self): return {"items":[{"id":"access_1", "grantee":{"id":"guest", "type":"user"}, "resource":{"id":"repo", "type":"repository"}, "stale":True}]}
    def list_review_proposals(self): return {"items":[{"proposal_id":"review_1", "status":"proposed", "evidence":[{"id":"ev_1", "source":"fixture", "acquired_at":"2026-01-01", "fresh":True}]}]}
    def list_action_plans(self): return {"items":[{"plan_id":"plan_1", "status":"proposed"}]}
    def get_evidence(self, identifier, include_content=False):
        assert not include_content
        return {"id":identifier,"source_kind":"manual","mime_type":"text/plain","content":"private content"}
    def get_commitment(self, identifier): return {"id":identifier,"facts":{"provider":{"value":"Example"},"password":{"value":"secret"}}}
    def propose_cancellation(self, identifier): self.proposals+=1; return {"plan_id":"plan_1", "status":"proposed", "commitment_id":identifier}


def test_product_host_scoped_latch_read_and_proposal(composition):
    from haven.android_bridge.product_host import LatchAdapter, HarborAdapter, ProductHost, VERSION
    director, store, bridge, subjects = composition
    latch=LatchFixture()
    host=ProductHost(store, bridge._gateway, {"latch":LatchAdapter(latch), "harbor":HarborAdapter()}, lambda admitted, product, operation: True, lambda: NOW)
    def call(tool, ident, section=None, item_id=None, request_id="product-1", token=TOKEN):
        args={"id":ident,"version":VERSION}
        if section: args["section"]=section
        if item_id: args["item_id"]=item_id
        return dispatch(bridge, token, subjects, body(tool,request_id,args), product_host=host)
    status, description=call("haven.product.describe","latch")
    assert status==200 and "propose_cancellation" in description["descriptor"]["capabilities"]
    assert call("haven.product.read","latch","commitments",request_id="p2")[1]["data"]["items"][0]["provider"] == "Example"
    assert call("haven.product.read","latch","access",request_id="p3")[1]["data"]["items"][0]["stale"] is True
    assert call("haven.product.read","latch","reviews",request_id="p4")[1]["data"]["items"][0]["evidence"][0]["id"] == "ev_1"
    assert "content" not in call("haven.product.read","latch","evidence","ev_1",request_id="p5")[1]["data"]["item"]
    assert "password" not in call("haven.product.read","latch","commitment_detail","com_1",request_id="p6")[1]["data"]["item"]["facts"]
    first=call("haven.product.propose","latch","propose_cancellation","com_1",request_id="p7")
    assert first[0]==200 and first[1]["data"]["item"]["status"]=="proposed"
    assert call("haven.product.propose","latch","propose_cancellation","com_1",request_id="p7")==first
    assert latch.proposals==1
    assert call("haven.product.propose","latch","execute","com_1",request_id="p8")[0]==400
    host.authorize=lambda *args: False
    assert call("haven.product.read","latch","commitments",request_id="p9")[0]==403
    assert call("haven.product.read","latch","commitments",request_id="p10",token="invalid")[0]==401


def test_product_harbor_metadata_is_allowlisted_locked_and_no_verification(composition):
    from haven.android_bridge.product_host import LatchAdapter, HarborAdapter, ProductHost, VERSION
    _, store, bridge, subjects = composition
    state={"state":"unlocked","snapshot":{"objects":[{"object_id":"one","display_label":"Fixture","object_type":"credential","secret":"NEVER EXPORT"}],"requests":[],"history":[],"app_grants":[]},"password":"NEVER EXPORT"}
    harbor=HarborAdapter(lambda: state)
    host=ProductHost(store,bridge._gateway,{"harbor":harbor},lambda *args: True,lambda: NOW)
    args={"id":"harbor","version":VERSION,"section":"metadata"}
    status,response=dispatch(bridge,TOKEN,subjects,body("haven.product.read","harbor-1",args),product_host=host)
    assert status==200 and response["data"]["snapshot"]["objects"][0]["display_label"]=="Fixture"
    assert "NEVER EXPORT" not in json.dumps(response)
    state["state"]="locked"
    response=dispatch(bridge,TOKEN,subjects,body("haven.product.read","harbor-2",args),product_host=host)[1]
    assert response["data"] == {"state":"locked","snapshot":{}}
    args["section"]="unlock"
    assert dispatch(bridge,TOKEN,subjects,body("haven.product.propose","harbor-3",args),product_host=host)[0]==400
    args["version"]="unknown"
    assert dispatch(bridge,TOKEN,subjects,body("haven.product.read","harbor-4",args),product_host=host)[0]==400


def test_confirmation_rejects_different_connection_even_for_same_principal(composition):
    director, store, bridge, subjects = composition
    other="other-synthetic-credential"
    store.save_connection(ExternalAgentConnection(connection_id="other", household_id=HOUSEHOLD_ID,
        provider=ExternalProvider.MCP_CLIENT, display_name="Other", enabled=True,
        credential_hash=hash_credential(other), created_at=NOW, created_by="owner"))
    store.save_binding(PrincipalBinding(binding_id="other-binding", connection_id="other", subject_key="other-subject",
        subject_label="Other", principal_id="ada", scopes=frozenset({ExternalScope.ACTIONS_REQUEST}), created_by="owner", created_at=NOW))
    subjects[hash_credential(other)]="other-subject"
    status, pending = dispatch(bridge, TOKEN, subjects, body("haven.action.request", "own-action", {"device_id":"garage_door", "service":"cover.close"}))
    assert status == 200 and pending["status"] == "confirmation_required"
    checker=lambda credential, request_id: pending_belongs_to_connection(store, director, credential, request_id)
    confirm=body("haven.action.confirm", "other-confirm", {"request_id":pending["request_id"]})
    assert dispatch(bridge, other, subjects, confirm, pending_checker=checker)[0] == 403
    assert dispatch(bridge, TOKEN, subjects, body("haven.action.confirm", "owner-confirm", {"request_id":pending["request_id"]}), pending_checker=checker)[1]["status"] == "confirmed"


def test_product_read_never_allows_unbound_world_scope(composition):
    from haven.android_bridge.product_host import LatchAdapter, ProductHost, VERSION
    _, store, bridge, subjects=composition
    credential="unbound-synthetic"
    store.save_connection(ExternalAgentConnection(connection_id="unbound",household_id=HOUSEHOLD_ID,
        provider=ExternalProvider.MCP_CLIENT,display_name="Unbound",enabled=True,created_at=NOW,created_by="owner",
        credential_hash=hash_credential(credential),unbound_scopes=frozenset({ExternalScope.WORLD_READ})))
    subjects[hash_credential(credential)] = None
    fixture=LatchFixture()
    host=ProductHost(store,bridge._gateway,{"latch":LatchAdapter(fixture)},lambda *args: True,lambda: NOW)
    status,response=dispatch(bridge,credential,subjects,body("haven.product.read","unbound-product",{"id":"latch","version":VERSION,"section":"commitments"}),product_host=host)
    assert status == 403 and response["code"] == "product_access_denied"


def test_product_uncertain_on_post_dispatch_failure(composition):
    from haven.android_bridge.product_host import LatchAdapter, ProductHost, VERSION
    _, store, bridge, subjects=composition
    class BrokenLatch(LatchFixture):
        def propose_cancellation(self, identifier):
            self.proposals += 1
            raise ValueError("response lost after proposal")
    fixture=BrokenLatch(); host=ProductHost(store,bridge._gateway,{"latch":LatchAdapter(fixture)},lambda *args:True,lambda:NOW)
    request=body("haven.product.propose","uncertain-product",{"id":"latch","version":VERSION,"section":"propose_cancellation","item_id":"com_1"})
    assert dispatch(bridge,TOKEN,subjects,request,product_host=host)[1]["code"] == "outcome_uncertain"
    assert dispatch(bridge,TOKEN,subjects,request,product_host=host)[1]["code"] == "outcome_uncertain"
    assert fixture.proposals == 1


def test_optional_session_provider_authorization_replay_idempotency_and_restart(composition, tmp_path):
    import sys
    from pathlib import Path
    from haven.session_bridge.gateway import SessionGateway
    from haven.session_bridge.contract import CONTRACT, READ, SEND
    sys.path.insert(0,str(Path(__file__).parents[1]/'native'/'Haven.Android'/'session_contract'))
    from fixture import SessionFixture
    _, store, bridge, subjects = composition
    provider=SessionFixture(tmp_path/'session-fixture.db'); provider.seed_session('shared',HOUSEHOLD_ID,'ada');provider.seed_session('hidden',HOUSEHOLD_ID,'ada')
    seen=[]
    def grant(admitted, scope, session):
        seen.append((scope,session))
        return session in (None,'shared')
    host=SessionGateway(store=store,gateway=bridge._gateway,authorize=grant,provider=provider)
    def call(op, args, request_id='r1', token=TOKEN, session_host=host):
        return dispatch(bridge,token,subjects,body('haven.session.'+op,request_id,{'version':CONTRACT,**args}),session_host=session_host)
    assert call('capabilities',{},session_host=None)[0] == 503
    caps=call('capabilities',{})[1];assert caps['contract']==CONTRACT and 'memory' not in caps['operations']
    assert call('sessions',{},'r2')[1]['sessions']==[{'session_id':'shared'}]
    assert call('snapshot',{'session_id':'hidden'},'r3')[0]==403
    first=call('submit',{'session_id':'shared','text':'Synthetic'},'command-1')
    assert first[0]==200 and first[1]['state']=='queued'
    assert call('submit',{'session_id':'shared','text':'Synthetic'},'command-1') == first
    assert call('submit',{'session_id':'shared','text':'Different'},'command-1')[0]==409
    replay=call('replay',{'session_id':'shared','after':0,'limit':100},'read-1')
    assert replay[1]['events'][0]['payload']['text']=='Synthetic'
    assert call('memory',{'session_id':'shared'},'m1')[0]==503
    provider=SessionFixture(tmp_path/'session-fixture.db');provider.recover_after_host_restart();host.provider=provider
    assert call('command_status',{'session_id':'shared','command_id':'command-1'},'status')[1]['state']=='recorded'
    assert call('snapshot',{'session_id':'shared'},'r4',token='wrong')[0]==401
    assert (READ,'shared') in seen and (SEND,'shared') in seen


def test_session_gateway_refuses_untrusted_guarantees_and_malformed_reply(composition, tmp_path):
    import sys
    from pathlib import Path
    from haven.session_bridge.gateway import SessionGateway
    from haven.session_bridge.contract import CONTRACT
    sys.path.insert(0,str(Path(__file__).parents[1]/'native'/'Haven.Android'/'session_contract'))
    from fixture import SessionFixture
    _, store, bridge, subjects=composition
    provider=SessionFixture(tmp_path/'fixture.db');provider.seed_session('shared',HOUSEHOLD_ID,'ada')
    host=SessionGateway(store=store,gateway=bridge._gateway,authorize=lambda *args: True,provider=provider)
    req=body('haven.session.replay','r1',{'version':CONTRACT,'session_id':'shared','after':0,'limit':100})
    provider.capabilities=lambda:{'contract':CONTRACT,'operations':['replay'],'guarantees':[]}
    assert dispatch(bridge,TOKEN,subjects,req,session_host=host)[0]==503
    provider.capabilities=lambda:{'contract':'different','operations':['replay'],'guarantees':['durable_replay']}
    assert dispatch(bridge,TOKEN,subjects,req,session_host=host)[0]==503
    provider.capabilities=lambda:{'contract':CONTRACT,'operations':['replay'],'guarantees':['durable_replay']}
    provider.replay=lambda *args:{'contract':CONTRACT,'session_id':'wrong','events':[],'next_cursor':0}
    assert dispatch(bridge,TOKEN,subjects,req,session_host=host)[0]==400


def test_session_json_schemas_match_optional_provider_envelopes(composition, tmp_path):
    import sys
    from pathlib import Path
    import jsonschema
    from haven.session_bridge.gateway import SessionGateway
    from haven.session_bridge.contract import CONTRACT
    directory=Path(__file__).parents[1]/'native'/'Haven.Android'/'session_contract'
    sys.path.insert(0,str(directory)); from fixture import SessionFixture
    request_schema=json.loads((directory/'request.schema.json').read_text(encoding='utf-8'))
    response_schema=json.loads((directory/'response.schema.json').read_text(encoding='utf-8'))
    _,store,bridge,subjects=composition
    provider=SessionFixture(tmp_path/'schema-fixture.db');provider.seed_session('shared',HOUSEHOLD_ID,'ada')
    host=SessionGateway(store=store,gateway=bridge._gateway,authorize=lambda *args:True,provider=provider)
    requests=[body('haven.session.capabilities','a',{'version':CONTRACT}),body('haven.session.sessions','b',{'version':CONTRACT}),
      body('haven.session.submit','c',{'version':CONTRACT,'session_id':'shared','text':'hello'}),
      body('haven.session.snapshot','d',{'version':CONTRACT,'session_id':'shared'}),
      body('haven.session.replay','e',{'version':CONTRACT,'session_id':'shared','after':0,'limit':100}),
      body('haven.session.command_status','f',{'version':CONTRACT,'session_id':'shared','command_id':'c'})]
    for req in requests:
        jsonschema.validate(req,request_schema)
        status,response=dispatch(bridge,TOKEN,subjects,req,session_host=host)
        assert status==200
        jsonschema.validate(response,response_schema)
    bad=body('haven.session.snapshot','z',{'version':CONTRACT,'session_id':'shared','principal_id':'owner'})
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(bad,request_schema)
    assert dispatch(bridge,TOKEN,subjects,bad,session_host=host)[0]==400


def test_session_ambiguous_provider_failure_is_uncertain_and_retry_idempotent(composition,tmp_path):
    import sys
    from pathlib import Path
    from haven.session_bridge.gateway import SessionGateway
    from haven.session_bridge.contract import CONTRACT
    sys.path.insert(0,str(Path(__file__).parents[1]/'native'/'Haven.Android'/'session_contract'))
    from fixture import SessionFixture
    _,store,bridge,subjects=composition
    class FailingOnce(SessionFixture):
        def __init__(self,path): super().__init__(path);self.first=True
        def submit(self,*args):
            result=super().submit(*args)
            if self.first:
                self.first=False
                raise OSError('response lost after durable submission')
            return result
    provider=FailingOnce(tmp_path/'uncertain.db');provider.seed_session('shared',HOUSEHOLD_ID,'ada')
    host=SessionGateway(store=store,gateway=bridge._gateway,authorize=lambda *args:True,provider=provider)
    req=body('haven.session.submit','same-id',{'version':CONTRACT,'session_id':'shared','text':'once'})
    assert dispatch(bridge,TOKEN,subjects,req,session_host=host)[1]['code']=='outcome_uncertain'
    status,result=dispatch(bridge,TOKEN,subjects,req,session_host=host)
    assert status==200 and result['state']=='queued'
    from haven.session_bridge.contract import AuthContext, READ
    assert len(provider.snapshot(AuthContext(HOUSEHOLD_ID,'ada',frozenset({READ})),'shared')['jobs'])==1
