"""Live smoke: scheduled firing through authority + trust chain drill-down.

Boots the real web server with an advanceable clock, ticks the scheduler
across the office-light rule's 22:35 daily boundary, then approves a guarded
direct action and reads its trust chain back over HTTP.
"""
import json
import threading
import urllib.request
from datetime import datetime, timedelta, timezone

from haven.web.server import make_server

_state = {"now": datetime(2026, 9, 18, 22, 34, 40, tzinfo=timezone.utc)}


def clock():
    return _state["now"]


def get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:8125{path}") as r:
        return json.load(r)


def post(path, body=None):
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(
        f"http://127.0.0.1:8125{path}", data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


server, director = make_server(8125, clock=clock)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()

try:
    # -- 1. before the boundary: enabled, not due, tick is quiet -------------
    rules = {r["rule_id"]: r for r in get("/api/scheduler")["scheduler"]}
    office = next(r for r in rules.values() if "office light" in r["summary"])
    print("before: enabled =", office["enabled"], "| due_now =", office["due_now"],
          "| next_run =", office["next_run_at"])
    post("/api/scheduler/tick")
    office = {r["rule_id"]: r for r in get("/api/scheduler")["scheduler"]}[office["rule_id"]]
    assert office["last_fired_at"] is None, "fired before the schedule boundary!"
    print("tick before 22:35: quiet (last_fired_at still null) -- due gate holds")

    # -- 2. cross the boundary: tick fires the rule through authority --------
    _state["now"] = _state["now"] + timedelta(minutes=1)
    post("/api/scheduler/tick")
    office = {r["rule_id"]: r for r in get("/api/scheduler")["scheduler"]}[office["rule_id"]]
    print("after:  last_fired_at =", office["last_fired_at"],
          "| last_outcome =", office["last_outcome"])
    assert office["last_fired_at"] is not None, "scheduled firing did not happen"

    state = get("/api/state")
    activity = state.get("activity", [])
    fired = [e for e in activity if e.get("event_type") == "action_executed"]
    assert fired, "no action_executed event after scheduled firing"
    print("scheduled firing produced action_executed event(s):",
          [e["summary"] for e in fired])

    # -- 3. trust chain drill-down on a fresh guarded direct action ----------
    post("/api/chat", {"text": "close the garage"})
    state = get("/api/state")
    pending = state.get("pending") or []
    assert pending, "no permission request for guarded garage close"
    rid = pending[0]["request_id"]
    post(f"/api/requests/{rid}/approve", {})
    state = get("/api/state")
    executed = [e for e in state.get("activity", []) if e.get("event_type") == "action_executed"]
    assert executed, "no executed event after approval"
    event_id = executed[-1]["event_id"]
    chain = get(f"/api/actions/chain?event_id={event_id}")
    assert chain.get("ok"), f"chain lookup failed: {chain}"
    c = chain["chain"]
    print("trust chain sections:")
    for section in ("request", "interpretation", "evidence", "authority",
                    "execution", "consequence", "timeline"):
        present = section in c and c[section] not in (None, [], {})
        print(f"  {section:<14} {'present' if present else 'empty'}")
    print("  authority.status =", c["authority"].get("status"),
          "| risk_tier =", c["authority"].get("risk_tier"))
    print("  execution.service =", c["execution"].get("service"),
          "| success =", c["execution"].get("success"))
    print("  consequence.delay_ms =", c["consequence"].get("delay_ms"))
    print("SMOKE OK")
finally:
    server.shutdown()
    server.server_close()
