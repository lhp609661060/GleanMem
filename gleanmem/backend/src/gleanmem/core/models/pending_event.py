from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, new_uuid


class PendingEvent(Base, TimestampMixin):
    __tablename__ = "pending_events"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=new_uuid
    )
    agent_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("agent_spaces.agent_id"), nullable=False
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="chat", server_default=text("'chat'")
    )  # chat | example | observation | codebase(V1.5)
    session_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Dify conversation_id,审计维度"
    )
    event_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # chat: user_feedback | agent_mark；observation: entity_changed 等
    context: Mapped[str] = mapped_column(Text, nullable=False)
    marked_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    dedup_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="幂等键（observation 必填 = 业务方 event_id）"
    )
    extra_meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"))
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    __table_args__ = (
        Index("idx_pending_agent", agent_id, "created_at"),
        Index(
            "idx_pending_dedup",
            agent_id,
            dedup_key,
            unique=True,
            postgresql_where=text("dedup_key IS NOT NULL"),
        ),
    )
