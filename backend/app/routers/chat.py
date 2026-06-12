"""POST /chat — NDJSON streaming endpoint.

Stream contract (§19.3, §17.4, §30.11):
  - Emits one NDJSON object per line.
  - Intermediate events: node_start, domain_finding, synthesis.
  - Terminal events (exactly ONE per stream, never both):
      * hitl_pending — graph paused at HITL gate
      * final        — graph reached END
      * error        — unrecoverable exception

Blueprint invariant §30.11: a stream ends with EITHER hitl_pending OR final,
never both, never neither.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
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
        "intent_classifier",
        "sales_agent",
        "inventory_agent",
        "marketing_agent",
        "support_agent",
        "memory_retrieve",
        "join_findings",
        "synthesizer",
        "reflection",
        "action_agent",
        "hitl_node",
        "execute_actions",
        "assemble_response",
        "persist_incident",
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
    proposed_actions_snapshot: list = []

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

            # ── synthesis ───────────────────────────────────────────────────
            elif kind == "on_chain_end" and name == "synthesizer":
                output = event.get("data", {}).get("output") or {}
                synthesis = output.get("synthesis")
                if synthesis is not None:
                    payload = (
                        synthesis.model_dump() if hasattr(synthesis, "model_dump") else synthesis
                    )
                    yield _ndjson({"type": "synthesis", "synthesis": payload})

            # ── capture proposed_actions for hitl_pending terminal event ────
            elif kind == "on_chain_end" and name == "action_agent":
                output = event.get("data", {}).get("output") or {}
                proposed_actions_snapshot = output.get("proposed_actions") or []

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
        # Terminal: graph is paused at hitl_node — emit hitl_pending (§17.4).
        # Serialize action_type explicitly because it is a @property (§30.13).
        actions_payload = []
        for p in proposed_actions_snapshot:
            if hasattr(p, "model_dump"):
                d = p.model_dump()
                d["action_type"] = p.action_type
                actions_payload.append(d)
            else:
                actions_payload.append(p)

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
        yield _ndjson({"type": "final", "final_response": fr_payload})


@router.post("/chat")
async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    """Start a diagnosis; returns an NDJSON stream of agent steps.

    Terminal event is either hitl_pending (graph paused) or final (graph done).
    Never both, never neither (§30.11).
    """
    user_id: str = request.state.user_id
    thread_id = str(uuid.uuid4())

    # ── Create sessions row (Layer 2) ────────────────────────────────────────
    async with get_session() as session:
        await session.execute(
            text(
                """
                INSERT INTO sessions (thread_id, user_id, query, status, created_at, updated_at)
                VALUES (:thread_id, :user_id, :query, 'active', NOW(), NOW())
                ON CONFLICT (thread_id) DO NOTHING
                """
            ),
            {"thread_id": thread_id, "user_id": user_id, "query": body.query},
        )
        await session.commit()

    # ── Build initial AgentState ─────────────────────────────────────────────
    initial_state: dict = {
        "session_id": thread_id,
        "thread_id": thread_id,
        "user_id": user_id,
        "query": body.query,
        "retry_count": 0,
        "domain_findings": {},
        "proposed_actions": [],
        "action_results": [],
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
