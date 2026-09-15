from __future__ import annotations

from sqlalchemy import String, Text, Float, Integer, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, new_uuid, utcnow


class AgentSpace(Base, TimestampMixin):
    __tablename__ = "agent_spaces"

    agent_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=new_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="SHA-256(space_key)，仅存哈希"
    )
    api_key_prefix: Mapped[str | None] = mapped_column(
        String(8), nullable=True, comment="space_key 前 8 位，UI 辨认用"
    )
    config: Mapped[dict] = mapped_column(
        JSONB,
        default=lambda: {"decay_per_day": 0.95, "min_weight": 0.1, "max_memories": 5000, "learning_mode": "heuristic"},
    )
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default=text("'active'")
    )
    purged_at: Mapped[str | None] = mapped_column(String, nullable=True)
