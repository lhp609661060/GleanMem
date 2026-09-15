"""RecallOrchestrator — parallel 3-way retrieval + merge + rank.

V2：三路并发（独立 session），延迟 sum→max；一路失败不阻塞另两路。
Not an Agent: no loop, no tool calling, no LLM decisions.
"""

from __future__ import annotations

import asyncio
import logging

from yd_memory_service.core.database import async_session_factory
from yd_memory_service.core.long_term.pg_store import LongTermStore
from yd_memory_service.core.wiki.db_store import WikiStore

from .ranker import rank_by_relevance

logger = logging.getLogger(__name__)


class RecallResult:
    def __init__(self, memories: list, wiki_refs: list, hint: str):
        self.memories = memories
        self.wiki_refs = wiki_refs
        self.hint = hint

    def to_dict(self) -> dict:
        return {
            "memories": self.memories,
            "wiki_refs": self.wiki_refs,
            "hint": self.hint,
        }


async def recall(intent: str, agent_id: str) -> RecallResult:
    """三路并发检索：hot（高频）/ cold（ts_rank）/ wiki（文档）。

    V1 同 AsyncSession 不支持并发任务，三路顺序执行；V2 起独立 session 用
    asyncio.gather 并发，延迟降为 max(hot,cold,wiki)。只读场景独立 session
    安全；return_exceptions 容错——一路失败该路返空，merge 仍可用其他路结果，
    不阻塞整体 recall。
    """
    # 三路各自独立 session，互不阻塞
    async def _hot() -> list:
        async with async_session_factory() as s:
            return await LongTermStore(s).get_hot(agent_id, top_k=20)

    async def _cold() -> list:
        async with async_session_factory() as s:
            return await LongTermStore(s).search(
                intent, agent_id, top_k=5, with_rank=True
            )

    async def _wiki() -> list:
        async with async_session_factory() as s:
            return await WikiStore(s).search(intent, agent_id, top_k=3)

    hot, cold, wiki = await asyncio.gather(
        _hot(), _cold(), _wiki(), return_exceptions=True
    )

    # 容错：异常路降级为空列表，不影响其余路结果
    if isinstance(hot, Exception):
        logger.warning("recall hot 路失败，降级为空: %s", hot)
        hot = []
    if isinstance(cold, Exception):
        logger.warning("recall cold 路失败，降级为空: %s", cold)
        cold = []
    if isinstance(wiki, Exception):
        logger.warning("recall wiki 路失败，降级为空: %s", wiki)
        wiki = []

    # merge by id, hot takes priority；冷路带上 ts_rank 供重排（N5 修复）
    seen = {m.id for m in hot}
    pool = list(hot)
    for m, rank in cold:
        m._ts_rank = rank  # type: ignore[attr-defined]
        if m.id not in seen:
            pool.append(m)
            seen.add(m.id)

    ranked = rank_by_relevance(intent, pool)

    memories = [
        {
            "id": m.id,
            "title": m.title,
            "summary": m.content[:300],
            "weight": round(m.weight, 2),
            "type": m.type,
        }
        for m in ranked[:5]
    ]

    wiki_refs = [
        {
            "id": w.id,
            "title": w.title,
            "description": w.description,
        }
        for w in wiki[:2]
    ]

    hint = _build_hint(intent, memories, wiki_refs)

    return RecallResult(memories=memories, wiki_refs=wiki_refs, hint=hint)


def _build_hint(intent: str, memories: list, wiki_refs: list) -> str:
    parts = []
    if memories:
        parts.append(f"找到 {len(memories)} 条相关记忆")
    if wiki_refs:
        parts.append(f"{len(wiki_refs)} 篇参考文档")
    if not parts:
        parts.append("未找到匹配的记忆,可尝试更换关键词")
    return ", ".join(parts)
