"""Structured logging configuration using structlog.

Call configure_logging() exactly once, inside create_app(), before
any other code emits log lines. This bridges structlog into stdlib
so uvicorn/sqlalchemy/httpx logs also pass through the same renderer.
"""

from __future__ import annotations

import logging
import logging.config
from typing import Any

import structlog


def configure_logging(log_level: str = "INFO", app_env: str = "development") -> None:
    """Configure structlog + stdlib logging bridge.

    - Production: JSONRenderer (machine-parseable)
    - Development: ConsoleRenderer with colours
    """
    level_int: int = getattr(logging, log_level.upper(), logging.INFO)

    # Processors shared by structlog and the stdlib bridge formatter.
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if app_env == "production"
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(level_int),
        cache_logger_on_first_use=True,
    )

    # Wire structlog renderer into the stdlib logging system so that uvicorn,
    # sqlalchemy, httpx and any other library that calls logging.getLogger()
    # routes through the same renderer without a duplicate basicConfig call.
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "structlog": {
                    "()": structlog.stdlib.ProcessorFormatter,
                    "processor": renderer,
                    "foreign_pre_chain": shared_processors,
                }
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "structlog",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {"handlers": ["default"], "level": level_int},
            # Let uvicorn propagate to root so its records pass through structlog.
            "loggers": {
                "uvicorn": {"propagate": True, "handlers": [], "level": "INFO"},
                "uvicorn.error": {"propagate": True, "handlers": []},
                "uvicorn.access": {"propagate": True, "handlers": []},
                "fastapi": {"propagate": True, "handlers": []},
                "sqlalchemy.engine": {
                    "propagate": True,
                    "handlers": [],
                    "level": "WARNING",
                },
            },
        }
    )
