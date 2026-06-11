"""OpenTelemetry tracing setup — Phase 1 stub.

Phase 1:  TracerProvider wired; FastAPI auto-instrumented; SQLAlchemy stub
          (engine is None until Phase 2 creates the async engine).
Phase 2:  Pass the real async engine to SQLAlchemyInstrumentor.
Phase 10: Full Langfuse OTLP wiring per BLUEPRINT §14 (R3.1).

BLUEPRINT §14 (R3.1): OTel spans are exported to Langfuse's OTLP endpoint.
The exporter target here is intentionally a generic OTLP/HTTP URL that will
point at Langfuse in Phase 10 via OTEL_EXPORTER_OTLP_ENDPOINT env var.
"""

from __future__ import annotations

import base64

import structlog
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import settings

log = structlog.get_logger(__name__)

# Module-level reference so lifespan can call provider.shutdown() cleanly.
_provider: TracerProvider | None = None


def _langfuse_auth_header() -> str | None:
    """Return Basic auth header value expected by Langfuse OTLP endpoint."""
    if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
        return None
    token = base64.b64encode(
        f"{settings.LANGFUSE_PUBLIC_KEY}:{settings.LANGFUSE_SECRET_KEY}".encode()
    ).decode("utf-8")
    return f"Basic {token}"


def init_otel() -> TracerProvider:
    """Initialise and register the global OTel tracer provider."""
    global _provider

    resource = Resource.create({"service.name": settings.OTEL_SERVICE_NAME})

    headers: dict[str, str] = {}
    auth = _langfuse_auth_header()
    if auth:
        headers["Authorization"] = auth

    exporter = OTLPSpanExporter(
        endpoint=settings.LANGFUSE_OTEL_ENDPOINT,
        headers=headers,
    )

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _provider = provider

    log.info(
        "otel.tracing.configured",
        service=settings.OTEL_SERVICE_NAME,
        otlp_endpoint=settings.LANGFUSE_OTEL_ENDPOINT,
        auth_header_set=bool(auth),
    )
    return provider


def setup_tracing(app: FastAPI) -> TracerProvider:
    """Initialise TracerProvider and instrument FastAPI.

    Called inside the app lifespan so a fresh provider is created each
    startup and cleanly shut down on teardown.

    FastAPIInstrumentor.instrument_app() is idempotent on repeated calls
    (it checks internally whether already instrumented).
    """
    provider = init_otel()

    FastAPIInstrumentor.instrument_app(app)
    # Phase 1 stub: instrument without an engine — SQLAlchemy adds the hook
    # eagerly; Phase 2 will pass the real engine when it creates it.
    SQLAlchemyInstrumentor().instrument()

    return provider


def shutdown_tracing() -> None:
    """Flush and shut down the BatchSpanProcessor. Called in lifespan teardown."""
    if _provider is not None:
        _provider.shutdown()
        log.info("otel.tracing.shutdown")


def get_tracer(name: str) -> trace.Tracer:
    """Return a named tracer from the current global provider."""
    return trace.get_tracer(name)
