"""Memory retrieval node.

Embeds query, searches Qdrant, then hydrates incident records from Postgres.
Falls back to deterministic text match when embedding/vector infra is unavailable.
"""

from __future__ import annotations

import asyncio
import re

from sqlalchemy import text

from app.config import settings
from app.db.session import get_session
from app.embeddings import embed_text
from app.schemas import MemoryContext, PastIncident
from app.vector import qdrant_search


def _empty_context() -> MemoryContext:
    return MemoryContext(
        past_incidents=[],
        recommended_actions_from_history=[],
        relevant_outcomes=[],
    )


def _distill_actions(past: list[PastIncident]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for incident in past:
        for action in incident.actions_taken:
            if action not in seen:
                seen.add(action)
                ordered.append(action)
    return ordered


async def _fetch_incidents_by_ids(ids: list[str], scores: dict[str, float]) -> list[PastIncident]:
    if not ids:
        return []

    async with get_session() as session:
        rows = (
            (
                await session.execute(
                    text(
                        """
                    SELECT id, occurred_at, summary, root_causes, actions_taken, outcome
                    FROM incidents
                    WHERE id = ANY(:ids)
                    ORDER BY occurred_at DESC
                    """
                    ),
                    {"ids": ids},
                )
            )
            .mappings()
            .all()
        )

    ordered: list[PastIncident] = []
    by_id = {str(r["id"]): r for r in rows}
    for incident_id in ids:
        row = by_id.get(incident_id)
        if row is None:
            continue
        ordered.append(
            PastIncident(
                incident_id=incident_id,
                occurred_at=row["occurred_at"],
                summary=row["summary"],
                root_causes=list(row["root_causes"] or []),
                actions_taken=list(row["actions_taken"] or []),
                outcome=row["outcome"],
                similarity_score=float(scores.get(incident_id, 0.0)),
            )
        )
    return ordered


async def _fallback_text_match(query: str) -> list[PastIncident]:
    """Text-based incident search. Returns [] gracefully when DB is unavailable."""
    lowered = query.lower()
    terms: list[str] = []

    sku_terms = re.findall(r"sku[-_ ]?\d+", lowered)
    for sku in sku_terms:
        terms.append(sku.replace("_", "-").replace(" ", "-"))

    for token in re.findall(r"[a-z0-9-]+", lowered):
        if len(token) >= 5 and token not in {"about", "there", "similar", "incident"}:
            terms.append(token)

    if not terms:
        terms = [lowered.strip()]

    like_clauses: list[str] = []
    params: dict[str, str] = {}
    for i, term in enumerate(dict.fromkeys(terms)):
        key = f"q{i}"
        like_clauses.append(f"lower(summary) LIKE :{key}")
        like_clauses.append(
            f"EXISTS (SELECT 1 FROM unnest(root_causes) AS cause WHERE lower(cause) LIKE :{key})"
        )
        params[key] = f"%{term}%"

    where_clause = " OR ".join(like_clauses)

    try:
        async with get_session() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            """
                        SELECT id, occurred_at, summary, root_causes, actions_taken, outcome
                        FROM incidents
                        WHERE """
                            + where_clause
                            + """
                        ORDER BY occurred_at DESC
                        LIMIT 5
                        """
                        ),
                        params,
                    )
                )
                .mappings()
                .all()
            )
    except Exception:
        # DB not available (e.g., no running Postgres in unit-test environment)
        return []

    return [
        PastIncident(
            incident_id=str(r["id"]),
            occurred_at=r["occurred_at"],
            summary=r["summary"],
            root_causes=list(r["root_causes"] or []),
            actions_taken=list(r["actions_taken"] or []),
            outcome=r["outcome"],
            similarity_score=0.8,
        )
        for r in rows
    ]


async def memory_retrieve_node(state: dict) -> dict:
    """Retrieve semantically similar past incidents."""
    query = state.get("query", "")
    if not query:
        return {"memory_context": _empty_context()}

    if not settings.AZURE_OPENAI_API_KEY or not settings.AZURE_OPENAI_ENDPOINT:
        incidents = await _fallback_text_match(query)
        context = MemoryContext(
            past_incidents=incidents,
            recommended_actions_from_history=_distill_actions(incidents),
            relevant_outcomes=[p.outcome for p in incidents if p.outcome],
        )
        return {"memory_context": context}

    incidents: list[PastIncident] = []

    try:
        vector = await asyncio.wait_for(embed_text(query), timeout=2.0)
        hits = await asyncio.wait_for(
            qdrant_search(
                vector=vector,
                top_k=5,
                score_threshold=settings.MEMORY_SIM_THRESHOLD,
            ),
            timeout=2.0,
        )

        ids: list[str] = []
        scores: dict[str, float] = {}
        for hit in hits:
            payload = getattr(hit, "payload", {}) or {}
            incident_id = payload.get("incident_id")
            if incident_id:
                incident_id = str(incident_id)
                ids.append(incident_id)
                scores[incident_id] = float(getattr(hit, "score", 0.0))

        incidents = await _fetch_incidents_by_ids(ids, scores)
    except Exception:
        incidents = await _fallback_text_match(query)

    context = MemoryContext(
        past_incidents=incidents,
        recommended_actions_from_history=_distill_actions(incidents),
        relevant_outcomes=[p.outcome for p in incidents if p.outcome],
    )
    return {"memory_context": context}
