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


def setup_tracing(app: FastAPI) -> TracerProvider:
    """Initialise TracerProvider and instrument FastAPI.

    Called inside the app lifespan so a fresh provider is created each
    startup and cleanly shut down on teardown.

    FastAPIInstrumentor.instrument_app() is idempotent on repeated calls
    (it checks internally whether already instrumented).
    """
    global _provider

    resource = Resource.create({"service.name": settings.OTEL_SERVICE_NAME})
    exporter = OTLPSpanExporter(
        # Phase 10: this endpoint will be updated to
        # f"{settings.LANGFUSE_HOST}/api/public/otel/v1/traces" with Basic auth.
        endpoint=f"{settings.OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces",
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _provider = provider

    FastAPIInstrumentor.instrument_app(app)
    # Phase 1 stub: instrument without an engine — SQLAlchemy adds the hook
    # eagerly; Phase 2 will pass the real engine when it creates it.
    SQLAlchemyInstrumentor().instrument()

    log.info(
        "otel.tracing.configured",
        service=settings.OTEL_SERVICE_NAME,
        otlp_endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
    )
    return provider


def shutdown_tracing() -> None:
    """Flush and shut down the BatchSpanProcessor. Called in lifespan teardown."""
    if _provider is not None:
        _provider.shutdown()
        log.info("otel.tracing.shutdown")


def get_tracer(name: str) -> trace.Tracer:
    """Return a named tracer from the current global provider."""
    return trace.get_tracer(name)
