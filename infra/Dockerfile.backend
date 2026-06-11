# ── Stage 1: dependency resolver ─────────────────────────────────────────────
# Uses the official uv image which has uv pre-installed on PATH.
# Decision: ghcr.io/astral-sh/uv:python3.12-bookworm-slim (Q6-A).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

# Install system deps required by some Python packages (asyncpg → libpq)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy only the dependency manifest first — leverages Docker layer cache.
# If pyproject.toml/uv.lock don't change, this layer is reused on every build.
COPY backend/pyproject.toml backend/uv.lock* ./

# Sync runtime deps only (no dev group). --frozen ensures uv.lock is the source
# of truth; the build fails if pyproject.toml and uv.lock are out of sync.
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini* ./

# Install the project itself into the venv
RUN uv sync --frozen --no-dev

# ── Stage 2: lean runtime image ───────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

# Runtime system deps only (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy the resolved venv from builder — no pip, no uv needed at runtime
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY --from=builder /app/app /app/app
COPY --from=builder /app/alembic /app/alembic
COPY --from=builder /app/alembic.ini* /app/

# Activate venv by prepending to PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Non-root user for security (OWASP: run as least-privilege)
RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid appuser --no-create-home appuser
USER appuser

EXPOSE 8000

# Default: Alembic migrations first, then uvicorn.
# Overridable per-environment (e.g., docker-compose command: override for dev).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
