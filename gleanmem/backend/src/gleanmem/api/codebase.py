"""codebase 蒸馏入站（V1.5a，D13）。

端点独立于 observations：事件结构不同，硬塞会污染 observation 分析器契约。

服务端零仓库访问权（D11）：客户端 CLI 在本地扫描/蒸馏，这里只收知识卡与审计元数据。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gleanmem.core.codebase.analyzer import CardDraft
from gleanmem.core.codebase.store import (
    DEFAULT_TOKEN_BUDGET,
    CodebaseStore,
)
from gleanmem.core.database import get_db
from gleanmem.core.models.agent_space import AgentSpace
from gleanmem.core.models.pending_event import PendingEvent

from .deps import require_agent

router = APIRouter(prefix="/api/v1/codebase", tags=["codebase"])

FILE_SNIPPET_MAX_BYTES = 4 * 1024  # 与 observations 一致的限长护栏


class RunCreate(BaseModel):
    mode: str = "batch"  # batch | incremental
    repo_path: str
    commit_sha: str | None = None
    model: str | None = None
    files_scanned: int = 0
    files_excluded: int = 0


class CardIn(BaseModel):
    module: str
    title: str
    description: str = Field(max_length=200)
    content: str = ""
    narrative: str = ""
    tags: list[str] = Field(default_factory=list)
    file_paths: list[str] = Field(default_factory=list)
    fingerprints: dict[str, str] = Field(default_factory=dict)
    tokens_used: int = 0


class CardsUpload(BaseModel):
    run_id: str
    cards: list[CardIn]
    finish: bool = False  # 最后一批：置 run 为 succeeded


class ChangedFile(BaseModel):
    path: str
    fingerprint: str
    snippet: str | None = None  # 变更文件片段（≤4KB）；服务端不读仓库


class ModuleChange(BaseModel):
    module: str
    files: list[ChangedFile] = Field(default_factory=list)
    change: str = "modified"  # added | modified | deleted


class RefreshIn(BaseModel):
    commit_sha: str
    changes: list[ModuleChange]


@router.post("/runs")
async def create_run(
    body: RunCreate,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """开一个蒸馏 run，返回 run_id。上传知识卡时须带上（审计链起点）。"""
    if body.mode not in ("batch", "incremental"):
        raise HTTPException(status_code=422, detail="mode 只能是 batch | incremental")

    store = CodebaseStore(db)

    # token 硬预算（D14）：超限直接拒绝开 run，避免烧光演示预算
    space = await db.get(AgentSpace, agent_id)
    budget = int((space.config or {}).get("codebase_token_budget", DEFAULT_TOKEN_BUDGET)) if space else DEFAULT_TOKEN_BUDGET
    spent = await store.tokens_spent(agent_id)
    if spent >= budget:
        raise HTTPException(
            status_code=429,
            detail=f"codebase token 预算已用尽（{spent}/{budget}），调高 config.codebase_token_budget 后重试",
        )

    run = await store.start_run(
        agent_id=agent_id,
        mode=body.mode,
        repo_path=body.repo_path,
        commit_sha=body.commit_sha,
        model=body.model,
        files_scanned=body.files_scanned,
        files_excluded=body.files_excluded,
    )
    await db.commit()
    return {
        "run_id": run.id,
        "status": run.status,
        "token_budget": budget,
        "tokens_spent": spent,
    }


@router.post("/cards")
async def upload_cards(
    body: CardsUpload,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """批量 upsert 知识卡（跳过 protected），并累计 run 的审计计数。"""
    store = CodebaseStore(db)
    run = await store.get_run(body.run_id, agent_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run 不存在或不属于当前 Space")

    written: list[str] = []
    skipped: list[str] = []
    for card in body.cards:
        draft = CardDraft(
            module=card.module,
            title=card.title,
            description=card.description,
            content=card.content,
            narrative=card.narrative,
            tags=card.tags,
            file_paths=card.file_paths,
            fingerprints=card.fingerprints,
            tokens_used=card.tokens_used,
        )
        cid = await store.upsert_card(
            agent_id=agent_id,
            draft=draft,
            run_id=run.id,
            commit_sha=run.commit_sha,
        )
        if cid is None:
            skipped.append(card.module)
        else:
            written.append(cid)
        run.tokens_used += card.tokens_used

    run.cards_written += len(written)
    run.cards_skipped_protected += len(skipped)
    if body.finish:
        await store.finish_run(run, status="succeeded")
    await db.commit()

    return {
        "run_id": run.id,
        "cards_written": len(written),
        "cards_skipped_protected": len(skipped),
        "skipped_modules": skipped,
        "tokens_used_total": run.tokens_used,
        "status": run.status,
    }


@router.post("/refresh")
async def refresh(
    body: RefreshIn,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """event 增量入站（V1.5b 消费）：按模块聚合进收件箱，幂等。

    dedup_key = cb:{commit_sha}:{module}——同一 commit 的同一模块重复推送只进一条。
    """
    accepted, duplicated = [], []
    for change in body.changes:
        for f in change.files:
            if f.snippet and len(f.snippet.encode("utf-8")) > FILE_SNIPPET_MAX_BYTES:
                raise HTTPException(
                    status_code=413, detail=f"{f.path} 的 snippet 超过 4KB 上限"
                )

        context = json.dumps(
            {
                "module": change.module,
                "change": change.change,
                "commit_sha": body.commit_sha,
                "files": [
                    {"path": f.path, "fingerprint": f.fingerprint, "snippet": f.snippet}
                    for f in change.files
                ],
            },
            ensure_ascii=False,
        )
        stmt = (
            pg_insert(PendingEvent)
            .values(
                agent_id=agent_id,
                source="codebase",
                event_type="module_changed",
                context=context,
                dedup_key=f"cb:{body.commit_sha}:{change.module}",
                extra_meta={
                    "module": change.module,
                    "commit_sha": body.commit_sha,
                    "file_paths": [f.path for f in change.files],
                },
            )
            .on_conflict_do_nothing(
                index_elements=["agent_id", "dedup_key"],
                index_where=text("dedup_key IS NOT NULL"),
            )
        )
        result = await db.execute(stmt)
        (accepted if result.rowcount > 0 else duplicated).append(change.module)

    await db.commit()
    return {
        "status": "accepted",
        "commit_sha": body.commit_sha,
        "modules_accepted": accepted,
        "modules_duplicate": duplicated,
    }


@router.get("/runs")
async def list_runs(
    page: int = 1,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """run 级审计查询（每页 50 条，倒序）。"""
    store = CodebaseStore(db)
    runs = await store.list_runs(agent_id, limit=50, offset=(max(page, 1) - 1) * 50)
    return [
        {
            "id": r.id,
            "mode": r.mode,
            "repo_path": r.repo_path,
            "commit_sha": r.commit_sha,
            "files_scanned": r.files_scanned,
            "files_excluded": r.files_excluded,
            "cards_written": r.cards_written,
            "cards_skipped_protected": r.cards_skipped_protected,
            "tokens_used": r.tokens_used,
            "model": r.model,
            "status": r.status,
            "error_message": r.error_message,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in runs
    ]


@router.get("/cards")
async def list_cards(
    sync_pending: bool | None = None,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """CLI sync 拉取 md 投影用（D11）。"""
    store = CodebaseStore(db)
    docs = await store.list_cards(agent_id, sync_pending=sync_pending)
    return [
        {
            "id": d.id,
            "title": d.title,
            "description": d.description,
            "content": d.content,
            "tags": d.tags,
            "module": (d.extra_meta or {}).get("module"),
            "narrative": (d.extra_meta or {}).get("narrative", ""),
            "file_paths": (d.extra_meta or {}).get("file_paths", []),
            "commit_sha": (d.extra_meta or {}).get("commit_sha"),
            "run_id": (d.extra_meta or {}).get("run_id"),
            "protected": bool((d.extra_meta or {}).get("protected")),
            "wiki_sync_pending": bool((d.extra_meta or {}).get("wiki_sync_pending")),
        }
        for d in docs
    ]


class SyncAck(BaseModel):
    card_ids: list[str]


@router.post("/cards/synced")
async def mark_synced(
    body: SyncAck,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """CLI 落盘 md 后回执，清 wiki_sync_pending。"""
    store = CodebaseStore(db)
    n = await store.mark_synced(agent_id, body.card_ids)
    await db.commit()
    return {"synced": n}
