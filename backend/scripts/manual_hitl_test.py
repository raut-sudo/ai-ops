"""
Manual end-to-end HITL test.
Usage: python scripts/manual_hitl_test.py
"""

from __future__ import annotations

import json
import sys
import uuid
from typing import Any

import httpx

BASE = "http://localhost:8000"
HEADERS = {"X-User-Id": "ops-engineer-1", "Content-Type": "application/json"}
THREAD_ID = str(uuid.uuid4())


def pp(label: str, data: Any) -> None:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(json.dumps(data, indent=2, default=str))


# ── Step 1: Send diagnostic query ────────────────────────────────────────────
print(f"\n[THREAD] {THREAD_ID}")
print("\n[STEP 1] POST /api/v1/chat — diagnosing sales drop...")

with httpx.Client(timeout=120) as client:
    resp = client.post(
        f"{BASE}/api/v1/chat",
        headers=HEADERS,
        json={
            "query": "Why did sales drop yesterday? Diagnose root causes and propose remediation actions.",
            "thread_id": THREAD_ID,
        },
    )

print(f"HTTP {resp.status_code}")
if resp.status_code != 200:
    print("ERROR:", resp.text)
    sys.exit(1)

# Parse NDJSON stream
events: list[dict] = []
for line in resp.text.splitlines():
    line = line.strip()
    if line:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"  [skip non-JSON line]: {line[:80]}")

print(f"\n  Received {len(events)} SSE events:")
for ev in events:
    etype = ev.get("type", "?")
    if etype == "status":
        print(f"  · [status] {ev.get('message', '')}")
    elif etype == "hitl_pending":
        print(f"  · [hitl_pending] {len(ev.get('proposed_actions', []))} proposed action(s)")
    elif etype == "final":
        print(f"  · [final] status={ev.get('status')} response_length={len(ev.get('response',''))}")
    elif etype == "error":
        print(f"  · [ERROR] {ev.get('message', ev)}")
    else:
        print(f"  · [{etype}]")

# Find terminal event
terminal = next((e for e in events if e.get("type") in {"hitl_pending", "final", "error"}), None)
if terminal is None:
    print("\n[FAIL] No terminal event received!")
    sys.exit(1)

if terminal["type"] == "error":
    print(f"\n[FAIL] Graph returned error: {terminal.get('message')}")
    sys.exit(1)

if terminal["type"] == "final":
    print("\n[INFO] Graph completed without HITL (no actionable proposals).")
    pp("Final Response", terminal)
    sys.exit(0)

# ── HITL path ─────────────────────────────────────────────────────────────────
print("\n[STEP 2] Graph paused at HITL OK")
proposed = terminal.get("proposed_actions", [])
pp("Proposed Actions", proposed)

if not proposed:
    print("\n[FAIL] hitl_pending event but no proposed_actions!")
    sys.exit(1)

# Approve the first action, reject any remaining
first_id = proposed[0]["action_id"]
rest_ids = [p["action_id"] for p in proposed[1:]]
decision = {
    "approved_action_ids": [first_id],
    "rejected_action_ids": rest_ids,
    "approver": "ops-engineer-1",
}

print(f"\n[STEP 3] POST /api/v1/approve — approving action '{first_id}'...")
pp("HITL Decision", decision)

with httpx.Client(timeout=60) as client:
    resp2 = client.post(
        f"{BASE}/api/v1/approve",
        headers=HEADERS,
        json={"thread_id": THREAD_ID, "decision": decision},
    )

print(f"HTTP {resp2.status_code}")
if resp2.status_code != 200:
    print("ERROR:", resp2.text)
    sys.exit(1)

result = resp2.json()
pp("Approve Response", result)

# ── Evaluate quality ──────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  QUALITY EVALUATION")
print("=" * 60)

status = result.get("status")
summary = result.get("summary", "")
root_causes = result.get("root_causes", [])
executed = result.get("executed_actions", [])
recommendations = result.get("recommendations", [])

checks = [
    ("Status is 'success'", status == "success"),
    ("Summary / narrative present", len(summary) > 50),
    ("Root causes identified", len(root_causes) > 0),
    ("Recommendations present", len(recommendations) > 0),
    ("At least 1 executed action", len(executed) > 0),
    ("Executed action status=executed", any(a.get("status") == "executed" for a in executed)),
    ("Rejected actions marked skipped", any(a.get("status") == "skipped" for a in executed)),
]

all_ok = True
for label, ok in checks:
    icon = "OK" if ok else "FAIL"
    print(f"  {icon} {label}")
    if not ok:
        all_ok = False

print()
if summary:
    print("[SUMMARY / NARRATIVE]")
    print("-" * 60)
    print(summary[:2000])
    if len(summary) > 2000:
        print(f"  ... ({len(summary) - 2000} more chars)")
    print("-" * 60)

if root_causes:
    print("\n[ROOT CAUSES]")
    for i, rc in enumerate(root_causes, 1):
        print(f"  {i}. [{rc.get('domain','?')}] {rc.get('cause','?')}")

if executed:
    print("\n[EXECUTED ACTIONS]")
    for a in executed:
        payload = a.get("result_payload") or {}
        outcome = a.get("error") or str(payload)[:100]
        print(f"  · {a.get('action_id','?')} — status={a.get('status','?')}")
        if payload:
            print(f"    payload: {payload}")

print("\n" + ("OK ALL CHECKS PASSED" if all_ok else "FAIL SOME CHECKS FAILED"))
sys.exit(0 if all_ok else 1)
