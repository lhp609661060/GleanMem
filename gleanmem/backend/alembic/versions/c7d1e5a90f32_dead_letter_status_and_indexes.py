"""dead-letter status on pending_events + flush index + api_key_hash unique index

D19：重试耗尽的事件不再删除，改标 status='dead' 留在库里（可 revive）。
D20：鉴权每次按 api_key_hash 查库，补唯一索引；flush 快照按 (agent_id, status,
created_at) 取前 N 条，补对应索引。

Revision ID: c7d1e5a90f32
Revises: 4f7c1a92be05
Create Date: 2026-09-28 10:12:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7d1e5a90f32'
down_revision: Union[str, None] = '4f7c1a92be05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'pending_events',
        sa.Column(
            'status',
            sa.String(length=8),
            server_default=sa.text("'pending'"),
            nullable=False,
            comment='pending=待处理 | dead=重试耗尽的死信（保留数据，不再进 flush）',
        ),
    )
    op.create_index(
        'idx_pending_events_flush',
        'pending_events',
        ['agent_id', 'status', 'created_at'],
        unique=False,
    )
    op.create_index(
        'uq_agent_spaces_key_hash', 'agent_spaces', ['api_key_hash'], unique=True
    )


def downgrade() -> None:
    op.drop_index('uq_agent_spaces_key_hash', table_name='agent_spaces')
    op.drop_index('idx_pending_events_flush', table_name='pending_events')
    op.drop_column('pending_events', 'status')
