"""Application configuration.

All settings are read from environment variables (or .env file via
pydantic-settings). No hardcoded secrets — ever.

BLUEPRINT §18 (Day 1) requirement:
  MAX_RETRIES = 3
  RECURSION_LIMIT = 50   — INDEPENDENT values, never derived from each other.
  Coupling RECURSION_LIMIT to MAX_RETRIES guarantees GraphRecursionError (S-NEW-6).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # ── Azure OpenAI ───────────────────────────────────────────────────────────
    AZURE_OPENAI_API_KEY: str = ""
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_API_VERSION: str = "2024-12-01-preview"
    AZURE_OPENAI_DEPLOYMENT_GPT4O: str = "gpt-4o"
    AZURE_OPENAI_DEPLOYMENT_GPT4O_MINI: str = "gpt-4o-mini"
    AZURE_OPENAI_DEPLOYMENT_EMBEDDING: str = "text-embedding-3-small"

    # ── Database ───────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://ecomops:ecomops@localhost:5432/ecom_brain"
    # Sync URL used only by Alembic migrations (not the async app)
    DATABASE_URL_SYNC: str = "postgresql://ecomops:ecomops@localhost:5432/ecom_brain"

    # ── Qdrant ─────────────────────────────────────────────────────────────────
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION: str = "incidents"

    # ── LangGraph graph settings (BLUEPRINT §18 — INDEPENDENT, do not derive) ──
    MAX_RETRIES: int = 3  # reflection retry cap
    RECURSION_LIMIT: int = 50  # LangGraph super-step guard — NOT 3*MAX_RETRIES

    # ── Memory retrieval thresholds (BLUEPRINT §8) ─────────────────────────────
    MEMORY_SIM_THRESHOLD: float = 0.72  # cosine similarity gate
    SIM_WEIGHT: float = 0.6  # rerank weight for similarity
    RECENCY_WEIGHT: float = 0.4  # rerank weight for recency

    # ── HITL ───────────────────────────────────────────────────────────────────
    HITL_TIMEOUT_HOURS: int = 24

    # ── Observability ──────────────────────────────────────────────────────────
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "ai-ops-brain"
    LANGCHAIN_TRACING_V2: str = "true"

    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4318"
    OTEL_SERVICE_NAME: str = "ai-ops-backend"

    # ── Security ───────────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60


settings = Settings()
