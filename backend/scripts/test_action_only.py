"""Test action_only HITL flow: place a restock order directly."""

import json
import sys
import uuid

import httpx

BASE = "http://localhost:8000"
HEADERS = {"X-User-Id": "ops-engineer-1", "Content-Type": "application/json"}
THREAD = str(uuid.uuid4())

print(f"[THREAD] {THREAD}")
print("[STEP 1] action_only: restock SKU-101 with 300 units...")

resp = httpx.post(
    f"{BASE}/api/v1/chat",
    headers=HEADERS,
    json={"query": "Place an order to restock SKU-101 with 300 units", "thread_id": THREAD},
    timeout=90,
)

print(f"HTTP {resp.status_code}")
if resp.status_code != 200:
    print("ERROR:", resp.text)
    sys.exit(1)

events = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
print(f"Events received: {len(events)}")
for e in events:
    t = e.get("type")
    if t == "node_start":
        print(f"  · [node_start] {e.get('node')}")
    elif t == "hitl_pending":
        print(f"  · [hitl_pending] {len(e.get('proposed_actions', []))} action(s)")
    elif t == "final":
        print(f"  · [final] status={e.get('final_response', {}).get('status')}")
    elif t == "error":
        print(f"  · [ERROR] {e.get('message')}")
    else:
        print(f"  · [{t}]")

term = next((e for e in events if e.get("type") in {"hitl_pending", "final", "error"}), None)
if not term:
    print("\nFAIL: no terminal event!")
    sys.exit(1)
if term["type"] == "error":
    print(f"\nFAIL: {term.get('message')}")
    sys.exit(1)
if term["type"] == "final":
    fr = term.get("final_response", {})
    print(f"\n⚠  Graph completed WITHOUT HITL — status={fr.get('status')}")
    print(f"   summary: {fr.get('summary', '')[:200]}")
    sys.exit(0)

# ── hitl_pending ──
print("\nOK HITL triggered!")
props = term.get("proposed_actions", [])
print(json.dumps(props, indent=2))

# Approve first action
first_id = props[0]["action_id"]
rest_ids = [p["action_id"] for p in props[1:]]
print(f"\n[STEP 2] Approving action '{first_id}'...")

resp2 = httpx.post(
    f"{BASE}/api/v1/approve",
    headers=HEADERS,
    json={
        "thread_id": THREAD,
        "decision": {
            "approved_action_ids": [first_id],
            "rejected_action_ids": rest_ids,
            "approver": "ops-engineer-1",
        },
    },
    timeout=30,
)
print(f"Approve HTTP {resp2.status_code}")
if resp2.status_code != 200:
    print("ERROR:", resp2.text)
    sys.exit(1)

result = resp2.json()
print(f"status={result.get('status')}")
print(f"summary: {result.get('summary','')[:300]}")
print("\n[EXECUTED ACTIONS]")
for a in result.get("executed_actions", []):
    print(f"  · {a['action_id']} → {a['status']} | payload: {a.get('result_payload')}")

print("\nOK action_only HITL flow complete!")
