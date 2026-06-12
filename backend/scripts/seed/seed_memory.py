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
    """Embed incidents into Qdrant using Azure OpenAI text-embedding-3-small.

    Creates the collection if it does not exist, then upserts one point per
    incident. Uses deterministic integer IDs (hash of incident string ID) so
    re-seeding is idempotent.
    """
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    from app.config import settings
    from app.embeddings import embed_text

    client = AsyncQdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)

    # Ensure collection exists (idempotent)
    existing = [c.name for c in (await client.get_collections()).collections]
    if settings.QDRANT_COLLECTION not in existing:
        await client.create_collection(
            collection_name=settings.QDRANT_COLLECTION,
            vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
        )
        logger.info(f"Created Qdrant collection '{settings.QDRANT_COLLECTION}'")

    points: list[PointStruct] = []
    for incident_id, summary in incidents:
        vector = await embed_text(summary)
        # Deterministic numeric ID from incident string id
        numeric_id = abs(hash(incident_id)) % (2**53)
        points.append(
            PointStruct(
                id=numeric_id,
                vector=vector,
                payload={"incident_id": incident_id, "summary": summary},
            )
        )
        logger.info(f"  Embedded: {incident_id[:60]}…")

    await client.upsert(collection_name=settings.QDRANT_COLLECTION, points=points)
    count = (await client.count(settings.QDRANT_COLLECTION)).count
    logger.info(f"Qdrant '{settings.QDRANT_COLLECTION}' now has {count} points")
