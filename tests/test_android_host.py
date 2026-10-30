"""Bounded synthetic TLS host lifecycle; no device or production host."""
from datetime import datetime, timedelta, timezone
import http.client
import ipaddress
import json
import socket
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from haven.android_bridge.host import ManagedBridgeHost
from haven.external_agents.transport import hash_credential

TOKEN = "synthetic-host-token"

@pytest.fixture
def tls(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
        .sign(key, hashes.SHA256()))
    cp, kp = tmp_path / "cert.pem", tmp_path / "key.pem"
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cp, kp)
    return context, cp


class Echo:
    def call_tool(self, **params): return {"ok": True, "state": {"rooms": []}}


def test_host_start_stop_restart_and_untrusted_tls_rejected(tls):
    context, cert = tls
    host = ManagedBridgeHost(("127.0.0.1", 0), Echo(), {hash_credential(TOKEN): "test"}, context, max_connections=2)
    _, port = host.start()
    assert host.running
    with pytest.raises(RuntimeError): host.start()
    try:
        bad = http.client.HTTPSConnection("127.0.0.1", port, timeout=3)
        with pytest.raises(ssl.SSLCertVerificationError): bad.connect()
        bad.close()
        good = http.client.HTTPSConnection("127.0.0.1", port, context=ssl.create_default_context(cafile=str(cert)), timeout=3)
        good.request("POST", "/v1/tool", json.dumps({"tool":"haven.world.get", "request_id":"one", "arguments":{}}), {"Authorization":"Bearer " + TOKEN, "Content-Type":"application/json"})
        response=good.getresponse(); assert response.status == 200 and json.loads(response.read())["ok"]
        good.close()
    finally:
        host.stop()
    assert not host.running
    host.stop()  # idempotent
    host.start(); assert host.running; host.stop()


def test_idle_tls_handshake_does_not_block_other_clients(tls):
    context, cert = tls
    host = ManagedBridgeHost(("127.0.0.1", 0), Echo(), {hash_credential(TOKEN): "test"}, context, max_connections=2)
    _, port = host.start()
    stalled = socket.create_connection(("127.0.0.1", port), timeout=3)
    try:
        good = http.client.HTTPSConnection("127.0.0.1", port, context=ssl.create_default_context(cafile=str(cert)), timeout=3)
        good.request("POST", "/v1/tool", json.dumps({"tool":"haven.world.get", "request_id":"two", "arguments":{}}), {"Authorization":"Bearer " + TOKEN, "Content-Type":"application/json"})
        response=good.getresponse(); assert response.status == 200; response.read(); good.close()
    finally:
        stalled.close(); host.stop()
