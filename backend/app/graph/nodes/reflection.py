"""Reflection node + Action Executor node.

Split into two nodes to fix the HITL interrupt() re-execution bug:
  - reflection_node: Evaluates synthesis quality, proposes actions, calls interrupt().
  - action_executor_node: Executes approved actions after HITL resume.

When interrupt() fires, the graph checkpoints and pauses. On resume, LangGraph
re-enters at the NEXT node (action_executor), NOT the reflection node. This
ensures proposals don't get regenerated with new UUIDs.

All confidence checks and retry decisions are made by the LLM.
No hardcoded thresholds or keyword matching.
"""

from __future__ import annotations

import json
import uuid
from typing import Literal

import structlog
from langchain_openai import AzureChatOpenAI
from langgraph.types import interrupt
from pydantic import BaseModel
from sqlalchemy import text

from app.config import settings
from app.db.session import get_session
from app.graph.state import AgentState
from app.schemas import (
    ActionProposal,
    ActionProposalList,
    ActionResult,
    HITLDecision,
    ReflectionResult,
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

# Must align with IntentClassification.intent_type literal values in app/schemas.py
NON_ACTIONABLE_INTENTS: frozenset[str] = frozenset(
    {
        "memory_recall",
        "reporting",
        "irrelevant",
    }
)

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
The synthesis sufficiently answers the query with acceptable quality.
Use this when:
- The correlated_explanation directly addresses the user's question.
- The findings contain concrete, relevant data.
- For lookup/reporting queries: any factual answer is a pass.
- Retries are exhausted (retry_count >= max_retries): always return pass regardless of quality.

### retry_with_domains
The synthesis is insufficient and specific domains should be re-queried.
Use this when:
- Key domains produced no useful findings but are clearly relevant to the query.
- The explanation does not address the user's actual question.
- Retries still remain (retry_count < max_retries).
- List only the domains that need re-investigation in domains_to_retry.

## Output
Return JSON matching the ReflectionResult schema:
- verdict: "pass" | "retry_with_domains"
- critique: brief explanation of your verdict (1-2 sentences)
- domains_to_retry: list of domain names (only for retry_with_domains, else empty)
"""

ACTION_PROPOSAL_SYSTEM_PROMPT = """\
You are the action planning layer of an e-commerce AI operations system.

You receive a synthesis result. Decide if concrete actions should be taken.

## Action types available
- restock_product: needs sku (string) and quantity (int > 0).
- resume_campaign / suspend_campaign: needs campaign_id (string).
- create_support_ticket: needs subject (string) and priority (low|medium|high).
- send_alert: needs channel (string) and message (string).

## When to propose
Propose actions ONLY when:
- synthesis.status == "answered", AND
- synthesis.root_causes is non-empty, AND
- A root cause has a clear, identifiable target (specific SKU, campaign, etc.)

## When NOT to propose
- Lookup / reporting queries (no root causes).
- Vague root causes without a target identifier.
- synthesis.status is "partial" or "insufficient".

If no actions are warranted, return an empty list.
Each proposal must cite specific evidence from synthesis.root_causes.
"""

ACTION_ONLY_PROPOSAL_PROMPT = """\
The user has explicitly requested a direct operational action.
Parse their request and generate the appropriate action proposal(s).

## Action types available
- restock_product: needs sku (string) and quantity (int > 0).
- resume_campaign / suspend_campaign: needs campaign_id (string).
- create_support_ticket: needs subject (string) and priority (low|medium|high).
- send_alert: needs channel (string) and message (string).

## Rules
- Generate exactly the action(s) the user is requesting.
- If quantity/campaign_id is not specified, use a sensible default and note it in justification.
- Set risk_level based on scale: large quantity restocks = "medium", routine = "low".
- estimated_impact should state the expected business outcome.
"""


async def _llm_reflect(state: AgentState) -> ReflectionResult:
    """Ask the LLM to evaluate synthesis quality and decide verdict."""
    try:
        synthesis = state.get("synthesis")
        retry_count = state.get("retry_count", 0)
        intent = state.get("intent")
        findings = state.get("domain_findings", {}) or {}

        synthesis_text = (
            synthesis.model_dump_json(indent=2) if synthesis else '{"error": "no synthesis"}'
        )
        findings_summary = {
            domain: {
                "status": df.status,
                "anomaly_count": len(df.anomalies),
                "finding_count": len(df.findings),
                "severity": df.severity,
            }
            for domain, df in findings.items()
        }

        user_msg = (
            f"Query: {state.get('query', '')}\n\n"
            f"Intent: {intent.intent_type if intent else 'unknown'}\n\n"
            f"Synthesis:\n{synthesis_text}\n\n"
            f"Domain findings summary:\n{json.dumps(findings_summary, indent=2)}\n\n"
            f"Retry count: {retry_count} / {settings.MAX_RETRIES}"
        )

        llm = AzureChatOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_GPT4O_MINI,
            temperature=0.0,
        ).with_structured_output(ReflectionResult)

        result = await llm.ainvoke(
            [
                {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ]
        )
        logger.info("reflection_llm_success", verdict=result.verdict)
        return result

    except Exception as exc:
        logger.warning("reflection_llm_failed", error=str(exc), exc_info=True)
        # Safe fallback: pass so graph terminates
        synthesis = state.get("synthesis")
        return ReflectionResult(
            verdict="pass",
            critique="Reflection LLM unavailable; passing with available synthesis.",
            domains_to_retry=[],
        )


class _FlatProposal(BaseModel):
    """Flat LLM-facing schema — avoids discriminated-union issues where the LLM omits
    the action_type discriminator field from nested parameters."""

    action_type: Literal[
        "restock_product",
        "apply_discount",
        "suspend_campaign",
        "resume_campaign",
        "create_support_ticket",
        "send_alert",
    ]
    target: str
    # Flat params — LLM fills what's relevant for the chosen action_type
    sku: str | None = None
    quantity: int | None = None
    campaign_id: str | None = None
    percent: float | None = None
    subject: str | None = None
    priority: Literal["low", "medium", "high"] = "medium"
    channel: str | None = None
    message: str | None = None
    risk_level: Literal["low", "medium", "high"] = "low"
    justification: str
    estimated_impact: str


class _FlatProposalList(BaseModel):
    proposals: list[_FlatProposal] = []


def _flat_to_action_proposal(raw: _FlatProposal) -> ActionProposal | None:
    """Convert flat LLM proposal to a typed ActionProposal with discriminated params."""
    try:
        if raw.action_type == "restock_product":
            params: dict = {
                "action_type": "restock_product",
                "sku": raw.sku or raw.target,
                "quantity": raw.quantity or 100,
            }
        elif raw.action_type in ("resume_campaign", "suspend_campaign"):
            params = {"action_type": raw.action_type, "campaign_id": raw.campaign_id or raw.target}
        elif raw.action_type == "apply_discount":
            params = {
                "action_type": "apply_discount",
                "sku": raw.sku or raw.target,
                "percent": raw.percent or 10.0,
            }
        elif raw.action_type == "create_support_ticket":
            params = {
                "action_type": "create_support_ticket",
                "subject": raw.subject or f"Support ticket for {raw.target}",
                "priority": raw.priority,
            }
        elif raw.action_type == "send_alert":
            params = {
                "action_type": "send_alert",
                "channel": raw.channel or "ops",
                "message": raw.message or f"Alert for {raw.target}",
            }
        else:
            return None
        return ActionProposal(
            target=raw.target,
            parameters=params,  # type: ignore[arg-type]
            risk_level=raw.risk_level,
            justification=raw.justification,
            estimated_impact=raw.estimated_impact,
        )
    except Exception as exc:
        logger.warning(
            "flat_proposal_conversion_failed", error=str(exc), action_type=raw.action_type
        )
        return None


async def _llm_propose_from_query(state: AgentState) -> list[ActionProposal]:
    """For action_only intents: generate proposals directly from the user query.

    Uses a flat schema (_FlatProposalList) to avoid the discriminated-union issue
    where the LLM omits the action_type field inside nested parameters dicts.
    """
    try:
        llm = AzureChatOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_GPT4O,
            temperature=settings.AZURE_TEMPERATURE,
        ).with_structured_output(_FlatProposalList, method="function_calling")

        result: _FlatProposalList = await llm.ainvoke(
            [
                {"role": "system", "content": ACTION_ONLY_PROPOSAL_PROMPT},
                {"role": "user", "content": f"User request: {state.get('query', '')}"},
            ]
        )
        flat_proposals = result.proposals if result else []
        proposals = [
            p for raw in flat_proposals if (p := _flat_to_action_proposal(raw)) is not None
        ]
        logger.info("action_only_proposals_generated", count=len(proposals))
        return proposals
    except Exception as exc:
        logger.warning("action_only_proposal_failed", error=str(exc), exc_info=True)
        return []


async def _llm_propose_actions(state: AgentState) -> list[ActionProposal]:
    """Ask the LLM to generate action proposals from synthesis root causes."""
    synthesis = state.get("synthesis")
    intent = state.get("intent")

    if intent and intent.intent_type in NON_ACTIONABLE_INTENTS:
        return []

    # action_only / direct_action: user explicitly requested an action — generate directly from query.
    # Check both the boolean flag AND intent_type literal for robustness (LLM may set one but not both).
    if intent and (intent.action_only or intent.intent_type == "direct_action"):
        return await _llm_propose_from_query(state)

    if not synthesis or not synthesis.root_causes:
        return []

    try:
        llm = AzureChatOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_GPT4O,
            temperature=settings.AZURE_TEMPERATURE,
        ).with_structured_output(ActionProposalList, method="function_calling")

        messages = [
            {"role": "system", "content": ACTION_PROPOSAL_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Query: {state.get('query', '')}\n\n"
                    f"Synthesis:\n{synthesis.model_dump_json(indent=2)}\n\n"
                    "Generate action proposals."
                ),
            },
        ]

        result: ActionProposalList = await llm.ainvoke(messages)
        proposals = result.proposals if result else []

        # Ensure every proposal has a stable action_id
        for p in proposals:
            if not p.action_id:
                p.action_id = str(uuid.uuid4())

        logger.info("action_proposals_generated", count=len(proposals))
        return proposals

    except Exception as exc:
        logger.warning("action_proposal_llm_failed", error=str(exc), exc_info=True)
        return []


async def _persist_proposed_actions(proposals: list[ActionProposal], state: dict) -> bool:
    """Insert proposed actions into incident_actions with status='proposed'.

    Returns True if persistence succeeded, False otherwise.
    On failure, logs the error (callers can decide how to handle).
    """
    if not proposals:
        return True

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
        return True
    except Exception as exc:
        logger.error(
            "persist_proposed_actions_failed",
            error=str(exc),
            proposal_count=len(proposals),
            session_id=session_id,
            exc_info=True,
        )
        return False


async def reflection_node(state: AgentState) -> dict:
    """Reflect on synthesis quality; propose actions if passing.

    This node handles:
    1. LLM-driven reflection verdict (pass or retry_with_domains)
    2. If pass + actionable root causes: generate action proposals
    3. If proposals exist: persist to DB and call interrupt() for HITL

    For action_only intents (user explicitly requested an action):
    - Skip the reflection LLM entirely (no synthesis to evaluate)
    - Generate proposals directly from the user query
    - Route to action_executor for HITL

    On HITL resume, execution continues at the NEXT node (action_executor_node),
    NOT this node. This prevents re-generation of proposals with new UUIDs.
    """
    intent = state.get("intent")

    # Short-circuit for action_only / direct_action: no synthesis exists, propose from query directly.
    if intent and (intent.action_only or intent.intent_type == "direct_action"):
        reflection_result = ReflectionResult(
            verdict="pass",
            critique="Action-only intent: proposing requested action directly without diagnosis.",
            domains_to_retry=[],
        )
        logger.info("reflection_action_only_shortcut", query=state.get("query", "")[:80])
        proposals = await _llm_propose_actions(state)  # routes to _llm_propose_from_query

        updates: dict = {"reflection_result": reflection_result}
        if proposals:
            persisted = await _persist_proposed_actions(proposals, state)
            if persisted:
                updates["proposed_actions"] = proposals
            else:
                logger.error("proposals_dropped_persistence_failed", proposal_count=len(proposals))
        return updates

    # 1. LLM-driven reflection verdict
    reflection_result = await _llm_reflect(state)
    retry_count = state.get("retry_count", 0)

    logger.info(
        "reflection_verdict",
        verdict=reflection_result.verdict,
        retry_count=retry_count,
    )

    updates: dict = {
        "reflection_result": reflection_result,
    }

    # Fix #2: Only increment retry_count when actually retrying
    if reflection_result.verdict == "retry_with_domains":
        updates["retry_count"] = retry_count + 1
    # else: retry_count stays unchanged (don't consume budget on pass)

    # 2. If verdict is pass, check for actionable proposals
    if reflection_result.verdict == "pass":
        proposals = await _llm_propose_actions(state)

        if proposals:
            persisted = await _persist_proposed_actions(proposals, state)
            if persisted:
                updates["proposed_actions"] = proposals
            else:
                logger.error(
                    "proposals_dropped_persistence_failed",
                    proposal_count=len(proposals),
                )
                # Don't set proposed_actions → edge routes to response_composer
            # NOTE: No interrupt() here. The graph routes to action_executor_node
            # via the conditional edge (route_after_reflection sees proposed_actions).
            # action_executor_node calls interrupt() at its start, so on resume
            # it re-enters action_executor (not reflection) — fixing the UUID mismatch bug.

    return updates


async def _execute_approved_actions(
    proposals: list[ActionProposal],
    decision: HITLDecision,
    state: dict,
) -> list[ActionResult]:
    """Claim → dispatch → audit for each approved action. Returns list[ActionResult].

    Rejected actions are included with status='skipped'.
    DB-level idempotency: claims the row with UPDATE WHERE status='proposed'.
    """
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

        # DB-level idempotency: claim the action row
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

        # Update DB status + audit log
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

    # Record rejected actions
    for rejected_id in decision.rejected_action_ids:
        results.append(
            ActionResult(
                action_id=rejected_id,
                status="skipped",
                result_payload={"reason": "rejected_by_human"},
            )
        )

    return results


async def action_executor_node(state: AgentState) -> dict:
    """Execute approved actions after HITL approval.

    This node:
    1. Calls interrupt() to pause for human approval (HITL gate)
    2. On resume, receives the HITLDecision
    3. Executes approved actions with DB-level idempotency

    Because interrupt() is called HERE (not in reflection_node), when the graph
    resumes it re-enters THIS node. The proposals in state are stable (generated
    by reflection_node in a previous step), so the action_ids always match.
    """
    proposals = state.get("proposed_actions") or []

    if not proposals:
        # No proposals — nothing to do (shouldn't normally reach here)
        return {}

    # ── HITL Gate: pause for human approval ──
    synthesis = state.get("synthesis")
    summary = synthesis.correlated_explanation if synthesis else ""

    proposals_payload = [p.model_dump(mode="json") for p in proposals]

    decision_raw = interrupt(
        {
            "proposed_actions": proposals_payload,
            "session_id": state.get("session_id"),
            "summary": summary,
        }
    )

    # ── After resume: parse decision and execute ──
    decision = HITLDecision.model_validate(decision_raw)
    results = await _execute_approved_actions(proposals, decision, state)
    return {"hitl_decision": decision, "action_results": results}
