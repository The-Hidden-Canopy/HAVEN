"""Explicit synthetic demo. Never connects a live household or uses live credentials."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import secrets
import ssl
import tempfile

from haven.android_bridge import BridgeServer, phone_services, pending_belongs_to_connection
from haven.core.domain import Principal, RoleTier
from haven.external_agents.domain import ExternalAgentConnection, ExternalProvider, ExternalScope, PrincipalBinding
from haven.external_agents.gateway import ExternalAgentGateway
from haven.external_agents.reads import ExternalReadTools
from haven.external_agents.store import ExternalAgentStore
from haven.external_agents.transport import TransportBridge, hash_credential
from haven.web.demo import DemoDirector, HOUSEHOLD_ID


def main():
    parser = argparse.ArgumentParser(description="Synthetic HAVEN Android HTTPS bridge; no live data")
    parser.add_argument("--cert", required=True, help="PEM certificate with a hostname/IP trusted by the Android client")
    parser.add_argument("--key", required=True, help="PEM private key for that certificate")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8443)
    args = parser.parse_args()
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(args.cert, args.key)
    with tempfile.TemporaryDirectory(prefix="haven-android-demo-") as folder:
        now = datetime.now(timezone.utc)
        token = secrets.token_urlsafe(32)
        fingerprint = hash_credential(token)
        director = DemoDirector()
        store = ExternalAgentStore(Path(folder) / "external.db")
        store.save_connection(ExternalAgentConnection(connection_id="android-demo", household_id=HOUSEHOLD_ID,
            provider=ExternalProvider.MCP_CLIENT, display_name="Android demo", enabled=True,
            credential_hash=fingerprint, created_at=now, created_by="demo-owner"))
        store.save_binding(PrincipalBinding(binding_id="demo-binding", connection_id="android-demo", subject_key="demo-subject",
            subject_label="Demo resident", principal_id="android-demo-resident",
            scopes=frozenset({ExternalScope.WORLD_READ, ExternalScope.ACTIONS_REQUEST}), created_by="demo-owner", created_at=now))
        resident = Principal(actor_id="android-demo-resident", household_id=HOUSEHOLD_ID, role_tier=RoleTier.MEMBER)
        gateway = ExternalAgentGateway(store=store, household_id=HOUSEHOLD_ID,
            resolve_principal={resident.actor_id: resident}.get)
        bridge = TransportBridge(store=store, gateway=gateway, reads=ExternalReadTools(gateway=gateway, reader=director), executor=director)
        server = BridgeServer((args.host, args.port), bridge, {fingerprint: "demo-subject"}, tls,
                              services=lambda device_id: phone_services(director.registry, device_id),
                              pending_checker=lambda credential, request_id: pending_belongs_to_connection(store, director, credential, request_id))
        print("SYNTHETIC DEMO ONLY; no real devices. Token is ephemeral and expires when this process exits.", flush=True)
        print("Bridge port:", server.server_port, "token:", token, flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()

if __name__ == "__main__":
    main()
