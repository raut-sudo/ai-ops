"""Shared pytest fixtures for integration tests."""

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

# ── SKU-202 seed constants (deterministic IDs, safe to re-insert) ───────────
_SKU202_INCIDENT_ID = "00000000-0000-0000-0000-000000000202"
_SKU202_SUMMARY = (
    "SKU-202 stockout cascade: inventory dropped to zero triggering 28% revenue decline."
)
_SKU202_ROOT_CAUSES = [
    "SKU-202 out of stock for 3 days",
    "Restock PO delayed by supplier",
]
_SKU202_ACTIONS = [
    "Emergency restock order placed for SKU-202",
    "Safety stock threshold raised",
]
_SKU202_OUTCOME = "Stock restored within 48h; revenue recovered."


@pytest_asyncio.fixture
async def async_engine():
    """Create an async SQLAlchemy engine for testing."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncSession:
    """Create an async session for testing."""
    async_session_maker = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def ensure_seed_data():
    """Upsert the SKU-202 seed incident into Postgres (and Qdrant if available).

    Idempotent: safe to call on every test run. Uses a fixed UUID so repeated
    runs never create duplicates. Blueprint §18.6 — memory seeded in BOTH stores.

    Gracefully skips DB/Qdrant seeding when infrastructure is not running
    (e.g., local unit-test runs without Docker Compose). Tests that depend on
    a live DB will still exercise the text-fallback path in memory_retrieve.
    """
    # ── Postgres seed (best-effort) ───────────────────────────────────────────
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    try:
        async with engine.begin() as conn:
            existing = await conn.execute(
                text("SELECT id FROM incidents WHERE id = :id"),
                {"id": _SKU202_INCIDENT_ID},
            )
            row = existing.fetchone()

            if row is None:
                await conn.execute(
                    text(
                        """
                        INSERT INTO incidents
                            (id, occurred_at, summary, root_causes, actions_taken, outcome,
                             intent_type, status, created_at)
                        VALUES
                            (:id, :occurred_at, :summary, :root_causes, :actions_taken,
                             :outcome, 'business_diagnosis', 'closed', NOW())
                        ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    {
                        "id": _SKU202_INCIDENT_ID,
                        "occurred_at": datetime.now(UTC) - timedelta(days=30),
                        "summary": _SKU202_SUMMARY,
                        "root_causes": _SKU202_ROOT_CAUSES,
                        "actions_taken": _SKU202_ACTIONS,
                        "outcome": _SKU202_OUTCOME,
                    },
                )
    except Exception:
        pass  # Postgres not running — tests use text-fallback path
    finally:
        await engine.dispose()

    # ── Qdrant seed (best-effort) ─────────────────────────────────────────────
    try:
        from app.embeddings import embed_text
        from app.vector import upsert_incident_embedding

        vector = await embed_text(_SKU202_SUMMARY)
        if vector:
            await upsert_incident_embedding(
                incident_id=_SKU202_INCIDENT_ID,
                vector=vector,
                payload={"summary": _SKU202_SUMMARY},
            )
    except Exception:
        pass  # Qdrant not available — tests use text-fallback path

    yield
