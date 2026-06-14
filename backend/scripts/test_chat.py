"""Quick smoke-test for the /api/v1/chat endpoint."""

import json

import httpx

BASE = "http://localhost:8000/api/v1/chat"
HEADERS = {"X-User-Id": "test-user-smoke", "Content-Type": "application/json"}

queries = [
    "what is the status of the inventory",
    "why did sales drop yesterday",
    "which is the highest selling product",
]

for q in queries:
    print(f"\n{'='*60}")
    print(f"QUERY: {q}")
    print("=" * 60)
    parse_errors = 0
    with httpx.Client(timeout=120) as client:
        with client.stream("POST", BASE, headers=HEADERS, json={"query": q}) as resp:
            for line in resp.iter_lines():
                if not line.strip():
                    continue
                obj = json.loads(line)
                t = obj.get("type")
                if t == "node_start":
                    print(f"  [node]    {obj['node']}")
                elif t == "domain_finding":
                    f = obj["finding"]
                    is_parse_err = any("parsing failed" in s for s in f.get("findings", []))
                    tag = "[PARSE_ERR]" if is_parse_err else "[finding]"
                    if is_parse_err:
                        parse_errors += 1
                    print(f"  {tag:12} domain={obj['domain']} → {f['findings']}")
                elif t == "synthesis":
                    s = obj["synthesis"]
                    print(f"  [synthesis] {str(s.get('correlated_explanation',''))[:120]}")
                elif t == "error":
                    print(f"  [ERROR]   {obj}")
                elif t == "final":
                    fr = obj.get("final_response", {})
                    print(f"  [FINAL]   status={fr.get('status')}")
                    print(f"            summary={str(fr.get('summary',''))[:250]}")
                    break
                elif t == "hitl_pending":
                    print(f"  [HITL]    awaiting approval thread_id={obj.get('thread_id')}")
                    break
    if parse_errors:
        print(f"  ⚠️  {parse_errors} parse error(s) detected")
    else:
        print("  ✅ No parse errors")
