# Repository Guidelines

This repository contains the design, spike experiments, and backend implementation for **yd-memory-service**, a multi-agent external memory platform backed by PostgreSQL with Chinese full-text search. It integrates with Dify via MCP, with DSH/other agents and business systems via REST (per-space API Key). The authoritative design is `docs/yd-memory-service/01-design.md` (**v3.1**); its §实现现状与差距清单 tracks defects — V1 (Week 1-3) is implemented and verified, V1.5 (codebase distillation) is the next phase.

## Project Structure & Module Organization

- `docs/yd-memory-service/` — authoritative design and review documents. Read `README.md` first, then `01-design.md` and `05-solutions.md` before changing architecture.
- `yd-memory-service/backend/` — FastAPI backend and Python package.
  - `src/yd_memory_service/api/` — REST endpoints (`spaces`, `learning`, `observations`, `recall`, `memories`) + `deps.py` (per-space API Key auth). All `/api/v1/*` routes require `Authorization: Bearer <space_key>`; identity is resolved from the key, never from the body.
  - `src/yd_memory_service/core/` — models, database, `MemoryManager`, learning, wiki, and long-term memory.
  - `src/yd_memory_service/orchestrator/` — recall and ranking logic.
  - `src/yd_memory_service/mcp/` — MCP SSE server and tools (`recall`, `load_memory`, `memorize`).
  - `alembic/` — database migrations.
- `yd-memory-service/frontend/` — planned Vue frontend; currently empty.
- `spike/` — disposable Dify/MCP verification scripts, not V1 code.
- `yd-memory-service/docker-compose.yml` — local PostgreSQL service.

## Build, Test, and Development Commands

Run these from the noted directories.

```bash
cd yd-memory-service/backend && uv sync
docker compose -f yd-memory-service/docker-compose.yml up -d
cd yd-memory-service/backend && uv run alembic upgrade head
cd yd-memory-service/backend && uv run yd-memory
cd yd-memory-service/backend && uv run uvicorn yd_memory_service.main:app --reload
cd yd-memory-service/backend && uv run pytest
```

`uv sync` installs dependencies, `alembic upgrade head` applies migrations, `yd-memory` starts the FastAPI app, and the health endpoint is `/health`.

## Coding Style & Naming Conventions

- Backend requires Python 3.12+; spike scripts require 3.13+. Use `uv` for dependency management.
- Follow PEP 8 with 4-space indentation and type hints (`from __future__ import annotations`).
- Use `snake_case` for functions, variables, and modules; `PascalCase` for models and routers.
- Configuration reads `YDM_`-prefixed environment variables from `.env`; never commit secrets.
- Documentation is written in Simplified Chinese with English identifiers for code, tables, and APIs.

## Testing Guidelines

Use `pytest` with `asyncio_mode = "auto"` (already configured in `backend/pyproject.toml`). Add tests under `backend/tests/` with names beginning `test_`. No coverage threshold is configured yet.

## Commit & Pull Request Guidelines

This tree has no Git metadata yet. When version control is initialized, use Conventional Commits with a module scope, for example `feat(backend): add recall reranking` or `docs(design): update roadmap`. Pull requests should explain the change, link the relevant design or issue, call out migrations and environment-variable changes, and include screenshots for UI work.
