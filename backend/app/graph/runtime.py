from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

import structlog

from app.config import settings

log = structlog.get_logger(__name__)

_exit_stack: AsyncExitStack | None = None
_checkpointer = None
_compiled_graph: Any | None = None


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

    # Register app schema types so LangGraph checkpoint serde doesn't warn
    # about "unregistered type" on every deserialization (§30.2 / LG 1.2.4).
    try:
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

        _ALLOWED = [
            "app.schemas",
            "app.graph.state",
        ]
        serde = _checkpointer.serde
        if isinstance(serde, JsonPlusSerializer):
            for mod in _ALLOWED:
                if mod not in (getattr(serde, "allowed_msgpack_modules", None) or []):
                    serde.allowed_msgpack_modules = [
                        *getattr(serde, "allowed_msgpack_modules", []),
                        mod,
                    ]
    except Exception:
        pass  # Best-effort; serde API varies across LG versions

    log.info("graph.checkpointer.ready")
    return _checkpointer


async def shutdown_checkpointer() -> None:
    """Close saver connection context created at startup."""
    global _exit_stack, _checkpointer, _compiled_graph

    if _exit_stack is not None:
        await _exit_stack.aclose()
    _exit_stack = None
    _checkpointer = None
    _compiled_graph = None
    log.info("graph.checkpointer.shutdown")


def get_checkpointer() -> object:
    """Return initialized checkpointer singleton."""
    if _checkpointer is None:
        raise RuntimeError("Checkpointer is not initialized")
    return _checkpointer


async def compile_graph_singleton() -> Any:
    """Compile the graph once and cache it.  Called from lifespan after setup_checkpointer.

    If the checkpointer failed to initialize (e.g., no DB in test env), compilation
    is skipped and _compiled_graph stays None.  Endpoints that call get_compiled_graph()
    will raise RuntimeError — expected behavior in that case.
    """
    global _compiled_graph

    if _compiled_graph is not None:
        return _compiled_graph

    if _checkpointer is None:
        log.warning("graph.compile_skipped.no_checkpointer")
        return None

    try:
        from app.graph.graph import compile_graph

        _compiled_graph = await compile_graph(_checkpointer)
        log.info("graph.compiled")
    except Exception as exc:
        log.warning("graph.compile_failed", error=str(exc))

    return _compiled_graph


def get_compiled_graph() -> Any:
    """Return the compiled graph singleton.  Raises RuntimeError if not yet compiled."""
    if _compiled_graph is None:
        raise RuntimeError(
            "Compiled graph is not initialized — call compile_graph_singleton() first"
        )
    return _compiled_graph
