from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, new_uuid


class LearningLog(Base, TimestampMixin):
    __tablename__ = "learning_logs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=new_uuid
    )
    agent_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("agent_spaces.agent_id"), nullable=False
    )
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="chat", server_default=text("'chat'")
    )  # chat | example | observation | codebase(V1.5)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    event_context: Mapped[str] = mapped_column(Text, nullable=False)
    decision_action: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # store | discard | update | merge | failed
    decision_target: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # long_term
    memory_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("long_term_memories.id"),
        nullable=True,
    )
    llm_raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyzer_mode: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # llm | heuristic | direct
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_logs_agent", "agent_id", "created_at"),
    )
