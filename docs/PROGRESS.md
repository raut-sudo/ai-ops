# PROGRESS.md — Build Status

Track phase completion here. See DECISIONS.md for *why* choices were made.

---

## Phase Checklist

| Phase | Status | Branch | Notes |
|-------|--------|--------|-------|
| **0** — Scaffold (uv, ruff, pre-commit, Docker, .env, skeleton) | ✅ Done | `feat/project-scaffold` | |
| **1** — FastAPI skeleton + `/health` in Docker | 🔲 Not started | | |
| **2** — DB layer (Alembic, SQLAlchemy models, TIMESTAMPTZ) | 🔲 Not started | | |
| **3** — Pydantic schemas + AgentState + reducers | 🔲 Not started | | |
| **4** — Mock tools (sales/inventory/marketing/support/actions) | 🔲 Not started | | |
| **5** — Intent Classifier + merged routing edge + `_worker_payload` | 🔲 Not started | | |
| **6** — Domain agents + join_findings + Synthesizer + Reflection | 🔲 Not started | | |
| **7** — AsyncPostgresSaver + HITL + idempotent execute_actions | 🔲 Not started | | |
| **8** — Qdrant Memory (retrieval + persist_incident best-effort) | 🔲 Not started | | |
| **9** — Streaming `/chat` API + `/approve` (checkpoint idempotency) | 🔲 Not started | | |
| **10** — Observability wiring (LangSmith + Langfuse + OTel) | 🔲 Not started | | |
| **11** — Frontend (Next.js 15, useChat HITL branching) | 🔲 Not started | | |
| **12** — Evaluation (DeepEval) + polish + README | 🔲 Not started | | |

---

## Phase 0 — Completed Items

- [x] `feat/project-scaffold` branch created
- [x] `.gitignore` + `.dockerignore`
- [x] `backend/pyproject.toml` — all deps with version pins; ruff + pytest config
- [x] `.pre-commit-config.yaml` — ruff lint/format, file hygiene, conventional commits
- [x] Full folder skeleton (`backend/app/**`, `backend/tests/**`, `infra/`, `docs/`, `frontend/`)
- [x] `backend/app/main.py` — minimal FastAPI placeholder
- [x] `backend/app/config.py` — pydantic-settings with all required env vars
- [x] `infra/Dockerfile.backend` — multi-stage (uv builder → slim runtime, non-root user)
- [x] `infra/docker-compose.yml` — postgres + qdrant + backend
- [x] `infra/docker-compose.override.yml` — dev hot-reload volume mounts
- [x] `.env.example` — all keys documented
- [x] `docs/DECISIONS.md` — Phase 0 decisions logged
- [x] `Makefile` — standard targets
- [x] `README.md` — initial setup guide
- [x] `uv sync` — lockfile generated
- [x] `pre-commit install` — hooks installed
