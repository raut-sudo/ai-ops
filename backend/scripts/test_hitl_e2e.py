"""End-to-end HITL flow test: chat → hitl_pending → approve → executed.

Run: python scripts/test_hitl_e2e.py
Requires: docker compose up (backend + postgres + qdrant)
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx

BASE = "http://localhost:8000/api/v1"
HEADERS = {"X-User-Id": "test-operator-1", "Content-Type": "application/json"}


async def run() -> None:
    thread_id = str(uuid.uuid4())
    print(f"=== HITL E2E Test  thread_id={thread_id} ===\n")

    # ── Step 1: POST /chat ───────────────────────────────────────────────────
    chat_body = {
        "query": "SKU-101 is critically out of stock and causing revenue loss. Restock it now.",
        "thread_id": thread_id,
    }

    hitl_pending = None

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", f"{BASE}/chat", json=chat_body, headers=HEADERS) as resp:
            print(f"[chat] HTTP {resp.status_code}")
            async for raw in resp.aiter_lines():
                line = raw.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    print(f"  [raw] {line}")
                    continue

                event_type = event.get("type", "unknown")
                if event_type == "node_start":
                    print(f"  node_start -> {event.get('node')}")
                elif event_type == "domain_finding":
                    domain = event.get("domain")
                    finding = event.get("finding", {})
                    conf = finding.get("confidence", "?")
                    print(f"  domain_finding -> {domain} (confidence={conf})")
                elif event_type == "synthesis":
                    synth = event.get("synthesis", {})
                    print(
                        f"  synthesis -> confidence={synth.get('confidence_score')} causes={len(synth.get('root_causes', []))}"
                    )
                elif event_type == "hitl_pending":
                    hitl_pending = event
                    actions = event.get("proposed_actions", [])
                    print(f"  hitl_pending -> {len(actions)} actions")
                    for a in actions:
                        print(
                            f"    - action_id={a.get('action_id')} type={a.get('action_type')} target={a.get('target')}"
                        )
                elif event_type == "final":
                    fr = event.get("final_response", {})
                    print(
                        f"  final -> status={fr.get('status')} confidence={fr.get('confidence_score'):.2f}"
                    )
                elif event_type == "error":
                    print(f"  ERROR -> {event.get('message')}")
                else:
                    print(f"  {event_type} -> {line[:120]}")

    # ── Step 2: Verify DB proposed state ────────────────────────────────────
    print()
    if hitl_pending is None:
        print("[RESULT] No HITL triggered — graph completed directly. No approve needed.")
        print(
            "         (This may be expected if LLM confidence was low or no actionable root causes found.)"
        )
        return

    actions = hitl_pending.get("proposed_actions", [])
    if not actions:
        print("[WARN] hitl_pending had 0 proposed_actions. Cannot approve.")
        return

    action_id = actions[0]["action_id"]
    print(f"=== Approving action_id={action_id} ===")

    approve_body = {
        "thread_id": thread_id,
        "decision": {
            "approved_action_ids": [action_id],
            "rejected_action_ids": [a["action_id"] for a in actions[1:]],
            "approver": "test-operator-1",
        },
    }

    # ── Step 3: POST /approve ────────────────────────────────────────────────
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(f"{BASE}/approve", json=approve_body, headers=HEADERS)
        print(f"[approve] HTTP {resp.status_code}")
        if resp.status_code == 200:
            fr = resp.json()
            print(f"  status        = {fr.get('status')}")
            print(f"  confidence    = {fr.get('confidence_score')}")
            print(f"  summary       = {fr.get('summary', '')[:120]}")
            exe = fr.get("executed_actions", [])
            print(f"  executed_actions ({len(exe)}):")
            for ar in exe:
                print(
                    f"    action_id={ar.get('action_id')} status={ar.get('status')} payload={ar.get('result_payload')}"
                )
            print("\n[RESULT] HITL flow completed successfully.")
        else:
            print(f"  ERROR {resp.status_code}: {resp.text}")
            print("\n[RESULT] HITL approve failed.")


if __name__ == "__main__":
    asyncio.run(run())
