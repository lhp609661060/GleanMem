# Repository Guidelines

This repository contains the design, spike experiments, and backend implementation for **yd-memory-service**, a multi-agent external memory platform backed by PostgreSQL with Chinese full-text search. It integrates with Dify via MCP, with DSH/other agents and business systems via REST (per-space API Key). The authoritative design is `docs/yd-memory-service/01-design.md` (**v3.4**); its §实现现状与差距清单 tracks defects — V1, V1.5a (batch distillation), V1.5b (event incremental) and N6 (review_status recall filtering) are implemented and verified, closing every 🔴/🟡. V2 items (pull producer, scheduler, pgvector, admin UI) are the remaining backlog.

## Project Structure & Module Organization

- `docs/yd-memory-service/` — authoritative design and review documents. Read `README.md` first, then `01-design.md` and `05-solutions.md` before changing architecture.
- `yd-memory-service/backend/` — FastAPI backend and Python package.
  - `src/yd_memory_service/api/` — REST endpoints (`spaces`, `learning`, `observations`, `recall`, `memories`, `codebase`) + `deps.py` (per-space API Key auth). All `/api/v1/*` routes require `Authorization: Bearer <space_key>`; identity is resolved from the key, never from the body.
  - `src/yd_memory_service/core/` — models, database, `MemoryManager`, learning, wiki, long-term memory, and `codebase/` (V1.5a distillation: scanner / analyzer / store / md projection).
  - `src/yd_memory_service/orchestrator/` — recall and ranking logic.
  - `src/yd_memory_service/mcp/` — MCP SSE server and tools (`recall`, `load_memory`, `memorize`).
  - `src/yd_memory_service/cli/` — `ydm-distill` client CLI (`scan` dry-run / `run` distill+upload / `sync` md projection). Reads the repo locally; the server never touches a working tree (design D11).
  - `alembic/` — database migrations.
- `yd-memory-service/frontend/` — Vue 3 + Vite admin UI (5 pages: memories/review, learning logs, codebase cards, distillation audit, recall preview). Read-only plus the N6 review action; `npm install && npm run dev` proxies `/api` to port 8000. See `frontend/README.md`. `space_key` lives in sessionStorage only.
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
cd yd-memory-service/backend && uv run ydm-distill scan <repo>   # codebase 蒸馏 dry-run（不调 LLM）
```

`uv sync` installs dependencies, `alembic upgrade head` applies migrations, `yd-memory` starts the FastAPI app, and the health endpoint is `/health`. `ydm-distill` is the codebase-distillation client (`scan` reports file counts + token estimate before spending anything; `run` distills and uploads cards; `sync` rebuilds the md projection; `refresh` pushes git diffs into the inbox for incremental regeneration). End-to-end demos, none of which need an LLM key: `scripts/e2e_demo.py` (V1), `scripts/e2e_codebase_demo.py` (V1.5a batch), `scripts/e2e_incremental_demo.py` (V1.5b incremental).

## Coding Style & Naming Conventions

- Backend requires Python 3.12+; spike scripts require 3.13+. Use `uv` for dependency management.
- Follow PEP 8 with 4-space indentation and type hints (`from __future__ import annotations`).
- Use `snake_case` for functions, variables, and modules; `PascalCase` for models and routers.
- Configuration reads `YDM_`-prefixed environment variables from `.env`; never commit secrets.
- Documentation is written in Simplified Chinese with English identifiers for code, tables, and APIs.

## Testing Guidelines

Use `pytest` with `asyncio_mode = "auto"` (already configured in `backend/pyproject.toml`). Add tests under `backend/tests/` with names beginning `test_`. No coverage threshold is configured yet.

## Commit & Pull Request Guidelines

Git is initialized on branch `main` (initial commit: chore, 2026-08). Use Conventional Commits with a module scope, for example `feat(backend): add recall reranking`, `fix(core): keep pending events on empty LLM decisions`, or `docs(design): update roadmap`. Common scopes: `backend` / `core` / `mcp` / `docker` / `docs` / `spike` / `chore`.

Rules:
- One logical change per commit; run `uv run pytest` in `backend/` before committing backend changes.
- Never commit secrets: `space_key` exists only in the database; `.env` is gitignored.
- `.venv/`, `__pycache__/`, `.DS_Store`, `*.egg-info` are gitignored — verify with `git status` before committing.
- Tag milestones (`git tag v0.1.0` etc.) at each phase boundary (V1 done → v0.1.0, V1.5 → v0.2.0).
- Pull requests should explain the change, link the relevant design or issue, call out migrations and environment-variable changes, and include screenshots for UI work.
