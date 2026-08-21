"""RecallOrchestrator — parallel 3-way retrieval + merge + rank.

Not an Agent: no loop, no tool calling, no LLM decisions in V1.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from yd_memory_service.core.long_term.pg_store import LongTermStore
from yd_memory_service.core.wiki.db_store import WikiStore

from .ranker import rank_by_relevance


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


async def recall(intent: str, agent_id: str, session: AsyncSession) -> RecallResult:
    mem_store = LongTermStore(session)
    wiki_store = WikiStore(session)

    # 同一 AsyncSession 不支持并发任务（SQLAlchemy async 限制），三路顺序执行；
    # V1 三路都是毫秒级查询，顺序执行不影响体感。
    hot = await mem_store.get_hot(agent_id, top_k=20)
    cold = await mem_store.search(intent, agent_id, top_k=5, with_rank=True)
    wiki = await wiki_store.search(intent, agent_id, top_k=3)

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
