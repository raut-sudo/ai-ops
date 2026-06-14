"""Orchestrator-specific tools.

Provides two lightweight tools for the orchestrator agent to enrich its
routing decisions with historical context and operational policies:

  - ``recall_similar_incidents``: text-based incident search (Postgres)
  - ``get_related_policies``: keyword match against the operational policy set

These tools are READ-ONLY and designed for fast, sub-second lookup. They do
NOT perform business analysis — that is left to the domain agents.
"""

from __future__ import annotations

import re

import structlog
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db.session import get_session

logger = structlog.get_logger(__name__)

# ── Operational policy library ────────────────────────────────────────────
# Key: tuple of trigger keywords, Value: policy text
# The orchestrator can retrieve these to resolve ambiguous routing decisions.

_POLICIES: list[tuple[frozenset[str], str]] = [
    (
        frozenset({"revenue", "sales", "drop", "decline", "fell", "down", "decrease"}),
        "POLICY-001 — Revenue drops require multi-domain investigation: always route to "
        "sales + inventory + marketing. If complaints are spiking simultaneously, add support.",
    ),
    (
        frozenset({"stockout", "stock", "inventory", "sku", "restock", "replenish"}),
        "POLICY-002 — Inventory queries route to inventory_agent. If the stockout coincides "
        "with a revenue anomaly, also include sales. Propose restock action when zero-stock "
        "is confirmed.",
    ),
    (
        frozenset({"campaign", "roas", "marketing", "ad", "spend", "ctr", "promotion"}),
        "POLICY-003 — Campaign performance queries route to marketing_agent. If ROAS < 1.0 "
        "is confirmed, the system may propose campaign suspension.",
    ),
    (
        frozenset({"complaint", "ticket", "refund", "return", "sentiment", "churn", "support"}),
        "POLICY-004 — Support queries route to support_agent. High complaint rates on specific "
        "SKUs should also trigger inventory review.",
    ),
    (
        frozenset({"why", "cause", "reason", "explain", "investigate", "diagnose"}),
        "POLICY-005 — Root-cause questions require broad investigation. Default to all four "
        "domain agents plus memory retrieval for historical context.",
    ),
    (
        frozenset({"history", "before", "previous", "similar", "last time", "recurring"}),
        "POLICY-006 — Historical questions should set memory_needed=True. Use intent_type "
        "'memory_recall' if the question is solely about past incidents.",
    ),
    (
        frozenset({"restock", "pause", "suspend", "resume", "discount", "create ticket", "alert"}),
        "POLICY-007 — Action requests require action_only=True. Route to the minimum necessary "
        "domain to confirm the action target, then execute.",
    ),
    (
        frozenset({"top", "best", "list", "show", "summary", "report", "status"}),
        "POLICY-008 — Reporting and lookup queries use intent_type 'reporting'. "
        "Route to the single most relevant domain. memory_needed=False.",
    ),
]


def _match_policies(query: str) -> list[str]:
    """Return all policies whose trigger keywords overlap with the query tokens."""
    tokens = frozenset(re.findall(r"[a-z0-9]+", query.lower()))
    matched: list[str] = []
    for keywords, policy_text in _POLICIES:
        if keywords & tokens:  # non-empty intersection
            matched.append(policy_text)
    return matched


# ── Tool implementations ──────────────────────────────────────────────────


class RecallSimilarIncidentsArgs(BaseModel):
    query: str = Field(description="The current user query or situation description")
    limit: int = Field(default=3, ge=1, le=10, description="Max incidents to retrieve")


class GetRelatedPoliciesArgs(BaseModel):
    query: str = Field(description="The current user query")


async def _recall_similar_incidents(query: str, limit: int = 3) -> str:
    """Text-based search for past incidents relevant to the query.

    Returns a formatted summary of the most relevant incidents. Falls back to
    an empty result gracefully when the DB is unavailable.
    """
    lowered = query.lower()
    terms: list[str] = []

    # Extract SKU references
    for sku in re.findall(r"sku[-_ ]?\d+", lowered):
        terms.append(sku.replace("_", "-").replace(" ", "-"))

    # Extract meaningful tokens (≥5 chars, skip stop-words)
    _stop = {"about", "there", "similar", "incident", "happened", "before", "their"}
    for token in re.findall(r"[a-z0-9-]+", lowered):
        if len(token) >= 5 and token not in _stop:
            terms.append(token)

    if not terms:
        terms = [lowered.strip()]

    like_clauses = [f"summary ILIKE :term{i}" for i in range(len(terms))]
    sql = f"""
        SELECT summary, occurred_at, outcome
        FROM incidents
        WHERE {" OR ".join(like_clauses)}
        ORDER BY occurred_at DESC
        LIMIT :limit
    """
    params: dict = {f"term{i}": f"%{t}%" for i, t in enumerate(terms)}
    params["limit"] = limit

    try:
        async with get_session() as session:
            rows = (await session.execute(text(sql), params)).mappings().all()
    except Exception as exc:
        logger.warning("recall_similar_incidents_db_error", error=str(exc))
        return "No similar incidents found (database unavailable)."

    if not rows:
        return "No similar incidents found in the historical record."

    lines = ["Similar past incidents:"]
    for row in rows:
        occurred = row["occurred_at"].strftime("%Y-%m-%d") if row["occurred_at"] else "unknown"
        outcome = f" Outcome: {row['outcome']}" if row["outcome"] else ""
        lines.append(f"  [{occurred}] {row['summary']}.{outcome}")
    return "\n".join(lines)


async def _get_related_policies(query: str) -> str:
    """Return operational policies relevant to the query.

    Matches keywords in the query against the policy library and returns
    the relevant routing rules. Always returns at least one default policy.
    """
    matched = _match_policies(query)
    if not matched:
        return (
            "POLICY-DEFAULT — No specific policy matched. Apply standard routing: "
            "classify the most relevant single domain for reporting queries; use "
            "multi-domain investigation for anomaly or root-cause queries."
        )
    return "\n\n".join(matched)


# ── LangChain StructuredTools ─────────────────────────────────────────────

ORCHESTRATOR_TOOLS = [
    StructuredTool.from_function(
        coroutine=_recall_similar_incidents,
        name="recall_similar_incidents",
        description=(
            "Search the incident history for past events similar to the current query. "
            "Use this when the query is ambiguous or when historical context would help "
            "determine which domains to route to. Returns summaries of matching incidents."
        ),
        args_schema=RecallSimilarIncidentsArgs,
    ),
    StructuredTool.from_function(
        coroutine=_get_related_policies,
        name="get_related_policies",
        description=(
            "Retrieve operational routing policies relevant to the current query. "
            "Use this when the query intent is unclear and you need guidance on which "
            "domain agents to invoke and with what parameters."
        ),
        args_schema=GetRelatedPoliciesArgs,
    ),
]
