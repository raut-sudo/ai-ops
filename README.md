# AI E-Commerce Operations Brain

A production-grade LangGraph multi-agent system for e-commerce operations diagnosis
with Human-in-the-Loop (HITL) action approval and long-term semantic memory.

## Architecture at a Glance

```
User Query → Intent Classifier → parallel Send() fan-out (domain agents)
          → join_findings → Synthesizer → Reflection (targeted retry)
          → Action Agent → HITL interrupt() [checkpoint in Postgres]
          → execute_actions (idempotent) → assemble_response
          → persist_incident (best-effort Qdrant + Postgres)
```

Observability: **LangSmith** (LangGraph traces) + **Langfuse** (LLM + OTel spans)

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Docker + Compose | 24+ | https://docs.docker.com/get-docker/ |
| uv | 0.10+ | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Python | 3.12+ | managed by uv |

## Quick Start

```bash
# 1. Clone and enter
git clone <repo-url> ai-ops && cd ai-ops

# 2. Configure environment
cp .env.example .env
# Fill in: POSTGRES_PASSWORD, AZURE_OPENAI_*, LANGSMITH_API_KEY,
#          LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY

# 3. Install Python dependencies + pre-commit hooks
make install

# 4. Start infrastructure
make up

# 5. Verify
curl http://localhost:8000/
```

## Development

```bash
make test          # run all tests
make lint          # ruff check
make format        # ruff format
make logs          # tail backend logs
make migrate       # apply Alembic migrations
```

## Project Structure

```
ai-ops/
├── backend/
│   ├── pyproject.toml   # deps + ruff + pytest config
│   ├── app/
│   │   ├── main.py      # FastAPI app factory
│   │   ├── config.py    # pydantic-settings (all config from .env)
│   │   ├── schemas/     # ALL Pydantic models (LLM I/O + API contracts)
│   │   ├── graph/       # LangGraph: state, edges, nodes
│   │   ├── tools/       # LangChain @tool functions + mock data
│   │   ├── memory/      # Qdrant client + embedding service
│   │   ├── db/          # SQLAlchemy models + repositories
│   │   ├── routers/     # FastAPI route handlers
│   │   ├── services/    # Business logic (graph invocation, approvals)
│   │   ├── core/        # Dependencies, exceptions, logging, security
│   │   └── observability/ # OTel tracer + LangSmith setup
│   └── tests/
├── infra/
│   ├── Dockerfile.backend
│   ├── docker-compose.yml
│   └── docker-compose.override.yml  # dev hot-reload
├── docs/
│   ├── DECISIONS.md     # Architecture decision log
│   └── PROGRESS.md      # Phase completion tracker
├── frontend/            # Next.js 15 (Phase 11)
└── Makefile
```

## Key Design Decisions

See [docs/DECISIONS.md](docs/DECISIONS.md) for the full log. Highlights:

- **HITL**: `interrupt()` + `AsyncPostgresSaver` — no external workflow engine
- **Idempotency**: checkpoint-read guard + DB status-flip (never app-flag)
- **Parallel fan-out**: `Send()` with `_worker_payload` (includes `session_id`/`user_id`, excludes reduced `domain_findings` channel)
- **`RECURSION_LIMIT=50` independent of `MAX_RETRIES=3`** — coupling them causes `GraphRecursionError`
- **`persist_incident` is best-effort** — write-back failure never fails the user response

## Observability

- **LangSmith**: every LLM call and LangGraph node visible at https://smith.langchain.com
- **Langfuse**: LLM traces + OTel HTTP/DB spans at https://cloud.langfuse.com
