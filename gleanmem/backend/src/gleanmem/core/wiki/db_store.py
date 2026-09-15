"""PostgreSQL Wiki store with tsvector full-text search."""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import Text, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.wiki_document import WikiDocument


class WikiStore:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def create(self, doc: WikiDocument) -> WikiDocument:
        self._s.add(doc)
        await self._s.flush()
        return doc

    async def get(self, doc_id: str) -> WikiDocument | None:
        return await self._s.get(WikiDocument, doc_id)

    async def update(self, doc: WikiDocument) -> WikiDocument:
        await self._s.merge(doc)
        await self._s.flush()
        return doc

    async def _available_ts_configs(self) -> list[str]:
        rows = await self._s.execute(
            text("SELECT cfgname FROM pg_ts_config WHERE cfgname IN ('zhparser', 'simple')")
        )
        return [r[0] for r in rows.all()]

    async def search(
        self,
        query: str,
        agent_id: str | None = None,
        *,
        top_k: int = 5,
        include_global: bool = True,
        source: str | None = None,
        exclude_source: str | None = None,
    ) -> Sequence[WikiDocument]:
        """全文检索 wiki。

        source / exclude_source 按 metadata.source 过滤（V1.5a，07 评审 §5.3）：
        codebase 知识卡与业务 wiki 同表混池，不过滤时卡量大会挤占 Top3。
        """
        conditions = [WikiDocument.is_deleted == False]

        if agent_id is not None:
            if include_global:
                conditions.append(
                    (WikiDocument.agent_id == agent_id)
                    | (WikiDocument.agent_id.is_(None))
                )
            else:
                conditions.append(WikiDocument.agent_id == agent_id)

        if source is not None:
            conditions.append(WikiDocument.extra_meta["source"].astext == source)
        if exclude_source is not None:
            # metadata 无 source 键的历史条目视为「非该 source」，须保留
            conditions.append(
                (WikiDocument.extra_meta["source"].astext != exclude_source)
                | (WikiDocument.extra_meta["source"].is_(None))
            )

        configs = await self._available_ts_configs()
        for cfg in ("zhparser", "simple"):
            if cfg not in configs:
                continue
            and_tsq = func.plainto_tsquery(cfg, query)
            stmt = (
                select(WikiDocument, func.ts_rank(WikiDocument.search_vector, and_tsq).label("rank"))
                .where(*conditions, WikiDocument.search_vector.op("@@")(and_tsq))
                .order_by(text("rank DESC"))
                .limit(top_k)
            )
            result = await self._s.execute(stmt)
            rows = result.all()
            if rows:
                return [row[0] for row in rows]

            # AND 空结果 → OR 降级（同 long_term/pg_store.py，P0-3 实测结论）
            or_tsq = func.to_tsquery(
                cfg, func.replace(cast(and_tsq, Text), " & ", " | ")
            )
            stmt = (
                select(WikiDocument, func.ts_rank(WikiDocument.search_vector, or_tsq).label("rank"))
                .where(*conditions, WikiDocument.search_vector.op("@@")(or_tsq))
                .order_by(text("rank DESC"))
                .limit(top_k)
            )
            result = await self._s.execute(stmt)
            rows = result.all()
            if rows:
                return [row[0] for row in rows]

        ilike = f"%{query}%"
        stmt = (
            select(WikiDocument)
            .where(*conditions, (WikiDocument.title.ilike(ilike)) | (WikiDocument.description.ilike(ilike)))
            .limit(top_k)
        )
        result = await self._s.execute(stmt)
        return result.scalars().all()
