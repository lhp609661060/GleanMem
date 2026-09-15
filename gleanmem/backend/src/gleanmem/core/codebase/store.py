"""codebase 知识卡仓储 + run 级审计（D12）。

不变式：
- 自动更新**必须**跳过 metadata.protected=true 的卡（增量与修订保护捆绑发布，Qoder 教训）。
- 每张卡的 metadata 固化 run_id / 文件路径 / commit SHA / 指纹 → 审计链
  「卡 → run → commit SHA → 文件」闭合。
- 知识卡是唯一事实源，md 是投影：更新卡时打 wiki_sync_pending=true，等 CLI sync（D11）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.codebase_run import CodebaseRun
from ..models.wiki_document import WikiDocument
from .analyzer import CardDraft

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_BUDGET = 300_000  # Space 级默认硬预算（config.codebase_token_budget 可覆盖）


def card_id(agent_id: str, module: str) -> str:
    """知识卡 slug：稳定可复算，使 batch 重跑走 upsert 而非产生重复卡。"""
    safe = module.replace("/", ".").replace(" ", "_").strip(".") or "root"
    return f"cb.{agent_id[:8]}.{safe}"[:200]


@dataclass
class UpsertResult:
    written: int = 0
    skipped_protected: int = 0


class CodebaseStore:
    def __init__(self, session: AsyncSession):
        self._s = session

    # -- run 级审计 ---------------------------------------------------------

    async def start_run(
        self,
        *,
        agent_id: str,
        mode: str,
        repo_path: str,
        commit_sha: str | None = None,
        model: str | None = None,
        files_scanned: int = 0,
        files_excluded: int = 0,
    ) -> CodebaseRun:
        run = CodebaseRun(
            agent_id=agent_id,
            mode=mode,
            repo_path=repo_path,
            commit_sha=commit_sha,
            model=model,
            files_scanned=files_scanned,
            files_excluded=files_excluded,
        )
        self._s.add(run)
        await self._s.flush()
        return run

    async def finish_run(
        self,
        run: CodebaseRun,
        *,
        status: str,
        error_message: str | None = None,
    ) -> CodebaseRun:
        run.status = status
        run.error_message = error_message
        run.finished_at = func.now()
        await self._s.flush()
        return run

    async def get_run(self, run_id: str, agent_id: str) -> CodebaseRun | None:
        """归属校验一并做掉（同 load_memory 的 get_scoped 纪律）。"""
        result = await self._s.execute(
            select(CodebaseRun).where(
                CodebaseRun.id == run_id, CodebaseRun.agent_id == agent_id
            )
        )
        return result.scalar_one_or_none()

    async def list_runs(
        self, agent_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[CodebaseRun]:
        result = await self._s.execute(
            select(CodebaseRun)
            .where(CodebaseRun.agent_id == agent_id)
            .order_by(CodebaseRun.started_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def tokens_spent(self, agent_id: str) -> int:
        """该 Space 历史累计 token（硬预算裁决依据，D14）。"""
        result = await self._s.execute(
            select(func.coalesce(func.sum(CodebaseRun.tokens_used), 0)).where(
                CodebaseRun.agent_id == agent_id
            )
        )
        return int(result.scalar_one() or 0)

    # -- 知识卡 upsert ------------------------------------------------------

    async def upsert_card(
        self,
        *,
        agent_id: str,
        draft: CardDraft,
        run_id: str,
        commit_sha: str | None = None,
    ) -> str | None:
        """写入/更新一张知识卡。

        返回 card id；若目标卡受 protected 保护则返回 None（跳过，不覆盖人的判断）。
        """
        cid = card_id(agent_id, draft.module)
        existing = await self._s.get(WikiDocument, cid)

        if existing is not None and (existing.extra_meta or {}).get("protected"):
            logger.info("Card %s is protected; skipping auto-update", cid)
            return None

        meta = {
            "source": "codebase",
            "run_id": run_id,
            "module": draft.module,
            "file_paths": draft.file_paths,
            "fingerprints": draft.fingerprints,
            "commit_sha": commit_sha,
            "narrative": draft.narrative,  # 叙述层原文：md 投影由 CLI 从此渲染（D11）
            "wiki_sync_pending": True,  # 卡已更新，md 待 sync
        }
        if existing is not None:
            # 保留人工可能加上的 protected 之外的自定义键
            preserved = {
                k: v
                for k, v in (existing.extra_meta or {}).items()
                if k not in meta and k != "protected"
            }
            meta = {**preserved, **meta}

        if existing is None:
            self._s.add(
                WikiDocument(
                    id=cid,
                    agent_id=agent_id,
                    title=draft.title,
                    description=draft.description,
                    content=draft.content,
                    tags=draft.tags,
                    extra_meta=meta,
                )
            )
        else:
            existing.title = draft.title
            existing.description = draft.description
            existing.content = draft.content
            existing.tags = draft.tags
            existing.extra_meta = meta
            existing.is_deleted = False
        await self._s.flush()
        return cid

    async def list_cards(
        self,
        agent_id: str,
        *,
        sync_pending: bool | None = None,
        limit: int = 200,
    ) -> list[WikiDocument]:
        """CLI sync 拉取 md 投影用（D11）。"""
        conditions = [
            WikiDocument.agent_id == agent_id,
            WikiDocument.is_deleted == False,  # noqa: E712
            WikiDocument.extra_meta["source"].astext == "codebase",
        ]
        if sync_pending is True:
            conditions.append(
                WikiDocument.extra_meta["wiki_sync_pending"].astext == "true"
            )
        result = await self._s.execute(
            select(WikiDocument).where(*conditions).order_by(WikiDocument.id).limit(limit)
        )
        return list(result.scalars().all())

    async def mark_synced(self, agent_id: str, card_ids: list[str]) -> int:
        """CLI 落盘 md 后回执：清 wiki_sync_pending。"""
        if not card_ids:
            return 0
        result = await self._s.execute(
            select(WikiDocument).where(
                WikiDocument.agent_id == agent_id, WikiDocument.id.in_(card_ids)
            )
        )
        docs = list(result.scalars().all())
        for doc in docs:
            meta = dict(doc.extra_meta or {})
            meta["wiki_sync_pending"] = False
            doc.extra_meta = meta
        await self._s.flush()
        return len(docs)
