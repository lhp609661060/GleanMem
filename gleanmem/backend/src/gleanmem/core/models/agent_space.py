from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String, Text, DateTime, text
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
    last_fired_slot: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        comment="最近一次已触发的 cron 槽（分钟粒度 YYYY-MM-DDTHH:MM），多副本/重启去重",
    )
    last_scheduled_flush: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近一次调度 flush 完成时间"
    )
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default=text("'active'")
    )
    purged_at: Mapped[str | None] = mapped_column(String, nullable=True)

    # 鉴权每次请求都要按 hash 查库：无索引即每次顺序扫表；唯一约束同时挡住极小概率碰撞。
    # NULL 不参与唯一性（测试/导入的 Space 可以没有 key）。
    __table_args__ = (Index("uq_agent_spaces_key_hash", api_key_hash, unique=True),)
