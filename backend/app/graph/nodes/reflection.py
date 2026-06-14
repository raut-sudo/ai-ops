"""Reflection node.

LLM-driven reflection that:
 1. Evaluates synthesis quality and decides pass / retry_with_domains / fail.
 2. If passing with actionable root causes, generates action proposals via LLM.
 3. If proposals exist, pauses for HITL approval via interrupt().
 4. If approved, executes actions against tool dispatch.

All confidence checks and retry decisions are made by the LLM.
No hardcoded thresholds or keyword matching.
"""

from __future__ import annotations

import importlib
import json
import os
import uuid

import structlog
from langgraph.types import interrupt
from sqlalchemy import text

from app.config import settings
from app.db.session import get_session
from app.graph.state import AgentState
from app.schemas import (
    ActionProposal,
    ActionResult,
    CampaignParams,
    HITLDecision,
    ReflectionResult,
    RestockParams,
    TicketParams,
)
from app.tools.actions import ACTION_DISPATCH

logger = structlog.get_logger(__name__)

ACTION_TYPE_TO_DISPATCH_KEY: dict[str, str] = {
    "restock_product": "create_purchase_order",
    "apply_discount": "create_discount_offer",
    "suspend_campaign": "suspend_campaign",
    "resume_campaign": "resume_campaign",
    "create_support_ticket": "open_customer_issue",
    "send_alert": "notify_stakeholders",
}

REFLECTION_SYSTEM_PROMPT = """\
You are the reflection and quality-control layer of an e-commerce AI operations system.

You receive:
- The original user query
- The synthesis result (correlated explanation, root causes, confidence score, recommendations)
- Domain findings from each agent
- Current retry count and maximum retries allowed

Your job is to decide the quality verdict:

## Verdict options

### pass
The synthesis sufficiently answers the query with acceptable confidence.
Use this when:
- The correlated_explanation directly addresses the user's question.
- confidence_score is reasonably high (>= 0.6).
- For lookup/reporting queries: any confident factual answer is a pass.
- Retries are exhausted (retry_count >= max_retries): always return pass regardless of quality.

### retry_with_domains
The synthesis is insufficient and specific domains should be re-queried.
Use this when:
- confidence_score is low (< 0.6) AND retries remain.
- Key domains produced no useful findings but are clearly relevant.
- List only the domains that need re-investigation in domains_to_retry.

### fail
The investigation cannot produce a meaningful answer after retries.
Use this only when retry_count >= max_retries AND synthesis quality is still poor.

## Output
Return JSON matching the ReflectionResult schema:
- verdict: "pass" | "retry_with_domains" | "fail"
- critique: brief explanation of your verdict (1-2 sentences)
- confidence: your confidence in the synthesis quality (0.0 to 1.0)
- domains_to_retry: list of domain names (only for retry_with_domains)
- missing_information: list of what is missing (optional)
"""

ACTION_PROPOSAL_SYSTEM_PROMPT = """\
You are the action planning layer of an e-commerce AI operations system.

You receive a synthesis result with root causes and recommendations.
Your job is to decide if concrete actions should be taken and what they are.

## Action types available
- restock_product: Restock an inventory SKU. Requires sku (string) and quantity (int > 0).
- resume_campaign: Resume a paused marketing campaign. Requires campaign_id (string).
- suspend_campaign: Suspend an active campaign. Requires campaign_id (string).
- create_support_ticket: Open a support ticket. Requires subject (string) and priority (low|medium|high).
- send_alert: Send a stakeholder alert. Requires channel (string) and message (string).

## Rules
- Only propose actions if there are concrete, actionable root causes with high confidence (>= 0.7).
- Do NOT propose actions for lookup or reporting queries.
- Extract specific identifiers (SKU IDs, campaign IDs) from evidence when available.
- If no actions are warranted, return an empty list.
- Each proposal must include: target, action_type, parameters, risk_level, justification, estimated_impact.

Return a JSON array of action proposals (can be empty).
"""


async def _llm_reflect(state: AgentState) -> ReflectionResult:
    """Ask the LLM to evaluate synthesis quality and decide verdict."""
    try:
        AzureChatOpenAI = importlib.import_module("langchain_openai").AzureChatOpenAI
        ChatPromptTemplate = importlib.import_module("langchain_core.prompts").ChatPromptTemplate

        synthesis = state.get("synthesis")
        retry_count = state.get("retry_count", 0)
        intent = state.get("intent")
        findings = state.get("domain_findings", {}) or {}

        synthesis_text = (
            synthesis.model_dump_json(indent=2) if synthesis else '{"error": "no synthesis"}'
        )
        findings_summary = {
            domain: {
                "confidence": df.confidence,
                "anomaly_count": len(df.anomalies),
                "finding_count": len(df.findings),
                "severity": df.severity,
            }
            for domain, df in findings.items()
        }

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", REFLECTION_SYSTEM_PROMPT),
                (
                    "user",
                    "Query: {query}\n\n"
                    "Intent: {intent_type}\n\n"
                    "Synthesis:\n{synthesis}\n\n"
                    "Domain findings summary:\n{findings_summary}\n\n"
                    "Retry count: {retry_count} / {max_retries}",
                ),
            ]
        )

        llm = AzureChatOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_GPT4O_MINI,
            temperature=0.0,
        ).with_structured_output(ReflectionResult)

        messages = prompt.format_messages(
            query=state.get("query", ""),
            intent_type=intent.intent_type if intent else "unknown",
            synthesis=synthesis_text,
            findings_summary=json.dumps(findings_summary, indent=2),
            retry_count=retry_count,
            max_retries=settings.MAX_RETRIES,
        )

        result = await llm.ainvoke(messages)
        logger.info("reflection_llm_success", verdict=result.verdict)
        return result

    except Exception as exc:
        logger.warning("reflection_llm_failed", error=str(exc), exc_info=True)
        # Safe fallback: pass with low confidence so graph terminates
        synthesis = state.get("synthesis")
        return ReflectionResult(
            verdict="pass",
            critique="Reflection LLM unavailable; passing with available synthesis.",
            domains_to_retry=[],
            confidence=synthesis.confidence_score if synthesis else 0.3,
        )


async def _llm_propose_actions(state: AgentState) -> list[ActionProposal]:
    """Ask the LLM to generate action proposals from synthesis root causes."""
    synthesis = state.get("synthesis")
    if not synthesis or not synthesis.root_causes:
        return []

    intent = state.get("intent")
    actionable_intent_types = {
        "business_diagnosis",
        "cross_domain_analysis",
        "inventory_check",
        "marketing_analysis",
        "support_analysis",
        "direct_action",
    }
    if intent and intent.intent_type not in actionable_intent_types:
        return []

    try:
        AzureChatOpenAI = importlib.import_module("langchain_openai").AzureChatOpenAI

        llm = AzureChatOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_GPT4O,
            temperature=float(os.getenv("AZURE_TEMPRATURE", 0.3)),
        )

        user_content = (
            f"Query: {state.get('query', '')}\n\n"
            f"Synthesis:\n{synthesis.model_dump_json(indent=2)}\n\n"
            "Generate action proposals as a JSON array."
        )

        messages = [
            {"role": "system", "content": ACTION_PROPOSAL_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        response = await llm.ainvoke(messages)
        content = response.content if hasattr(response, "content") else str(response)

        # Parse JSON array from response
        raw = content.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw

        proposals_raw: list[dict] = json.loads(raw)
        proposals: list[ActionProposal] = []

        for item in proposals_raw:
            try:
                action_type = item.get("action_type") or item.get("parameters", {}).get(
                    "action_type", ""
                )
                target = item.get("target", "")

                if action_type == "restock_product":
                    params = RestockParams(
                        sku=item["parameters"].get("sku", "SKU-UNKNOWN"),
                        quantity=int(item["parameters"].get("quantity", 100)),
                    )
                elif action_type in ("resume_campaign", "suspend_campaign"):
                    params = CampaignParams(
                        action_type=action_type,
                        campaign_id=item["parameters"].get("campaign_id", "CAMP-UNKNOWN"),
                    )
                elif action_type == "create_support_ticket":
                    params = TicketParams(
                        subject=item["parameters"].get("subject", "Follow-up required"),
                        priority=item["parameters"].get("priority", "medium"),
                    )
                else:
                    continue  # Unknown action type; skip

                proposals.append(
                    ActionProposal(
                        action_id=str(uuid.uuid4()),
                        target=target,
                        parameters=params,
                        risk_level=item.get("risk_level", "medium"),
                        justification=item.get("justification", ""),
                        estimated_impact=item.get("estimated_impact", ""),
                    )
                )
            except Exception as parse_exc:
                logger.warning("action_proposal_parse_error", error=str(parse_exc))
                continue

        logger.info("action_proposals_generated", count=len(proposals))
        return proposals

    except Exception as exc:
        logger.warning("action_proposal_llm_failed", error=str(exc), exc_info=True)
        return []


async def _persist_proposed_actions(proposals: list[ActionProposal], state: dict) -> None:
    """Insert proposed actions into incident_actions with status='proposed'. Best-effort."""
    if not proposals:
        return

    session_id = state.get("thread_id") or state.get("session_id") or str(uuid.uuid4())

    try:
        async with get_session() as session:
            for proposal in proposals:
                await session.execute(
                    text(
                        """
                        INSERT INTO incident_actions
                            (id, action_id, session_id, action_type, target,
                             parameters, risk_level, justification, status,
                             created_at, updated_at)
                        VALUES
                            (:id, :action_id, :session_id, :action_type, :target,
                             CAST(:parameters AS JSONB), :risk_level, :justification,
                             'proposed', NOW(), NOW())
                        ON CONFLICT (action_id) DO NOTHING
                        """
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "action_id": proposal.action_id,
                        "session_id": session_id,
                        "action_type": proposal.action_type,
                        "target": proposal.target,
                        "parameters": proposal.parameters.model_dump_json(),
                        "risk_level": proposal.risk_level,
                        "justification": proposal.justification,
                    },
                )
            await session.commit()
    except Exception:
        pass  # Best-effort; do not fail graph on DB write errors


async def _execute_approved_actions(
    proposals: list[ActionProposal], decision: HITLDecision, state: dict
) -> list[ActionResult]:
    """Execute approved actions with DB-level idempotency guard."""
    proposal_map: dict[str, ActionProposal] = {p.action_id: p for p in proposals}
    results: list[ActionResult] = []

    for action_id in decision.approved_action_ids:
        proposal = proposal_map.get(action_id)
        if proposal is None:
            results.append(
                ActionResult(
                    action_id=action_id,
                    status="skipped",
                    result_payload={"reason": "approved_action_not_found"},
                )
            )
            continue

        async with get_session() as db_session:
            claim = await db_session.execute(
                text(
                    """
                    UPDATE incident_actions
                    SET status = 'executing', updated_at = NOW()
                    WHERE action_id = :aid AND status = 'proposed'
                    RETURNING id
                    """
                ),
                {"aid": action_id},
            )
            claimed = claim.first() is not None
            await db_session.commit()

        if not claimed:
            results.append(
                ActionResult(
                    action_id=action_id,
                    status="skipped",
                    result_payload={"reason": "already_processed"},
                )
            )
            continue

        try:
            dispatch_key = ACTION_TYPE_TO_DISPATCH_KEY.get(proposal.action_type)
            if dispatch_key is None:
                raise ValueError(f"Unknown action_type: {proposal.action_type}")
            tool = ACTION_DISPATCH[dispatch_key]
            result = await tool(proposal)
            final_status = result.status
        except Exception as exc:
            final_status = "failed"
            result = ActionResult(action_id=action_id, status="failed", error=str(exc))

        async with get_session() as db_session:
            await db_session.execute(
                text(
                    """
                    UPDATE incident_actions
                    SET status = :status, executed_at = NOW(), updated_at = NOW()
                    WHERE action_id = :aid
                    """
                ),
                {"status": final_status, "aid": action_id},
            )
            await db_session.execute(
                text(
                    """
                    INSERT INTO audit_logs (id, event_type, action_id, user_id, payload)
                    VALUES (:id, 'action_executed', :aid, :uid, CAST(:payload AS JSONB))
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "aid": action_id,
                    "uid": state.get("user_id"),
                    "payload": json.dumps(result.model_dump(mode="json")),
                },
            )
            await db_session.commit()

        results.append(result)

    for rejected_id in decision.rejected_action_ids:
        results.append(
            ActionResult(
                action_id=rejected_id,
                status="skipped",
                result_payload={"reason": "rejected_by_human"},
            )
        )

    return results


async def reflection_node(state: AgentState) -> dict:
    """Reflect on synthesis quality; propose actions; handle HITL; execute if approved.

    Returns updated state with:
    - reflection_result
    - retry_count (incremented)
    - proposed_actions (if any were generated on a pass verdict)
    - hitl_decision (if HITL was triggered)
    - action_results (if actions were executed)
    """
    # 1. LLM-driven reflection verdict
    reflection_result = await _llm_reflect(state)
    new_retry_count = state.get("retry_count", 0) + 1

    updates: dict = {
        "reflection_result": reflection_result,
        "retry_count": new_retry_count,
    }

    # 2. If verdict is pass, check for actionable proposals
    if reflection_result.verdict == "pass":
        proposals = await _llm_propose_actions(state)

        if proposals:
            # Persist to DB (best-effort)
            await _persist_proposed_actions(proposals, state)
            updates["proposed_actions"] = proposals

            # 3. HITL: pause for human approval
            synthesis = state.get("synthesis")
            summary = synthesis.correlated_explanation if synthesis else ""

            proposals_payload = [
                {**p.model_dump(), "action_type": p.action_type} for p in proposals
            ]

            decision_raw = interrupt(
                {
                    "proposed_actions": proposals_payload,
                    "session_id": state.get("session_id"),
                    "summary": summary,
                }
            )
            decision = HITLDecision.model_validate(decision_raw)
            updates["hitl_decision"] = decision

            # 4. Execute approved actions
            if decision.approved_action_ids:
                action_results = await _execute_approved_actions(proposals, decision, state)
                updates["action_results"] = action_results

    return updates
