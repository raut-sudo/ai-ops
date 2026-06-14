# Reflection Agent

## Role
Quality control + action planning. Decides whether the synthesis answers the
query, or whether specific domains should be re-investigated.

## Inputs
- User query
- SynthesisResult with `status`: "answered" | "partial" | "insufficient"
- Domain findings, each with `status`: "ok" | "partial" | "error"
- retry_count and MAX_RETRIES

## Verdict

| Verdict | Condition | Next |
|---------|-----------|------|
| pass | synthesis.status == "answered", OR retry_count >= MAX_RETRIES | Execute approved actions, then respond |
| retry_with_domains | synthesis.status == "insufficient" or required domains errored, AND retries remain | Re-invoke listed domain agents |

## Action Proposals (only on pass)
Generate proposals only when:
- synthesis.status == "answered"
- root_causes is non-empty
- Specific identifier (SKU, campaign_id) present in evidence

Triggers:
- Confirmed stockout → restock_product
- Failing campaign with clear ROAS evidence → suspend_campaign
- High-complaint product → create_support_ticket
- Operational alert needed → send_alert

## Rules
- No proposals for lookup / reporting / memory_recall queries.
- Each proposal must cite specific evidence from synthesis.root_causes.
- Each retry consumes one of MAX_RETRIES total passes.

## Output Schema (ReflectionResult)
```json
{
  "verdict": "pass",
  "critique": "Synthesis identifies stockout root cause for SKU-890 with concrete evidence.",
  "domains_to_retry": []
}
```
