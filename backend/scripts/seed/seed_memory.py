"""Seed long-term memory (Layer 3): incidents + Qdrant embeddings.

Creates 3 past incidents:
  1. SKU-202 stockout (60 days ago) - resolved by restock + discount
  2. Campaign performance drop (45 days ago)
  3. Support surge (30 days ago)

All are embedded into Qdrant for semantic retrieval.
The SKU-202 incident will be found by memory_retrieve when diagnosing SKU-101 stockout.

NOTE: This requires Qdrant to be running and Azure OpenAI embeddings configured.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Incident

logger = logging.getLogger("seed_memory")


async def seed_incidents(session: AsyncSession) -> list[tuple[str, str]]:
    """Create 3 past incidents. Return list of (id, summary) for Qdrant embedding.

    NOTE: Qdrant embedding is done separately by seed_qdrant_embeddings().
    """

    now = datetime.now(UTC)
    incidents_data = [
        {
            "id": f"incident-sku202-{(now - timedelta(days=60)).isoformat()}",
            "occurred_at": now - timedelta(days=60),
            "summary": "SKU-202 stockout crisis: emergency restock + 20% discount restored sales within 24h",
            "root_causes": [
                "Unexpected inventory movement due to supplier delay",
                "Campaign was still active on OOS SKU",
            ],
            "actions_taken": [
                "emergency-restock-sku202",
                "extend-campaign-discount",
                "notify-support-team",
            ],
            "outcome": "Restocked 300 units overnight; sales recovered within 24h; no customer complaints",
            "status": "closed",
            "embedded": False,  # Will be set to True after Qdrant upsert
        },
        {
            "id": f"incident-campaign-drop-{(now - timedelta(days=45)).isoformat()}",
            "occurred_at": now - timedelta(days=45),
            "summary": "Q2 campaign underperformed: 40% lower ROAS than baseline; paused and refocused",
            "root_causes": [
                "Targeting was too broad (all customers vs VIP)",
                "Discount was insufficient to drive conversions",
            ],
            "actions_taken": [
                "pause-campaign-q2",
                "audit-audience-segment",
                "increase-discount-to-20%",
            ],
            "outcome": "New campaign with refined targeting and higher discount achieved 1.8x ROAS",
            "status": "closed",
            "embedded": False,
        },
        {
            "id": f"incident-support-surge-{(now - timedelta(days=30)).isoformat()}",
            "occurred_at": now - timedelta(days=30),
            "summary": "Support ticket volume spiked 300%; traced to batch of defective units from Supplier A",
            "root_causes": [
                "Quality control failure at Supplier A",
                "Defects went undetected during inbound QC",
            ],
            "actions_taken": [
                "recall-batch-sa-2024-q2",
                "escalate-supplier-incident",
                "offer-replacement-expedited",
            ],
            "outcome": "All defective units replaced; Supplier A quality plan imposed; satisfaction restored",
            "status": "closed",
            "embedded": False,
        },
    ]

    stmt = insert(Incident).values(incidents_data)
    await session.execute(stmt)
    await session.commit()
    logger.info(f"Seeded {len(incidents_data)} incidents (Layer 3)")

    # Return ids and summaries for Qdrant embedding
    return [(i["id"], i["summary"]) for i in incidents_data]


async def seed_qdrant_embeddings(
    incidents: list[tuple[str, str]],
) -> None:
    """Embed incidents into Qdrant.

    This is called after seed_incidents() completes.
    Requires:
      - Qdrant service running
      - Azure OpenAI embeddings configured
      - Environment variables: OPENAI_API_KEY, OPENAI_API_BASE, etc.

    NOTE: For MVP, this is stubbed. Post-MVP, integrate with Azure OpenAI.
    """
    logger.info(f"Would embed {len(incidents)} incidents to Qdrant (not yet implemented)")
    # TODO: Implement Qdrant upsert once embeddings pipeline is finalized
