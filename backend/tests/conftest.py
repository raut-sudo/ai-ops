from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.db.models import Base
from scripts.seed.run_seed import main as run_seed_main


@pytest_asyncio.fixture(scope="session")
async def ensure_seed_data() -> None:
    """Ensure deterministic Phase 2 seed data exists before Phase 3 tests run."""
    engine = create_async_engine(settings.DATABASE_URL)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        await run_seed_main()
    finally:
        await engine.dispose()
