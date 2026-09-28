# Repository Guidelines

This repository contains the design, spike experiments, and backend implementation for **gleanmem**, a multi-agent external memory platform backed by PostgreSQL with Chinese full-text search. It integrates with Dify via MCP, with DSH/other agents and business systems via REST (per-space API Key). The authoritative design is `docs/gleanmem/01-design.md` (**v3.9**); its §实现现状与差距清单 tracks defects — every 🔴/🟡/🟢 is closed (V1, V1.5a batch distillation, V1.5b event incremental, N6 review_status recall filtering, the seed.py/Alembic dual-track removal, and the v3.9 hardening round D16–D20: MCP SSE requires `X-Space-Key`, `POST /spaces` is admin-only, flush runs in two phases with a batch cap, exhausted events are dead-lettered instead of deleted). One 🟢 residue is recorded there: the `source=codebase` branch still calls the LLM inside the lock. Of the V2 backlog, the built-in scheduler and the admin UI are done and pgvector was ruled out by a re-run P0-3 benchmark (100% Top-3); only the pull producer and remaining example-learning induction are left.

## Project Structure & Module Organization

- `docs/gleanmem/` — authoritative design and review documents. Read `README.md` first, then `01-design.md` and `05-solutions.md` before changing architecture.
- `gleanmem/backend/` — FastAPI backend and Python package.
  - `src/gleanmem/api/` — REST endpoints (`spaces`, `learning`, `observations`, `recall`, `memories`, `codebase`) + `deps.py` (per-space API Key auth). All `/api/v1/*` routes require `Authorization: Bearer <space_key>`; identity is resolved from the key, never from the body.
  - `src/gleanmem/core/` — models, database, `MemoryManager`, learning, wiki, long-term memory, and `codebase/` (V1.5a distillation: scanner / analyzer / store / md projection).
  - `src/gleanmem/orchestrator/` — recall/ranking logic, plus `scheduler.py` (V2 built-in per-Space cron: APScheduler ticks every 20s, croniter decides due-ness against `agent_spaces.config.schedule`, and a single atomic `last_fired_slot` UPDATE elects the executor across replicas). `YDM_SCHEDULER_ENABLED=false` turns it off.
  - `src/gleanmem/mcp/` — MCP SSE server and tools (`recall`, `load_memory`, `memorize`).
  - `src/gleanmem/cli/` — `gleanmem-distill` client CLI (`scan` dry-run / `run` distill+upload / `sync` md projection). Reads the repo locally; the server never touches a working tree (design D11).
  - `alembic/` — database migrations.
- `gleanmem/frontend/` — Vue 3 + Vite admin UI (business views are read-only plus the N6 review action; the admin-only 空间管理 page creates/edits/archives Spaces and configures their V2 cron `schedule`). `npm install && npm run dev` proxies `/api` to port 8000. See `frontend/README.md`. `space_key` lives in sessionStorage only.
- `spike/` — disposable Dify/MCP verification scripts, not V1 code.
- `gleanmem/docker-compose.yml` — local PostgreSQL service.

## Build, Test, and Development Commands

Run these from the noted directories.

```bash
cd gleanmem/backend && uv sync
docker compose -f gleanmem/docker-compose.yml up -d
cd gleanmem/backend && uv run alembic upgrade head
cd gleanmem/backend && uv run gleanmem
cd gleanmem/backend && uv run uvicorn gleanmem.main:app --reload
cd gleanmem/backend && uv run pytest
cd gleanmem/backend && uv run gleanmem-distill scan <repo>   # codebase 蒸馏 dry-run（不调 LLM）
```

`uv sync` installs dependencies, `alembic upgrade head` applies migrations, `gleanmem` starts the FastAPI app, and the health endpoint is `/health`. `gleanmem-distill` is the codebase-distillation client (`scan` reports file counts + token estimate before spending anything; `run` distills and uploads cards; `sync` rebuilds the md projection; `refresh` pushes git diffs into the inbox for incremental regeneration). End-to-end demos, none of which need an LLM key: `scripts/e2e_demo.py` (V1), `scripts/e2e_codebase_demo.py` (V1.5a batch), `scripts/e2e_incremental_demo.py` (V1.5b incremental), `scripts/e2e_scheduler_demo.py` (V2 cron — boots its own uvicorn on :8011 and waits for the service to flush itself). Schema is created by `alembic upgrade head` only; there is no `seed.py` (deleted with the V1.5 dual-track cleanup) — use `scripts/seed_demo_space.py` or the pytest fixtures for data.

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
