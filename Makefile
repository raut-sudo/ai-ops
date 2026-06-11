# AI E-Commerce Operations Brain
# Run all commands from the repo root unless noted.

.PHONY: up down restart logs migrate seed test eval lint format install

# ── Docker ────────────────────────────────────────────────────────────────────
up:
	docker compose -f infra/docker-compose.yml up -d

down:
	docker compose -f infra/docker-compose.yml down

restart:
	docker compose -f infra/docker-compose.yml restart backend

logs:
	docker compose -f infra/docker-compose.yml logs -f backend

# ── Database (run inside container or with uv run locally) ────────────────────
migrate:
	cd backend && uv run alembic upgrade head

migrate-down:
	cd backend && uv run alembic downgrade -1

seed:
	cd backend && uv run python scripts/seed_mock_data.py

# ── Testing ───────────────────────────────────────────────────────────────────
test:
	cd backend && uv run pytest tests/ -v

test-unit:
	cd backend && uv run pytest tests/unit/ -v

test-integration:
	cd backend && uv run pytest tests/integration/ -v

eval:
	cd backend && uv run pytest tests/evaluation/ -v

# ── Code quality ──────────────────────────────────────────────────────────────
lint:
	cd backend && uv run ruff check .

format:
	cd backend && uv run ruff format .

lint-fix:
	cd backend && uv run ruff check --fix .

# ── Local setup ───────────────────────────────────────────────────────────────
install:
	cd backend && uv sync --group dev
	cd backend && uv run pre-commit install
