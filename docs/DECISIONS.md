# DECISIONS.md — Architecture Decision Log

Every design choice that has a real tradeoff is recorded here.
Format: `| Date | Decision | Rationale |`

---

## Phase 0 — Project Scaffold (2026-06-11)

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-06-11 | **Observability: Langfuse Cloud + LangSmith Cloud** (no Aspire Dashboard, no self-hosted Langfuse) | BLUEPRINT specifies LangSmith + Aspire Dashboard. User overrides to LangSmith + Langfuse. Langfuse v3 self-hosted requires ClickHouse + MinIO + Redis + worker + web (5 extra compose services) — excessive for a portfolio project. Langfuse Cloud free tier provides identical LangChain/OTel SDK functionality. Aspire Dashboard dropped in favour of Langfuse's built-in OTel ingestion. |
| 2026-06-11 | **Ruff rule set: `["E", "F", "I", "UP", "B", "ASYNC", "RUF"]`** (standard) | Catches bugs (F), import order (I), modern Python (UP), async anti-patterns (ASYNC), and ruff-specific rules (RUF). Avoids `ANN` (annotation noise during early development) and `ARG` (unused args). |
| 2026-06-11 | **No mypy in pre-commit** | mypy adds 15–30 s per commit during iterative development. Run manually or in CI. Pydantic v2 + ruff catch the vast majority of type errors at dev time. |
| 2026-06-11 | **Version pinning strategy: `==` for HITL-critical packages, `>=` for others** | `langgraph==1.2.4`, `langchain==1.3.7`, `langchain-openai==1.3.0`, `langgraph-checkpoint-postgres==3.1.0` pinned exactly. The `interrupt()` / `Command(resume=)` / `aget_state()` HITL APIs are version-sensitive (BLUEPRINT §10). Other packages pinned via `uv.lock`. |
| 2026-06-11 | **Flat layout (`backend/app/`)** | Matches BLUEPRINT §13 exactly. Simpler for uv + Docker COPY. |
| 2026-06-11 | **Docker base image: `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`** (builder stage) | Official uv image; uv pre-installed on PATH; simplest multi-stage setup. Runtime stage uses `python:3.12-slim-bookworm` (no uv needed at runtime). |
| 2026-06-11 | **Phase 0 compose: `postgres + qdrant + backend` only** | Langfuse is cloud-hosted (see row 1). LangSmith is cloud-hosted. No self-hosted observability containers needed in Phase 0. |
| 2026-06-11 | **Git branching: `main` + `dev` + feature branches** | Feature branches merge into `dev`; `dev` merges into `main` at phase milestones. |
| 2026-06-11 | **`RECURSION_LIMIT=50` INDEPENDENT of `MAX_RETRIES=3`** | Coupling these values guarantees `GraphRecursionError` (BLUEPRINT §5 S-NEW-6). `recursion_limit` counts LangGraph super-steps (~15–20 per retry cycle); `MAX_RETRIES` bounds reflection retries. They are orthogonal concerns. |

## Pinned Versions (2026-06-11)

| Package | Version | Note |
|---------|---------|------|
| langgraph | 1.2.4 | HITL-critical |
| langchain | 1.3.7 | HITL-critical |
| langchain-openai | 1.3.0 | HITL-critical |
| langgraph-checkpoint-postgres | 3.1.0 | HITL-critical; import: `from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver` — verify against this version |
| langfuse (SDK) | 4.7.1 | LLM tracing SDK |
| fastapi | 0.136.3 | |
| pydantic-settings | 2.14.1 | |
| qdrant-client | 1.18.0 | |
| opentelemetry-sdk | 1.42.1 | |
| asyncpg | 0.31.0 | |
| tenacity | 9.1.4 | |
| structlog | 26.1.0 | |
| alembic | 1.18.4 | |
| sqlalchemy | 2.0.50 | |
| pytest-asyncio | 1.4.0 | |
| deepeval | 4.0.6 | |

## BLUEPRINT Deviations

| # | BLUEPRINT Spec | Actual Choice | Reason |
|---|---------------|---------------|--------|
| 1 | Aspire Dashboard (OTel/OTLP) | Langfuse Cloud (OTel + LLM tracing) | User override; Aspire is .NET infra-only, Langfuse covers same use case + LLM traces |
