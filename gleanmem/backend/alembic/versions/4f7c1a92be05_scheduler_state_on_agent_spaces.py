"""scheduler state columns on agent_spaces for V2 built-in cron

Revision ID: 4f7c1a92be05
Revises: ba3603bedd21
Create Date: 2026-09-20 11:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '4f7c1a92be05'
down_revision: Union[str, None] = 'ba3603bedd21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'agent_spaces',
        sa.Column(
            'last_fired_slot',
            sa.String(length=16),
            nullable=True,
            comment='最近一次已触发的 cron 槽（分钟粒度 YYYY-MM-DDTHH:MM），多副本/重启去重',
        ),
    )
    op.add_column(
        'agent_spaces',
        sa.Column(
            'last_scheduled_flush',
            sa.DateTime(timezone=True),
            nullable=True,
            comment='最近一次调度 flush 的完成时间，可观测用',
        ),
    )


def downgrade() -> None:
    op.drop_column('agent_spaces', 'last_scheduled_flush')
    op.drop_column('agent_spaces', 'last_fired_slot')
