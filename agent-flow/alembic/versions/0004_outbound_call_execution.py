"""外呼执行引擎 schema: call_session 外呼关联列 + call_target 号码清单表

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-24

- call_session 增 call_task_id (FK→call_task.id, nullable) / call_target_id (nullable)
  外呼通话非空；inbound 为 NULL，向后兼容（inbound 写入路径无需改动）。
  注：call_session 已有 task_id TEXT 列（业务串），call_task_id 是独立 FK，二者不冲突。
- 新增 call_target 号码清单表（执行器消费：status/attempt_count/next_attempt_ts）
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = 'callbot'


def upgrade() -> None:
    # ── call_session 外呼关联列（可空，向后兼容）──
    op.execute(
        f'ALTER TABLE {SCHEMA}.call_session '
        f'ADD COLUMN IF NOT EXISTS call_task_id BIGINT REFERENCES {SCHEMA}.call_task(id)'
    )
    op.execute(
        f'ALTER TABLE {SCHEMA}.call_session '
        f'ADD COLUMN IF NOT EXISTS call_target_id BIGINT'
    )

    # ── call_target 号码清单表 ──
    op.execute(f'''
        CREATE TABLE IF NOT EXISTS {SCHEMA}.call_target (
            id                  BIGSERIAL     PRIMARY KEY,
            task_id             BIGINT        NOT NULL REFERENCES {SCHEMA}.call_task(id),
            tenant_id           TEXT          NOT NULL,
            phone_hash          TEXT          NOT NULL,
            phone_masked        TEXT,
            user_key            TEXT          NOT NULL,
            status              TEXT          NOT NULL DEFAULT 'pending',
            attempt_count       INTEGER       NOT NULL DEFAULT 0,
            max_attempts        INTEGER       NOT NULL DEFAULT 1,
            next_attempt_ts     TIMESTAMPTZ,
            last_call_session_id BIGINT,
            last_hangup_cause   TEXT,
            create_time         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            create_user         TEXT          NOT NULL DEFAULT 'system',
            update_time         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            update_user         TEXT          NOT NULL DEFAULT 'system',
            CONSTRAINT uq_call_target_task_phone UNIQUE (task_id, phone_hash)
        )
    ''')
    op.execute(
        f'CREATE INDEX IF NOT EXISTS ix_call_target_task_status '
        f'ON {SCHEMA}.call_target (task_id, status)'
    )


def downgrade() -> None:
    op.execute(f'DROP TABLE IF EXISTS {SCHEMA}.call_target')
    op.execute(f'ALTER TABLE {SCHEMA}.call_session DROP COLUMN IF EXISTS call_target_id')
    op.execute(f'ALTER TABLE {SCHEMA}.call_session DROP COLUMN IF EXISTS call_task_id')
