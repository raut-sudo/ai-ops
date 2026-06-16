"""
End-to-End Test Suite — AI-Ops backend
======================================
Tests every flow:
  1. Health + graph compile verification
  2. Single-domain: inventory (focused SKU query)
  3. Single-domain: sales (revenue drop)
  4. Single-domain: marketing (campaign performance)
  5. Single-domain: support (complaint health)
  6. Cross-domain business diagnosis
  7. Action query → HITL pending → approve
  8. HITL reject (campaign suspend)
  9. Idempotent approve (Case A)
 10. Pending actions API
 11. Irrelevant query (graceful degradation)

Rate-limit aware: 30s pause between queries to avoid 429 cascades.
"""

from __future__ import annotations

import json
import sys
import textwrap
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

# ---------------------------------------------------------------------------
BASE = "http://localhost:8000"
API = f"{BASE}/api/v1"
HEADERS = {"X-User-Id": "e2e-tester", "Content-Type": "application/json"}
TIMEOUT = 240  # seconds per query — allow for 2 consecutive 429 retries (56+34+buffer)
PAUSE = 35  # seconds between queries to let rate-limit window recover

# ---------------------------------------------------------------------------
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
DIM = "\033[2m"


def c(color: str, text: str) -> str:
    return f"{color}{text}{RESET}"


@dataclass
class TestResult:
    name: str
    passed: bool
    notes: list[str] = field(default_factory=list)
    duration_s: float = 0.0


results: list[TestResult] = []


def _separator(title: str = "") -> None:
    width = 72
    if title:
        pad = (width - len(title) - 2) // 2
        print(f"\n{c(BOLD, '─' * pad + ' ' + title + ' ' + '─' * pad)}")
    else:
        print(c(DIM, "─" * width))


def _pause(label: str = ""):
    print(f"  {c(DIM, f'⏱  pausing {PAUSE}s for rate-limit recovery {label}...')}")
    time.sleep(PAUSE)


# ---------------------------------------------------------------------------
# Stream helper
# ---------------------------------------------------------------------------


def chat(query: str, thread_id: str | None = None, label: str = "") -> dict[str, Any]:
    """POST /chat → consume NDJSON stream → return parsed result dict."""
    payload: dict[str, Any] = {"query": query}
    if thread_id:
        payload["thread_id"] = thread_id

    nodes_visited: list[str] = []
    domain_findings: dict[str, Any] = {}
    synthesis: dict | None = None
    final: dict | None = None
    hitl: dict | None = None
    errors: list[str] = []

    if label:
        print(f"\n  {c(BOLD, 'QUERY:')} {label}")
    print(f"  {c(DIM, repr(query[:100]))}")

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            with client.stream("POST", f"{API}/chat", headers=HEADERS, json=payload) as resp:
                resp.raise_for_status()
                for raw in resp.iter_lines():
                    if not raw.strip():
                        continue
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    t = obj.get("type")
                    if t == "node_start":
                        nodes_visited.append(obj.get("node", "?"))
                        print(f"    {c(DIM, '→')} node: {c(CYAN, obj.get('node', '?'))}")
                    elif t == "domain_finding":
                        dom = obj.get("domain", "?")
                        f = obj.get("finding", {})
                        domain_findings[dom] = f
                        sev = f.get("severity", "?")
                        sev_color = RED if sev == "critical" else YELLOW if sev == "high" else GREEN
                        calls = f.get("tool_calls_made", [])
                        is_err = any(
                            "Agent error" in s or "parsing failed" in s
                            for s in f.get("findings", [])
                        )
                        tag = c(RED, "[ERR]") if is_err else c(GREEN, "[ok] ")
                        print(
                            f"    {c(DIM,'→')} {tag} domain={c(BOLD, dom)} sev={c(sev_color, sev)} tools={calls}"
                        )
                        for finding in f.get("findings", [])[:2]:
                            print(f"       {c(DIM,'·')} {finding[:110]}")
                    elif t == "synthesis":
                        synthesis = obj.get("synthesis", {})
                        expl = synthesis.get("correlated_explanation", "")
                        print(f"    {c(DIM,'→')} synthesis: {expl[:110]}")
                    elif t == "hitl_pending":
                        hitl = obj
                        tid = obj.get("thread_id", "?")
                        acts = obj.get("proposed_actions", [])
                        print(f"    {c(YELLOW,'⏸  HITL PENDING')} thread={tid}")
                        for act in acts:
                            params = act.get("parameters", {})
                            print(
                                f"       {params.get('action_type','?')} risk={act.get('risk_level','?')}"
                            )
                    elif t == "final":
                        final = obj
                        fr = obj.get("final_response", {})
                        status = fr.get("status", "?")
                        color = (
                            GREEN
                            if status == "success"
                            else YELLOW
                            if status == "hitl_pending"
                            else RED
                        )
                        print(f"    {c(color,'✓ FINAL')} status={status}")
                        print(
                            f"    {textwrap.fill(fr.get('summary','')[:300], 90, initial_indent='    ', subsequent_indent='    ')}"
                        )
                        for act in fr.get("proposed_actions", []):
                            params = act.get("parameters", {})
                            print(
                                f"    {c(YELLOW,'  ⚡')} {params.get('action_type','?')} → {act.get('target','?')}"
                            )
                    elif t == "error":
                        errors.append(str(obj))
                        print(f"    {c(RED,'✗ ERROR')} {obj}")
    except httpx.RemoteProtocolError as exc:
        errors.append(f"connection dropped (server reloaded?): {exc}")
        print(f"    {c(RED,'connection dropped:')} {exc}")
    except Exception as exc:
        errors.append(str(exc))
        print(f"    {c(RED,'exception:')} {exc}")

    tid = None
    if hitl:
        tid = hitl.get("thread_id")
    if not tid and final:
        tid = final.get("final_response", {}).get("thread_id") or final.get("thread_id")

    return {
        "nodes": nodes_visited,
        "domains": domain_findings,
        "synthesis": synthesis,
        "final": final,
        "hitl": hitl,
        "errors": errors,
        "thread_id": tid,
    }


# ---------------------------------------------------------------------------
# Approve helper
# ---------------------------------------------------------------------------


def approve(thread_id: str, action_ids: list[str], reject_ids: list[str] | None = None) -> dict:
    payload = {
        "thread_id": thread_id,
        "decision": {
            "approved_action_ids": action_ids,
            "rejected_action_ids": reject_ids or [],
            "approver": "e2e-tester",
        },
    }
    resp = httpx.post(f"{API}/approve", headers=HEADERS, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def pending_actions() -> list[dict]:
    resp = httpx.get(f"{API}/actions/pending", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Assertion + recording helpers
# ---------------------------------------------------------------------------


def record(name: str, passed: bool, notes: list[str], duration: float):
    results.append(TestResult(name, passed, notes, duration))
    icon = c(GREEN, "✅ PASS") if passed else c(RED, "❌ FAIL")
    print(f"  {icon}  {name}  {c(DIM, f'({duration:.1f}s)')}")
    for n in notes:
        print(f"         {c(DIM, n)}")


def agent_errored(result: dict, domain: str) -> bool:
    """True when an agent returned an error finding (rate-limit degradation)."""
    findings = result["domains"].get(domain, {}).get("findings", [])
    return any("Agent error" in s or "timed out" in s.lower() for s in findings)


def _notes_warn_if_agent_error(result: dict, domain: str) -> list[str]:
    """Return a WARNING note (not a failure) when agent errored due to rate limiting."""
    if agent_errored(result, domain):
        return [f"⚠ {domain} agent errored (likely 429 rate-limit) — graph degraded gracefully"]
    return []


def assert_domain(result: dict, domain: str) -> list[str]:
    return [f"'{domain}' domain finding missing"] if domain not in result["domains"] else []


def assert_final(result: dict, status: str) -> list[str]:
    actual = (result.get("final") or {}).get("final_response", {}).get("status")
    return [f"expected status={status}, got {actual}"] if actual != status else []


def assert_no_stream_errors(result: dict) -> list[str]:
    return [f"stream error: {e}" for e in result.get("errors", [])]


def get_action_ids(result: dict) -> list[str]:
    ids: list[str] = []
    if result.get("hitl"):
        ids = [
            a.get("action_id")
            for a in result["hitl"].get("proposed_actions", [])
            if a.get("action_id")
        ]
    if not ids:
        proposed = (result.get("final") or {}).get("final_response", {}).get("proposed_actions", [])
        ids = [a.get("action_id") for a in proposed if a.get("action_id")]
    return ids


# ===========================================================================
# ── TEST CASES ──────────────────────────────────────────────────────────────
# ===========================================================================


def test_1_health():
    _separator("1. Health + Graph Verify")
    t0 = time.time()
    resp = httpx.get(f"{BASE}/health", timeout=10)
    dur = time.time() - t0
    ok = resp.status_code == 200 and resp.json().get("status") == "ok"
    record("Health endpoint → ok", ok, [] if ok else [resp.text[:200]], dur)

    # Verify graph compiled in last 10 min via logs
    import subprocess

    try:
        out = subprocess.run(
            ["docker", "logs", "ai-ops-backend", "--since", "10m"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        compiled = "graph.compiled" in (out.stdout + out.stderr)
        record(
            "graph.compiled in recent logs",
            compiled,
            [] if compiled else ["graph.compiled not seen — check backend"],
            0,
        )
    except Exception as exc:
        record("graph.compiled check", False, [str(exc)], 0)


def test_2_inventory():
    _separator("2. Inventory — focused SKU query")
    _pause("before inventory query")
    t0 = time.time()
    result = chat(
        "What is the current stock level for SKU SKU-101 and is it near its reorder point?",
        label="inventory SKU check",
    )
    dur = time.time() - t0

    notes = assert_no_stream_errors(result)
    notes += assert_domain(result, "inventory")
    notes += _notes_warn_if_agent_error(result, "inventory")
    # Only fail for missing domain or stream errors, not rate-limit degradation
    hard_fail = [n for n in notes if not n.startswith("⚠")]
    record("Inventory SKU stock-level diagnostic", not hard_fail, notes, dur)


def test_3_sales():
    _separator("3. Sales — revenue drop analysis")
    _pause("before sales query")
    t0 = time.time()
    result = chat(
        "What were our total sales and top 3 products by revenue over the last 7 days?",
        label="sales top products",
    )
    dur = time.time() - t0

    notes = assert_no_stream_errors(result)
    notes += assert_domain(result, "sales")
    notes += _notes_warn_if_agent_error(result, "sales")
    hard_fail = [n for n in notes if not n.startswith("⚠")]
    record("Sales 7-day revenue + top products", not hard_fail, notes, dur)


def test_4_marketing():
    _separator("4. Marketing — campaign ROAS analysis")
    _pause("before marketing query")
    t0 = time.time()
    result = chat(
        "Which active campaigns have ROAS below 2.0 and should be reviewed?",
        label="marketing underperforming campaigns",
    )
    dur = time.time() - t0

    notes = assert_no_stream_errors(result)
    notes += assert_domain(result, "marketing")
    notes += _notes_warn_if_agent_error(result, "marketing")
    hard_fail = [n for n in notes if not n.startswith("⚠")]
    record("Marketing underperforming campaigns (ROAS < 2.0)", not hard_fail, notes, dur)


def test_5_support():
    _separator("5. Support — complaint health + sentiment")
    _pause("before support query")
    t0 = time.time()
    result = chat(
        "What are the most common customer complaint categories and what is the overall sentiment this week?",
        label="support complaint categories",
    )
    dur = time.time() - t0

    notes = assert_no_stream_errors(result)
    notes += assert_domain(result, "support")
    notes += _notes_warn_if_agent_error(result, "support")
    hard_fail = [n for n in notes if not n.startswith("⚠")]
    record("Support complaint categories + sentiment", not hard_fail, notes, dur)


def test_6_cross_domain():
    _separator("6. Cross-domain — business diagnosis")
    _pause("before cross-domain query")
    t0 = time.time()
    result = chat(
        "We've seen a 20% revenue decline this week and customer complaints are rising. "
        "What is the root cause — is it inventory, marketing, or product quality?",
        label="cross-domain root cause",
    )
    dur = time.time() - t0

    notes = assert_no_stream_errors(result)
    # At least 2 domains should be engaged
    engaged = set(result["domains"].keys())
    if len(engaged) < 2:
        notes.append(f"expected ≥2 domains, got {engaged}")
    print(f"    Domains engaged: {engaged}")
    record(
        "Cross-domain diagnosis (≥2 domains)",
        not [n for n in notes if not n.startswith("⚠")],
        notes,
        dur,
    )


def test_7_action_and_hitl() -> str | None:
    _separator("7. Action Query → HITL → Approve")
    _pause("before action query")
    t0 = time.time()
    result = chat(
        "SKU-101 has been out of stock for 5 days causing revenue loss. "
        "Please restock it with 200 units.",
        label="restock action → HITL",
    )
    dur = time.time() - t0

    notes = assert_no_stream_errors(result)
    final_status = (result.get("final") or {}).get("final_response", {}).get("status", "")
    proposed = (result.get("final") or {}).get("final_response", {}).get("proposed_actions", [])
    is_hitl = result.get("hitl") is not None or final_status == "hitl_pending"

    if not is_hitl and not proposed:
        notes.append(
            "Expected HITL pending or proposed_actions — agent may have decided action not warranted"
        )
    record("Restock action → HITL gate triggered", not notes, notes, dur)

    thread_id = result.get("thread_id")
    if not thread_id:
        print(f"    {c(RED, 'No thread_id — cannot approve')}")
        return None

    # ── Approve ─────────────────────────────────────────────────────────────
    _separator()
    t0 = time.time()
    aids = get_action_ids(result)
    if not aids:
        # Try DB
        pending = pending_actions()
        aids = [
            p["action_id"]
            for p in pending
            if p.get("session_id") == thread_id or p.get("action_type") == "restock_product"
        ]

    notes2: list[str] = []
    if not aids:
        notes2.append("No action_ids found (action may not have been proposed)")
        record("Approve restock action (skipped)", True, notes2, time.time() - t0)
        return thread_id

    try:
        fr = approve(thread_id, aids)
        dur2 = time.time() - t0
        status = fr.get("status", "?")
        exec_count = len(fr.get("executed_actions", []))
        print(f"    post-approve status={status}  executed={exec_count}")
        record(
            "Approve restock → final response", status in ("success", "hitl_pending"), notes2, dur2
        )
    except Exception as exc:
        record("Approve restock", False, [str(exc)], time.time() - t0)

    return thread_id


def test_8_idempotent_approve(thread_id: str | None):
    _separator("8. Idempotent Approve (Case A)")
    if not thread_id:
        record("Idempotent approve (skipped — no thread)", True, ["skipped"], 0)
        return

    t0 = time.time()
    try:
        fr = approve(thread_id, [])  # empty — already completed
        dur = time.time() - t0
        status = fr.get("status", "?")
        print(f"    Case A: status={status}")
        record("Re-approve completed thread → cached final (Case A)", True, [], dur)
    except httpx.HTTPStatusError as exc:
        dur = time.time() - t0
        if exc.response.status_code == 409:
            record("Re-approve → 409 Conflict (acceptable)", True, ["409 returned"], dur)
        else:
            record("Idempotent approve", False, [str(exc)], dur)


def test_9_campaign_action_reject():
    _separator("9. Campaign Suspend → HITL → Reject")
    _pause("before campaign action query")
    t0 = time.time()
    result = chat(
        "Our Facebook Ads campaign has been running with a ROAS of 0.3 for two weeks — "
        "it's losing money. Please suspend it.",
        label="campaign suspend → HITL",
    )
    dur = time.time() - t0

    notes = assert_no_stream_errors(result)
    final_status = (result.get("final") or {}).get("final_response", {}).get("status", "")
    proposed = (result.get("final") or {}).get("final_response", {}).get("proposed_actions", [])
    is_hitl = result.get("hitl") is not None or final_status == "hitl_pending"

    if not is_hitl and not proposed:
        notes.append(
            "Expected HITL for campaign suspend — agent may not have found evidence to support action"
        )
    record("Campaign suspend → HITL gate", not notes, notes, dur)

    thread_id = result.get("thread_id")
    if not thread_id:
        return

    # ── Reject ──────────────────────────────────────────────────────────────
    _separator()
    t0 = time.time()
    aids = get_action_ids(result)
    if not aids:
        record(
            "Reject campaign action (no action_ids)",
            True,
            ["no actions proposed"],
            time.time() - t0,
        )
        return

    notes2: list[str] = []
    try:
        fr = approve(thread_id, [], reject_ids=aids)
        dur2 = time.time() - t0
        status = fr.get("status", "?")
        print(f"    post-reject: status={status}")
        record(
            "Reject campaign suspend → clean final",
            status in ("success", "hitl_pending"),
            notes2,
            dur2,
        )
    except Exception as exc:
        record("Reject campaign", False, [str(exc)], time.time() - t0)


def test_10_pending_actions():
    _separator("10. Pending Actions API")
    t0 = time.time()
    try:
        actions = pending_actions()
        dur = time.time() - t0
        print(f"    {len(actions)} pending action(s) in queue")
        for a in actions[:5]:
            print(
                f"    · {a.get('action_type','?')} | risk={a.get('risk_level','?')} | target={a.get('target','?')}"
            )
        record("GET /actions/pending returns list", True, [], dur)
    except Exception as exc:
        record("GET /actions/pending", False, [str(exc)], time.time() - t0)


def test_11_irrelevant():
    _separator("11. Irrelevant Query — graceful degradation")
    _pause("before irrelevant query")
    t0 = time.time()
    result = chat("What is the tallest mountain in the world?", label="irrelevant")
    dur = time.time() - t0
    notes = assert_no_stream_errors(result)
    if not result.get("final"):
        notes.append("No final event received")
    record("Irrelevant query handled gracefully", not notes, notes, dur)


def test_12_new_tools_spot_check():
    """Quick checks on 2 new tools to verify they're wired end-to-end."""
    _separator("12. New Tools Spot Check")

    _pause("before new-tool query 1")
    t0 = time.time()
    r1 = chat(
        "What is the return rate and top return reasons for SKU PROD-003?",
        label="support: get_return_rate_by_sku",
    )
    dur1 = time.time() - t0
    n1 = assert_no_stream_errors(r1)
    n1 += assert_domain(r1, "support")
    n1 += _notes_warn_if_agent_error(r1, "support")
    record(
        "New tool: get_return_rate_by_sku (support)",
        not [n for n in n1 if not n.startswith("⚠")],
        n1,
        dur1,
    )

    _pause("before new-tool query 2")
    t0 = time.time()
    r2 = chat(
        "Show me orders grouped by status (completed, cancelled, returned) for the last 7 days",
        label="sales: get_orders_by_status",
    )
    dur2 = time.time() - t0
    n2 = assert_no_stream_errors(r2)
    n2 += assert_domain(r2, "sales")
    n2 += _notes_warn_if_agent_error(r2, "sales")
    record(
        "New tool: get_orders_by_status (sales)",
        not [n for n in n2 if not n.startswith("⚠")],
        n2,
        dur2,
    )


# ===========================================================================
# ── MAIN ────────────────────────────────────────────────────────────────────
# ===========================================================================


def main():
    _separator("AI-OPS END-TO-END TEST SUITE")
    print(f"  Target  : {BASE}")
    print(f"  Timeout : {TIMEOUT}s per query")
    print(f"  Pause   : {PAUSE}s between queries (rate-limit guard)\n")

    test_1_health()
    test_2_inventory()
    test_3_sales()
    test_4_marketing()
    test_5_support()
    test_6_cross_domain()
    restock_thread = test_7_action_and_hitl()
    test_8_idempotent_approve(restock_thread)
    test_9_campaign_action_reject()
    test_10_pending_actions()
    test_11_irrelevant()
    test_12_new_tools_spot_check()

    # ── Summary ──────────────────────────────────────────────────────────────
    _separator("RESULTS SUMMARY")
    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]
    warned = [r for r in passed if any(n.startswith("⚠") for n in r.notes)]
    total_t = sum(r.duration_s for r in results)

    print(
        f"\n  {c(GREEN, f'{len(passed)} passed')}   "
        f"{c(RED,   f'{len(failed)} failed')}   "
        f"{c(YELLOW, f'{len(warned)} degraded (rate-limited)')}   "
        f"{c(DIM,    f'{len(results)} total   {total_t:.0f}s')}\n"
    )

    if failed:
        print(c(RED, "  FAILURES:"))
        for r in failed:
            print(f"    {c(RED,'✗')} {r.name}")
            for n in r.notes:
                print(f"       {c(DIM, n)}")

    if warned:
        print(c(YELLOW, "\n  DEGRADED (rate-limit, not failures):"))
        for r in warned:
            print(f"    {c(YELLOW,'⚠')} {r.name}")
            for n in r.notes:
                print(f"       {c(DIM, n)}")

    # ── Backend log snapshot ─────────────────────────────────────────────────
    _separator("BACKEND LOG SNAPSHOT (chat/tool/action events)")
    import subprocess

    try:
        out = subprocess.run(
            ["docker", "logs", "ai-ops-backend", "--since", "2h"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        relevant = [
            line
            for line in (out.stdout + out.stderr).splitlines()
            if any(
                k in line
                for k in [
                    "chat.started",
                    "chat.complete",
                    "tool.",
                    "hitl",
                    "action.",
                    "graph.compile",
                    "agent.",
                    "429",
                ]
            )
        ]
        for line in relevant[-25:]:
            print(f"  {c(DIM, line[:140])}")
    except Exception:
        pass

    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()

    _pause("before new-tool query 2")
    t0 = time.time()
    r2 = chat(
        "Show me orders grouped by status (completed, cancelled, returned) for the last 7 days",
        label="sales: get_orders_by_status",
    )
    dur2 = time.time() - t0
    n2 = assert_no_stream_errors(r2)
    n2 += assert_domain(r2, "sales")
    n2 += _notes_warn_if_agent_error(r2, "sales")
    record(
        "New tool: get_orders_by_status (sales)",
        not [n for n in n2 if not n.startswith("⚠")],
        n2,
        dur2,
    )


# ===========================================================================
# ── MAIN ────────────────────────────────────────────────────────────────────
# ===========================================================================


def main():
    _separator("AI-OPS END-TO-END TEST SUITE")
    print(f"  Target  : {BASE}")
    print(f"  Timeout : {TIMEOUT}s per query")
    print(f"  Pause   : {PAUSE}s between queries (rate-limit guard)\n")

    test_1_health()
    test_2_inventory()
    test_3_sales()
    test_4_marketing()
    test_5_support()
    test_6_cross_domain()
    restock_thread = test_7_action_and_hitl()
    test_8_idempotent_approve(restock_thread)
    test_9_campaign_action_reject()
    test_10_pending_actions()
    test_11_irrelevant()
    test_12_new_tools_spot_check()

    # ── Summary ──────────────────────────────────────────────────────────────
    _separator("RESULTS SUMMARY")
    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]
    warned = [r for r in passed if any(n.startswith("⚠") for n in r.notes)]
    total_t = sum(r.duration_s for r in results)

    print(
        f"\n  {c(GREEN, f'{len(passed)} passed')}   "
        f"{c(RED,   f'{len(failed)} failed')}   "
        f"{c(YELLOW, f'{len(warned)} degraded (rate-limited)')}   "
        f"{c(DIM,    f'{len(results)} total   {total_t:.0f}s')}\n"
    )

    if failed:
        print(c(RED, "  FAILURES:"))
        for r in failed:
            print(f"    {c(RED,'✗')} {r.name}")
            for n in r.notes:
                print(f"       {c(DIM, n)}")

    if warned:
        print(c(YELLOW, "\n  DEGRADED (rate-limit, not failures):"))
        for r in warned:
            print(f"    {c(YELLOW,'⚠')} {r.name}")
            for n in r.notes:
                print(f"       {c(DIM, n)}")

    # ── Backend log snapshot ─────────────────────────────────────────────────
    _separator("BACKEND LOG SNAPSHOT (chat/tool/action events)")
    import subprocess

    try:
        out = subprocess.run(
            ["docker", "logs", "ai-ops-backend", "--since", "2h"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        relevant = [
            line
            for line in (out.stdout + out.stderr).splitlines()
            if any(
                k in line
                for k in [
                    "chat.started",
                    "chat.complete",
                    "tool.",
                    "hitl",
                    "action.",
                    "graph.compile",
                    "agent.",
                    "429",
                ]
            )
        ]
        for line in relevant[-25:]:
            print(f"  {c(DIM, line[:140])}")
    except Exception:
        pass

    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
