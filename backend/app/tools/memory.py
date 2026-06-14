"""Memory search tools for the Memory Agent.

Exposes the incident store as three callable tools so the memory agent can
run semantic and text searches, then hydrate full incident details:

  - ``search_incidents_vector``: Qdrant embedding-based semantic search
  - ``search_incidents_text``: Postgres text/keyword fallback
  - ``fetch_incident_details``: Hydrate a single incident record by ID

All tools return JSON strings so they can be consumed directly by the LLM
inside ``create_agent``.
"""

from __future__ import annotations

import json
import re

import structlog
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.config import settings
from app.db.session import get_session
from app.embeddings import embed_text
from app.vector import qdrant_search

logger = structlog.get_logger(__name__)


# ── Tool argument schemas ─────────────────────────────────────────────────


class SearchVectorArgs(BaseModel):
    query: str = Field(
        description="The query to embed and search for semantically similar incidents"
    )
    top_k: int = Field(default=5, ge=1, le=20, description="Maximum number of results to return")


class SearchTextArgs(BaseModel):
    query: str = Field(description="The query to search for by keyword/text matching")
    limit: int = Field(default=5, ge=1, le=20, description="Maximum number of results to return")


class FetchIncidentArgs(BaseModel):
    incident_id: str = Field(description="The ID of the incident to hydrate")


# ── Private helpers ───────────────────────────────────────────────────────


async def _hydrate_by_ids(ids: list[str], scores: dict[str, float]) -> list[dict]:
    """Fetch full incident rows from Postgres ordered by the given ID list."""
    if not ids:
        return []
    try:
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
    except Exception as exc:
        logger.warning("memory_hydrate_error", error=str(exc))
        return []

    by_id = {str(r["id"]): r for r in rows}
    result = []
    for incident_id in ids:
        row = by_id.get(incident_id)
        if row is None:
            continue
        result.append(
            {
                "incident_id": incident_id,
                "occurred_at": row["occurred_at"].isoformat() if row["occurred_at"] else None,
                "summary": row["summary"],
                "root_causes": list(row["root_causes"] or []),
                "actions_taken": list(row["actions_taken"] or []),
                "outcome": row["outcome"],
                "similarity_score": float(scores.get(incident_id, 0.8)),
            }
        )
    return result


def _build_text_search_sql(query: str) -> tuple[str, dict]:
    """Build parameterised WHERE clause for text-based incident search."""
    lowered = query.lower()
    terms: list[str] = []

    for sku in re.findall(r"sku[-_ ]?\d+", lowered):
        terms.append(sku.replace("_", "-").replace(" ", "-"))

    _stop = {"about", "there", "similar", "incident", "happened", "before"}
    for token in re.findall(r"[a-z0-9-]+", lowered):
        if len(token) >= 5 and token not in _stop:
            terms.append(token)

    if not terms:
        terms = [lowered.strip()]

    params: dict = {}
    like_clauses = []
    for i, term in enumerate(dict.fromkeys(terms)):
        k = f"q{i}"
        like_clauses.append(f"lower(summary) LIKE :{k}")
        like_clauses.append(
            f"EXISTS (SELECT 1 FROM unnest(root_causes) AS c WHERE lower(c) LIKE :{k})"
        )
        params[k] = f"%{term}%"

    where = " OR ".join(like_clauses)
    return where, params


# ── Tool implementations ──────────────────────────────────────────────────


async def _search_incidents_vector(query: str, top_k: int = 5) -> str:
    """Semantic search over past incidents using embeddings.

    Returns a JSON array of incident objects ordered by similarity score.
    Falls back to an empty list if vector infrastructure is unavailable.
    """
    if not settings.AZURE_OPENAI_API_KEY or not settings.AZURE_OPENAI_ENDPOINT:
        return json.dumps([])

    try:
        import asyncio

        vector = await asyncio.wait_for(embed_text(query), timeout=3.0)
        hits = await asyncio.wait_for(
            qdrant_search(
                vector=vector,
                top_k=top_k,
                score_threshold=settings.MEMORY_SIM_THRESHOLD,
            ),
            timeout=3.0,
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

        incidents = await _hydrate_by_ids(ids, scores)
        return json.dumps(incidents)

    except Exception as exc:
        logger.warning("search_incidents_vector_error", error=str(exc))
        return json.dumps([])


async def _search_incidents_text(query: str, limit: int = 5) -> str:
    """Keyword/text search over past incidents in Postgres.

    Returns a JSON array of incident objects. Returns empty list gracefully
    when Postgres is unavailable.
    """
    where, params = _build_text_search_sql(query)
    params["limit"] = limit

    try:
        async with get_session() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            f"""
                        SELECT id, occurred_at, summary, root_causes, actions_taken, outcome
                        FROM incidents
                        WHERE {where}
                        ORDER BY occurred_at DESC
                        LIMIT :limit
                        """
                        ),
                        params,
                    )
                )
                .mappings()
                .all()
            )
    except Exception as exc:
        logger.warning("search_incidents_text_error", error=str(exc))
        return json.dumps([])

    result = [
        {
            "incident_id": str(r["id"]),
            "occurred_at": r["occurred_at"].isoformat() if r["occurred_at"] else None,
            "summary": r["summary"],
            "root_causes": list(r["root_causes"] or []),
            "actions_taken": list(r["actions_taken"] or []),
            "outcome": r["outcome"],
            "similarity_score": 0.8,
        }
        for r in rows
    ]
    return json.dumps(result)


async def _fetch_incident_details(incident_id: str) -> str:
    """Hydrate a single incident record by ID.

    Returns a JSON object with full incident details, or an error message
    if the incident is not found.
    """
    try:
        async with get_session() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            """
                        SELECT id, occurred_at, summary, root_causes, actions_taken, outcome
                        FROM incidents
                        WHERE id = :id
                        """
                        ),
                        {"id": incident_id},
                    )
                )
                .mappings()
                .all()
            )
    except Exception as exc:
        logger.warning("fetch_incident_details_error", incident_id=incident_id, error=str(exc))
        return json.dumps({"error": str(exc)})

    if not rows:
        return json.dumps({"error": f"Incident {incident_id!r} not found"})

    r = rows[0]
    return json.dumps(
        {
            "incident_id": str(r["id"]),
            "occurred_at": r["occurred_at"].isoformat() if r["occurred_at"] else None,
            "summary": r["summary"],
            "root_causes": list(r["root_causes"] or []),
            "actions_taken": list(r["actions_taken"] or []),
            "outcome": r["outcome"],
            "similarity_score": 1.0,
        }
    )


# ── LangChain StructuredTools ─────────────────────────────────────────────

MEMORY_TOOLS = [
    StructuredTool.from_function(
        coroutine=_search_incidents_vector,
        name="search_incidents_vector",
        description=(
            "Semantic embedding-based search over past incidents. "
            "Use this first to find structurally similar past events. "
            "Returns a JSON array of incidents ordered by similarity score."
        ),
        args_schema=SearchVectorArgs,
    ),
    StructuredTool.from_function(
        coroutine=_search_incidents_text,
        name="search_incidents_text",
        description=(
            "Keyword text-based search over past incidents in Postgres. "
            "Use this as fallback if vector search returns no results, "
            "or to search by specific SKU IDs or keywords. "
            "Returns a JSON array of matching incidents."
        ),
        args_schema=SearchTextArgs,
    ),
    StructuredTool.from_function(
        coroutine=_fetch_incident_details,
        name="fetch_incident_details",
        description=(
            "Hydrate a single incident record by its ID for full context. "
            "Use this to get complete details (root causes, actions taken, outcome) "
            "for incidents found via search. "
            "Returns a JSON object with full incident details."
        ),
        args_schema=FetchIncidentArgs,
    ),
]
