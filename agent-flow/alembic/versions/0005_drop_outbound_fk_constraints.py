"""去掉外呼关联的外键约束（call_session.call_task_id / call_target.task_id）

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-24

理由：删除 call_task 时被 FK 约束阻止（有通话记录引用）。
改为不用 FK，由应用层（console service remove）做关联删除：
  - call_target（号码清单）：删任务时一并删除（从属数据）
  - call_session.call_task_id：删任务时置 NULL（通话记录是历史，保留）
列本身保留，仅去掉 DB 约束。
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = 'callbot'


def upgrade() -> None:
    op.execute(
        f'ALTER TABLE {SCHEMA}.call_session '
        f'DROP CONSTRAINT IF EXISTS call_session_call_task_id_fkey'
    )
    op.execute(
        f'ALTER TABLE {SCHEMA}.call_target '
        f'DROP CONSTRAINT IF EXISTS call_target_task_id_fkey'
    )


def downgrade() -> None:
    # 恢复 FK（NO ACTION）。若已有悬空引用会失败，需先清理。
    op.execute(
        f'ALTER TABLE {SCHEMA}.call_target '
        f'ADD CONSTRAINT call_target_task_id_fkey '
        f'FOREIGN KEY (task_id) REFERENCES {SCHEMA}.call_task(id)'
    )
    op.execute(
        f'ALTER TABLE {SCHEMA}.call_session '
        f'ADD CONSTRAINT call_session_call_task_id_fkey '
        f'FOREIGN KEY (call_task_id) REFERENCES {SCHEMA}.call_task(id)'
    )
