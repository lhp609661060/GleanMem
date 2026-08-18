# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

This repo contains design docs, spike experiments, and a first-cut backend implementation for **yd-memory-service**, a multi-agent external memory platform integrated to Dify (MCP), DSH/other agents (REST), and business systems (push). The referenced `yd-agent` codebase is frozen.

- `docs/yd-memory-service/01-design.md` is the **authoritative design (v3 rewrite)** — read it before touching code.
- `yd-memory-service/backend/` implements V1 + V1.5a (batch codebase distillation) + V1.5b (event incremental) + N6 review filtering — v3.4 contract, 54 tests green. **Every 🔴/🟡 in `01-design.md` §实现现状与差距清单 is closed**; only the 🟢 seed.py/Alembic dual-track item and V2 backlog remain.
- `yd-memory-service/frontend/` is a Vue 3 + Vite admin UI (5 pages), no longer empty — see `frontend/README.md`.
- `spike/` holds disposable Dify/MCP verification scripts + `SPIKE-REPORT.md` (Spike passed).
- It is a git repo (branch `main`, Conventional Commits; see `AGENTS.md` for commit/tag conventions). Dev commands (uv, docker compose, alembic, pytest) are documented in `AGENTS.md`.

## Document set and reading order

All docs live in `docs/yd-memory-service/` and form a single deliberate sequence (see `README.md`):

| File | Role |
|------|------|
| `01-design.md` | **Authoritative design (v3.1)** — multi-agent external memory platform, knowledge inbox (source × trigger), push-first observation, per-space API Key, 4 learning needs, codebase distillation as V1.5 candidate mainline. Any change to "what we're building" lands here first. |
| `02-review-round1.md` | Round 1 review — code-level bugs. Referenced for implementation caveats. |
| `03-review-round2.md` | Round 2 review — unverified assumptions / risk list. |
| `04-review-round3.md` | Round 3 review — root-direction critique (does the project need this at all, is it over-designed for a resume-backing side project). Sets scope-cutting context. |
| `05-solutions.md` | **P0/P1/P2 problem-and-verification manual.** Every 🔴 blocker and 🟡 should-fix from the reviews has a fix + a verification checklist here. Anyone about to write code reads this after `01-design.md`. |
| `06-repo-wiki-research.md` | Qoder Repo Wiki research (demand feedback from DSH usage). Folded into `01-design.md` v3.1 §代码库蒸馏 as the V1.5 candidate mainline; this file keeps the research details. |
| `07-architect-review.md` | v3.1 architecture review — gap-list verification, new findings (N1-N9), feasibility, and the working order that `01-design.md` v3.1 修正意见 A-E are based on. Read before starting V1 code. |
| `README.md` | Index + reading order + one-liner summary. |

When updating design decisions, keep these five in sync: the newer file overrides the older on conflicts, and `01-design.md` should reflect the final decision even when the reasoning lives in a review file. Cross-references between docs use relative Markdown links — preserve them.

## Core design invariants (don't drift from these without explicit sign-off)

These are load-bearing across the whole design; a proposed edit that violates one usually means the reviewer missed context:

- **PostgreSQL is the only runtime dependency.** No Redis, no separate vector DB in V1. `pending_events` and `learning_logs` are PG tables, not queues. Chinese search is `tsvector + zhparser`, not pgvector.
- **The Recall Orchestrator is a function, not an Agent.** No loop, no tool calling, no LLM decisions in V1 (rule-based rerank). Only three MCP tools are exposed to Dify: `recall`, `load_memory`, `memorize`. `query_db` was deleted in v3 (D3) — do not reintroduce it or any SQL-exposing tool.
- **Identity is unified on `agent_id`:** MCP SSE carries it in the URL's `X-Agent-ID` header (Dify does not forward `sys.conversation_id`/`app_id` — see `02-review-round1.md` §1.1); REST carries it via per-space API Key (`Authorization: Bearer`). Both resolve to the same `agent_id`; `agent_id` never appears as a tool argument.
- **Learning sources share one inbox.** All four learning needs (chat feed / example induction / observation / scheduled) land in `pending_events` with a `source` dimension; source (chat|example|observation, +codebase V1.5) and trigger (webhook-flush|cron) are orthogonal. Observation learning is **push-first** — pull/fetch is deferred to other projects and re-enters later as a producer of the same `ObservationEvent`.
- **Codebase distillation (V1.5 candidate mainline) is a batch/event split:** initial full generation is a batch job that writes wiki docs directly (bypasses `pending_events`); incremental diffs flow through the inbox as `source=codebase`. Auto-updates must skip `metadata.protected` entries — incremental update and human-edit protection ship together, never separately. It is the one sanctioned in-project pull producer (local code, no credentials).
- **Wiki loading uses the Skill-style mechanism**: the `description` field is what tsvector matches on, so the management UI enforces it. Don't propose dumping full wiki bodies into system prompts.
- **Learning is deferred + audited.** `memorize` writes to `pending_events`; the `LearningModel` runs on Flush (webhook at end of Dify workflow) under a PG advisory lock keyed by `agent_id`, and every decision writes a `learning_logs` row — including `llm_raw_response` for LLM-mode decisions.
- **The project's real driver is resume/portfolio, not a paying user** (`04-review-round3.md` §1.1). Bias scope decisions toward "buildable + demonstrable" over "production-audit-grade". Aggressive cuts to admin UI, review workflow, or dashboarding are on the table; adding them needs a justification.

## Working conventions for this repo

- **Language:** documents are written in Simplified Chinese with English identifiers for code/APIs/tables. Match that style when editing.
- **Severity tags in review docs** are 🔴 阻塞项 / 🟡 应修项 / 🟢 可接受. Keep them when quoting or updating findings; they drive `05-solutions.md` prioritization (P0 = Spike blockers, P1 = pre-V1, P2 = V1.1+).
- **Timelines** in `01-design.md` §落地路径 are "Spike done + 3-week V1 (Week 1 split: day1-2 build-chain/entry/P0-3, day3-5 data-integrity/audit/pytest, day6-7 v3-contract migration) + V1.5 split into V1.5a (batch+protected+dual-layer, 1.5-2w) / V1.5b (event incremental, 1-1.5w)". If you change effort estimates in one place, reflect them in the roadmap section too.
- **No commands to run in this tree are part of any CI.** Dev commands (`uv sync`, `docker compose up`, `alembic upgrade head`, `pytest`) are documented in `AGENTS.md`; run them only when the user asks to build/test the backend. `pytest` needs the local PG container running (tests hit the real DB; pytest-asyncio loop scope is session-level — do not change it back to function scope, the global async engine breaks).

## Memory context caveats

Two auto-loaded memories (`versioning-deferred-mvp`, `yd-secret-key-startup-dep`) refer to a different repo (`generator/backend`, `executor/backend`, `yd_core.crypto`). Those paths do **not** exist in this tree — treat those memories as background on the frozen yd-agent codebase, not as facts about files here. Verify before citing.
