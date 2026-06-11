from __future__ import annotations

from contextlib import AsyncExitStack

import structlog

from app.config import settings

log = structlog.get_logger(__name__)

_exit_stack: AsyncExitStack | None = None
_checkpointer = None


def _checkpoint_conn_string() -> str:
    """Return a psycopg-compatible connection string for LangGraph saver."""
    conn = settings.DATABASE_URL_SYNC
    return conn.replace("postgresql+psycopg://", "postgresql://", 1)


async def setup_checkpointer() -> object | None:
    """Initialize AsyncPostgresSaver and run idempotent .setup()."""
    global _exit_stack, _checkpointer

    if _checkpointer is not None:
        return _checkpointer

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except Exception as exc:
        log.warning("graph.checkpointer.import_failed", error=str(exc))
        return None

    _exit_stack = AsyncExitStack()
    saver_cm = AsyncPostgresSaver.from_conn_string(_checkpoint_conn_string())
    _checkpointer = await _exit_stack.enter_async_context(saver_cm)
    await _checkpointer.setup()
    log.info("graph.checkpointer.ready")
    return _checkpointer


async def shutdown_checkpointer() -> None:
    """Close saver connection context created at startup."""
    global _exit_stack, _checkpointer

    if _exit_stack is not None:
        await _exit_stack.aclose()
    _exit_stack = None
    _checkpointer = None
    log.info("graph.checkpointer.shutdown")


def get_checkpointer() -> object:
    """Return initialized checkpointer singleton."""
    if _checkpointer is None:
        raise RuntimeError("Checkpointer is not initialized")
    return _checkpointer
