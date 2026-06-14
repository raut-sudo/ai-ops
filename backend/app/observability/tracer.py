"""LangSmith tracing configuration."""

from __future__ import annotations

import os

import structlog

from app.config import settings

log = structlog.get_logger(__name__)


def configure_langsmith_tracing() -> bool:
    """Configure LangSmith tracing environment variables for the current process.

    Sets both the legacy (LANGCHAIN_TRACING_V2) and current (LANGSMITH_TRACING)
    variable names so all LangChain/LangGraph versions pick it up correctly.

    Returns True when tracing is enabled.
    """
    enabled = settings.LANGCHAIN_TRACING_V2.lower() == "true" and bool(settings.LANGSMITH_API_KEY)
    enabled_str = "true" if enabled else "false"

    os.environ["LANGCHAIN_TRACING_V2"] = enabled_str
    os.environ["LANGSMITH_TRACING"] = enabled_str

    if enabled:
        os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY
        os.environ["LANGCHAIN_API_KEY"] = settings.LANGSMITH_API_KEY  # legacy alias
        os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT
        os.environ["LANGSMITH_ENDPOINT"] = settings.LANGSMITH_ENDPOINT
        os.environ["LANGCHAIN_ENDPOINT"] = settings.LANGSMITH_ENDPOINT  # legacy alias
    else:
        for key in ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"):
            os.environ.pop(key, None)

    log.info(
        "langsmith.tracing.configured",
        enabled=enabled,
        project=settings.LANGSMITH_PROJECT if enabled else "-",
        endpoint=settings.LANGSMITH_ENDPOINT if enabled else "-",
    )
    return enabled
