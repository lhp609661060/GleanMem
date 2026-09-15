from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class WikiDocument(Base, TimestampMixin):
    __tablename__ = "wiki_documents"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)  # user slug
    agent_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("agent_spaces.agent_id"),
        nullable=True,
        comment="NULL = 全局共享",
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False,
        comment="强制填写,召回时用 tsvector 匹配此字段")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[dict] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    extra_meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)

    __table_args__ = (
        Index("idx_wiki_search", "search_vector", postgresql_using="gin"),
    )
