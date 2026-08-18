from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, new_uuid


class LongTermMemory(Base, TimestampMixin):
    __tablename__ = "long_term_memories"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=new_uuid
    )
    agent_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("agent_spaces.agent_id"), nullable=False
    )
    type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # user | feedback | project | reference | task
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    extra_meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"))
    weight: Mapped[float] = mapped_column(Float, default=1.0, server_default=text("1.0"))
    review_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default=text("'pending'")
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)

    __table_args__ = (
        Index(
            "idx_memories_agent_weight",
            agent_id,
            weight.desc(),
            postgresql_where=(is_deleted == False),
        ),
        Index("idx_memories_search", "search_vector", postgresql_using="gin"),
    )
