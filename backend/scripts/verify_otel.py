from __future__ import annotations

import time

from opentelemetry import trace

from app.observability import get_tracer, init_otel


def main() -> None:
    init_otel()
    tracer = get_tracer("otel_verification")

    with tracer.start_as_current_span("verify_otel_smoke") as span:
        span.set_attribute("verification", True)

    trace.get_tracer_provider().force_flush()
    time.sleep(2)
    print(
        "Span emitted. Confirm it appears in Langfuse Cloud UI -> Traces -> "
        "'verify_otel_smoke'. If absent, verify endpoint and keys."
    )


if __name__ == "__main__":
    main()
