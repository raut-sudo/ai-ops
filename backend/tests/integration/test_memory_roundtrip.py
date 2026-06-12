from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.config import settings
from app.graph.nodes.memory_retrieve import memory_retrieve_node

pytestmark = pytest.mark.usefixtures("ensure_seed_data")


async def _db_is_reachable() -> bool:
    """Return True only if Postgres is accepting connections."""
    try:
        import asyncpg

        conn = await asyncpg.connect(settings.DATABASE_URL.replace("+asyncpg", ""), timeout=2)
        await conn.close()
        return True
    except Exception:
        return False


@pytest.mark.asyncio
async def test_memory_roundtrip_retrieves_seeded_sku202_incident() -> None:
    if not await _db_is_reachable():
        pytest.skip("Postgres not running — memory roundtrip requires a live DB")

    state = {
        "query": "We are seeing a stockout incident similar to SKU-202 from before.",
        "session_id": "mem-session-001",
        "thread_id": "mem-thread-001",
        "user_id": "mem-user",
        "created_at": datetime.now(UTC),
    }

    out = await memory_retrieve_node(state)
    memory_context = out["memory_context"]

    assert memory_context is not None
    assert len(memory_context.past_incidents) >= 1
    assert any(
        "sku-202" in p.summary.lower() or "sku-202" in p.incident_id.lower()
        for p in memory_context.past_incidents
    )
    assert len(memory_context.recommended_actions_from_history) >= 1
