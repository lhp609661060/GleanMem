"""PostgreSQL long-term memory store with tsvector + zhparser full-text search."""

from __future__ import annotations

import math
from typing import Sequence

from sqlalchemy import Text, cast, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from yd_memory_service.core.models.long_term_memory import LongTermMemory


class LongTermStore:
    def __init__(self, session: AsyncSession):
        self._s = session

    # -- CRUD -------------------------------------------------------

    async def create(self, memory: LongTermMemory) -> LongTermMemory:
        self._s.add(memory)
        await self._s.flush()
        return memory

    async def get(self, memory_id: str) -> LongTermMemory | None:
        return await self._s.get(LongTermMemory, memory_id)

    async def get_scoped(self, memory_id: str, agent_id: str) -> LongTermMemory | None:
        """按 agent_id 归属校验取记忆（load_memory 用，防跨 Space 越权读取）。"""
        stmt = select(LongTermMemory).where(
            LongTermMemory.id == memory_id,
            LongTermMemory.agent_id == agent_id,
            LongTermMemory.is_deleted == False,
        )
        result = await self._s.execute(stmt)
        return result.scalar_one_or_none()

    async def update(self, memory: LongTermMemory) -> LongTermMemory:
        await self._s.merge(memory)
        await self._s.flush()
        return memory

    async def soft_delete(self, memory_id: str) -> None:
        await self._s.execute(
            update(LongTermMemory)
            .where(LongTermMemory.id == memory_id)
            .values(is_deleted=True)
        )

    # -- tsvector full-text search ----------------------------------

    async def _available_ts_configs(self) -> list[str]:
        rows = await self._s.execute(
            text("SELECT cfgname FROM pg_ts_config WHERE cfgname IN ('zhparser', 'simple')")
        )
        return [r[0] for r in rows.all()]

    async def search(
        self,
        query: str,
        agent_id: str,
        *,
        top_k: int = 20,
        exclude_deleted: bool = True,
        min_weight: float = 0.0,
        with_rank: bool = False,
    ) -> Sequence[LongTermMemory] | Sequence[tuple[LongTermMemory, float]]:
        """Full-text search.

        Uses tsvector if available (zhparser > simple), falls back to ILIKE.
        with_rank=True 返回 (memory, ts_rank) 元组供编排器合并排序（N5 修复）。
        """
        conditions = [LongTermMemory.agent_id == agent_id]
        if exclude_deleted:
            conditions.append(LongTermMemory.is_deleted == False)
        if min_weight > 0:
            conditions.append(LongTermMemory.weight >= min_weight)

        # Try tsvector with available configs
        configs = await self._available_ts_configs()
        for cfg in ("zhparser", "simple"):
            if cfg not in configs:
                continue
            and_tsq = func.plainto_tsquery(cfg, query)
            stmt = (
                select(LongTermMemory, func.ts_rank(LongTermMemory.search_vector, and_tsq).label("rank"))
                .where(*conditions, LongTermMemory.search_vector.op("@@")(and_tsq))
                .order_by(text("rank DESC"))
                .limit(top_k)
            )
            result = await self._s.execute(stmt)
            rows = result.all()
            if rows:
                return (
                    [(r[0], float(r[1])) for r in rows]
                    if with_rank
                    else [r[0] for r in rows]
                )

            # AND 语义空结果 → 降级 OR 语义（P0-3 实测：自然语言查询 AND 命中率仅 25%，
            # OR + ts_rank 达 100%）。OR 查询 = 把 tsquery 的 ' & ' 替换为 ' | '。
            or_tsq = func.to_tsquery(
                cfg, func.replace(cast(and_tsq, Text), " & ", " | ")
            )
            stmt = (
                select(LongTermMemory, func.ts_rank(LongTermMemory.search_vector, or_tsq).label("rank"))
                .where(*conditions, LongTermMemory.search_vector.op("@@")(or_tsq))
                .order_by(text("rank DESC"))
                .limit(top_k)
            )
            result = await self._s.execute(stmt)
            rows = result.all()
            if rows:
                return (
                    [(r[0], float(r[1])) for r in rows]
                    if with_rank
                    else [r[0] for r in rows]
                )

        # Fall back to ILIKE
        ilike = f"%{query}%"
        stmt = (
            select(LongTermMemory)
            .where(*conditions, (LongTermMemory.title.ilike(ilike)) | (LongTermMemory.content.ilike(ilike)))
            .order_by(LongTermMemory.weight.desc())
            .limit(top_k)
        )
        result = await self._s.execute(stmt)
        memories = result.scalars().all()
        return (
            [(m, 0.0) for m in memories]
            if with_rank
            else memories
        )

    # -- hot memories (by weight) ---------------------------------------

    async def get_hot(
        self,
        agent_id: str,
        *,
        top_k: int = 20,
        exclude_deleted: bool = True,
    ) -> Sequence[LongTermMemory]:
        conditions = [LongTermMemory.agent_id == agent_id]
        if exclude_deleted:
            conditions.append(LongTermMemory.is_deleted == False)

        stmt = (
            select(LongTermMemory)
            .where(*conditions)
            .order_by(LongTermMemory.weight.desc())
            .limit(top_k)
        )
        result = await self._s.execute(stmt)
        return result.scalars().all()

    # -- count ----------------------------------------------------------

    async def count(self, agent_id: str, *, active_only: bool = True) -> int:
        conditions = [LongTermMemory.agent_id == agent_id]
        if active_only:
            conditions.append(LongTermMemory.is_deleted == False)

        stmt = select(func.count()).where(*conditions)
        result = await self._s.execute(stmt)
        return result.scalar_one()

    # -- weight decay ---------------------------------------------------

    async def decay_weights(
        self, agent_id: str, decay_per_day: float, min_weight: float
    ) -> int:
        """Apply decay: weight = max(weight * decay_per_day, min_weight)."""
        stmt = (
            update(LongTermMemory)
            .where(
                LongTermMemory.agent_id == agent_id,
                LongTermMemory.is_deleted == False,
            )
            .values(weight=func.greatest(LongTermMemory.weight * decay_per_day, min_weight))
        )
        result = await self._s.execute(stmt)
        return result.rowcount

    # -- archive low-weight memories ------------------------------------

    async def archive_lowest(
        self, agent_id: str, ratio: float = 0.1
    ) -> int:
        """Set is_deleted=True for the lowest-weight `ratio` fraction of memories."""
        total = await self.count(agent_id)
        if total == 0:
            return 0

        limit = max(1, math.floor(total * ratio))
        subq = (
            select(LongTermMemory.id)
            .where(
                LongTermMemory.agent_id == agent_id,
                LongTermMemory.is_deleted == False,
            )
            .order_by(LongTermMemory.weight.asc())
            .limit(limit)
        ).scalar_subquery()

        stmt = (
            update(LongTermMemory)
            .where(LongTermMemory.id.in_(subq))
            .values(is_deleted=True, review_status="deprecated")
        )
        result = await self._s.execute(stmt)
        return result.rowcount
