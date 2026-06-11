"""AI E-Commerce Operations Brain — FastAPI application entry point.

Phase 0 placeholder: bare app factory. Real lifespan, middleware,
and routers are wired in Phase 1.
"""

from fastapi import FastAPI

app = FastAPI(
    title="AI E-Commerce Operations Brain",
    version="0.1.0",
    description="LangGraph multi-agent e-commerce operations diagnosis system.",
)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "ai-ops-backend", "status": "starting"}
