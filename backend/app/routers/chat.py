"""POST /chat — NDJSON streaming endpoint.

Stream contract:
  - Emits one NDJSON object per line.
  - Intermediate events: node_start, domain_finding, synthesis.
  - Terminal events (exactly ONE per stream, never both):
      * hitl_pending — graph paused at HITL gate
      * final        — graph reached END
      * error        — unrecoverable exception

"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import text

from app.db.session import get_session
from app.graph.hitl_utils import _is_awaiting_hitl
from app.graph.runtime import get_compiled_graph
from app.schemas import ChatRequest

log = structlog.get_logger(__name__)

router = APIRouter(tags=["chat"])

# Node names that trigger a node_start stream event.
_GRAPH_NODES = frozenset(
    {
        "orchestrator",
        "sales_agent",
        "inventory_agent",
        "marketing_agent",
        "support_agent",
        "memory_agent",
        "synthesizer",
        "reflection",
        "action_executor",
        "response_composer",
    }
)

# Domain agent nodes (emit domain_finding events on completion).
_DOMAIN_NODES = frozenset(
    {
        "sales_agent",
        "inventory_agent",
        "marketing_agent",
        "support_agent",
    }
)


def _ndjson(obj: dict) -> str:
    """Serialize dict to a single NDJSON line (newline-terminated)."""
    return json.dumps(obj, default=str) + "\n"


def _domain_from_node(node_name: str) -> str:
    return node_name.replace("_agent", "")


async def _event_generator(
    graph,
    initial_state: dict,
    config: dict,
    thread_id: str,
) -> AsyncGenerator[str, None]:
    """Async generator that drives the graph and emits NDJSON events.

    Guarantees exactly one terminal event (hitl_pending | final | error).
    """
    action_requests_snapshot: list = []

    try:
        async for event in graph.astream_events(initial_state, config, version="v2"):
            kind = event.get("event", "")
            name = event.get("name", "")

            # ── node_start ──────────────────────────────────────────────────
            if kind == "on_chain_start" and name in _GRAPH_NODES:
                yield _ndjson(
                    {
                        "type": "node_start",
                        "node": name,
                        "ts": datetime.now(UTC).isoformat(),
                    }
                )

            # ── domain_finding ──────────────────────────────────────────────
            elif kind == "on_chain_end" and name in _DOMAIN_NODES:
                output = event.get("data", {}).get("output") or {}
                findings = output.get("domain_findings", {})
                domain = _domain_from_node(name)
                finding = findings.get(domain)
                if finding is not None:
                    payload = finding.model_dump() if hasattr(finding, "model_dump") else finding
                    yield _ndjson(
                        {
                            "type": "domain_finding",
                            "domain": domain,
                            "finding": payload,
                        }
                    )
                # Accumulate action_requests from domain agents
                reqs = output.get("action_requests") or []
                if reqs:
                    action_requests_snapshot.extend(reqs)

            # ── synthesis ───────────────────────────────────────────────────
            elif kind == "on_chain_end" and name == "synthesizer":
                output = event.get("data", {}).get("output") or {}
                synthesis = output.get("synthesis")
                if synthesis is not None:
                    payload = (
                        synthesis.model_dump() if hasattr(synthesis, "model_dump") else synthesis
                    )
                    yield _ndjson({"type": "synthesis", "synthesis": payload})

    except Exception as exc:
        log.exception("chat.stream.error", thread_id=thread_id, error=str(exc))
        yield _ndjson({"type": "error", "message": str(exc)})
        return

    # ── Determine terminal event from checkpoint (§17.4, §30.11) ───────────
    try:
        snapshot = await graph.aget_state(config)
    except Exception as exc:
        log.exception("chat.stream.aget_state_error", thread_id=thread_id, error=str(exc))
        yield _ndjson({"type": "error", "message": f"Failed to read graph state: {exc}"})
        return

    if _is_awaiting_hitl(snapshot):
        # Terminal: graph is paused inside action_executor (HITL via interrupt()) — emit hitl_pending.
        # Serialize action_type explicitly because it is a @property.
        actions_payload = []
        for r in action_requests_snapshot:
            if hasattr(r, "model_dump"):
                d = r.model_dump()
                d["action_type"] = r.action_type
                actions_payload.append(d)
            else:
                actions_payload.append(r)

        # When interrupt() fires the action_executor node never emits on_chain_end, so
        # action_requests_snapshot may be empty.  Fall back to the interrupt payload
        # that LangGraph checkpointed in snapshot.tasks[*].interrupts.
        if not actions_payload:
            for task in getattr(snapshot, "tasks", ()) or ():
                for intr in getattr(task, "interrupts", ()) or ():
                    intr_val = getattr(intr, "value", None)
                    if isinstance(intr_val, dict) and "proposed_actions" in intr_val:
                        actions_payload = intr_val["proposed_actions"]
                        break
                if actions_payload:
                    break

        yield _ndjson(
            {
                "type": "hitl_pending",
                "proposed_actions": actions_payload,
                "thread_id": thread_id,
            }
        )
    else:
        # Terminal: graph completed — emit final.
        fr = snapshot.values.get("final_response")
        fr_payload = fr.model_dump() if hasattr(fr, "model_dump") else fr or {}

        # Append AI summary as an AIMessage so future turns see it in history.
        summary = fr_payload.get("summary", "") if isinstance(fr_payload, dict) else ""
        if summary:
            try:
                await graph.aupdate_state(
                    config,
                    {"messages": [AIMessage(content=summary)]},
                )
            except Exception:
                pass  # Best-effort — do not fail the response

        yield _ndjson({"type": "final", "final_response": fr_payload})


@router.post("/chat")
async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    """Start a diagnosis; returns an NDJSON stream of agent steps.

    If the request includes a thread_id the server reuses the existing
    checkpoint (conversational memory).  If omitted a new UUID is generated.

    Terminal event is either hitl_pending (graph paused) or final (graph done).
    Never both, never neither (§30.11).
    """
    user_id: str = request.state.user_id
    # Reuse caller-supplied thread_id for multi-turn, or start fresh.
    thread_id: str = body.thread_id or str(uuid.uuid4())

    # ── Upsert sessions row (Layer 2) ─────────────────────────────────────
    async with get_session() as session:
        await session.execute(
            text(
                """
                INSERT INTO sessions (id, thread_id, user_id, query, status, created_at, updated_at)
                VALUES (:id, :thread_id, :user_id, :query, 'active', NOW(), NOW())
                ON CONFLICT (thread_id) DO UPDATE
                    SET query = EXCLUDED.query,
                        updated_at = NOW()
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "thread_id": thread_id,
                "user_id": user_id,
                "query": body.query,
            },
        )
        await session.commit()

    # ── Build initial AgentState ─────────────────────────────────────────
    # messages only contains the new HumanMessage; the add_messages reducer
    # will merge this with the persisted history from the checkpoint.
    # NOTE: Only include fields that should be set fresh per-turn.
    # Do NOT include retry_count, action_requests, action_results, or
    # domain_findings — these either use reducers or should
    # persist across the graph run from their default/checkpoint values.
    initial_state: dict = {
        "session_id": thread_id,
        "thread_id": thread_id,
        "user_id": user_id,
        "query": body.query,
        "messages": [HumanMessage(content=body.query)],
        "otel_trace_id": "",
    }

    config = {"configurable": {"thread_id": thread_id}}
    graph = get_compiled_graph()

    log.info("chat.stream.start", thread_id=thread_id, user_id=user_id)

    return StreamingResponse(
        _event_generator(graph, initial_state, config, thread_id),
        media_type="application/x-ndjson",
        headers={"X-Thread-Id": thread_id},
    )
