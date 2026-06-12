from __future__ import annotations

from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct

from app.config import settings


def _client() -> AsyncQdrantClient:
    return AsyncQdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)


async def qdrant_search(
    vector: list[float], top_k: int = 5, score_threshold: float | None = None
) -> list[Any]:
    """Search similar incidents in Qdrant and return scored hits."""
    client = _client()
    return await client.search(
        collection_name=settings.QDRANT_COLLECTION,
        query_vector=vector,
        limit=top_k,
        score_threshold=score_threshold,
    )


async def qdrant_upsert(point_id: str, vector: list[float], payload: dict[str, Any]) -> None:
    """Upsert an incident embedding payload into Qdrant."""
    client = _client()
    await client.upsert(
        collection_name=settings.QDRANT_COLLECTION,
        points=[PointStruct(id=point_id, vector=vector, payload=payload)],
    )
