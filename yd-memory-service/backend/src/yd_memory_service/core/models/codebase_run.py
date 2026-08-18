from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from .base import Base, new_uuid, utcnow


class CodebaseRun(Base):
    """codebase 蒸馏的 run 级审计（D12）。

    batch 不走收件箱 → 不写 learning_logs，故审计落在 run 粒度：
    知识卡的 metadata.run_id 回指本表，构成「卡 → run → commit SHA → 文件」审计链。
    event 增量（mode=incremental）同时写 learning_logs 与本表，两者不互斥。
    """

    __tablename__ = "codebase_runs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=new_uuid
    )
    agent_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("agent_spaces.agent_id"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)  # batch | incremental
    repo_path: Mapped[str] = mapped_column(
        Text, nullable=False, comment="客户端上报的仓库标识"
    )
    commit_sha: Mapped[str | None] = mapped_column(
        String(40), nullable=True, comment="蒸馏基线"
    )
    files_scanned: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), comment="排除清单过滤后的有效文件数"
    )
    files_excluded: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), comment="被排除数（体积治理可见性）"
    )
    cards_written: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    cards_skipped_protected: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), comment="修订保护生效次数"
    )
    tokens_used: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", server_default=text("'running'")
    )  # running | succeeded | failed
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("idx_cbruns_agent", agent_id, text("started_at DESC")),
    )
