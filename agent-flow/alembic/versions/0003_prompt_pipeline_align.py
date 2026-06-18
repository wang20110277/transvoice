"""prompt_config 维度重构 + prompt_version/inbound_route/call_task 三表

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-18

破坏性变更(项目初期允许):
- prompt_config: biz_system→tenant_id 重命名,新增 scenario,唯一键改为 (tenant_id, biz_type, scenario)
- call_session: 新增 tenant_id / scenario(可空,过渡兼容)
- 新增 prompt_version(版本快照)、inbound_route(DID 路由)、call_task(外呼任务定义层)
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = 'callbot'


def upgrade() -> None:
    # ── prompt_config 维度重构 ──
    op.execute(f'ALTER TABLE {SCHEMA}.prompt_config DROP CONSTRAINT IF EXISTS uq_prompt_config_system_type')
    op.execute(f'ALTER TABLE {SCHEMA}.prompt_config RENAME COLUMN biz_system TO tenant_id')
    op.execute(f"ALTER TABLE {SCHEMA}.prompt_config ADD COLUMN IF NOT EXISTS scenario TEXT NOT NULL DEFAULT 'default'")
    op.execute(
        f'ALTER TABLE {SCHEMA}.prompt_config '
        f'ADD CONSTRAINT uq_prompt_config_tenant_biz_scenario UNIQUE (tenant_id, biz_type, scenario)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS ix_prompt_config_tenant_biz '
        f'ON {SCHEMA}.prompt_config (tenant_id, biz_type)'
    )

    # ── call_session 增列(可空,过渡) ──
    op.execute(f'ALTER TABLE {SCHEMA}.call_session ADD COLUMN IF NOT EXISTS tenant_id TEXT')
    op.execute(f'ALTER TABLE {SCHEMA}.call_session ADD COLUMN IF NOT EXISTS scenario TEXT')

    # ── prompt_version 版本快照表 ──
    op.execute(f'''
        CREATE TABLE {SCHEMA}.prompt_version (
            id            BIGSERIAL    PRIMARY KEY,
            tenant_id     TEXT         NOT NULL,
            biz_type      TEXT         NOT NULL,
            scenario      TEXT         NOT NULL,
            system_prompt TEXT         NOT NULL,
            version       INTEGER      NOT NULL,
            snapshot      JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
            update_user   TEXT         NOT NULL DEFAULT 'system',
            update_time   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    ''')
    op.execute(
        f'CREATE INDEX ix_prompt_version_lookup '
        f'ON {SCHEMA}.prompt_version (tenant_id, biz_type, scenario, version)'
    )

    # ── inbound_route DID 路由表 ──
    op.execute(f'''
        CREATE TABLE {SCHEMA}.inbound_route (
            id          BIGSERIAL    PRIMARY KEY,
            did         TEXT         NOT NULL,
            did_pattern TEXT,
            tenant_id   TEXT         NOT NULL,
            biz_type    TEXT         NOT NULL,
            scenario    TEXT         NOT NULL DEFAULT 'default',
            is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
            description TEXT,
            create_time TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            create_user TEXT         NOT NULL DEFAULT 'system',
            update_time TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            update_user TEXT         NOT NULL DEFAULT 'system',
            CONSTRAINT uq_inbound_route_did UNIQUE (did)
        )
    ''')

    # ── call_task 外呼任务定义表(不含执行态) ──
    op.execute(f'''
        CREATE TABLE {SCHEMA}.call_task (
            id                BIGSERIAL    PRIMARY KEY,
            tenant_id         TEXT         NOT NULL,
            name              TEXT         NOT NULL,
            prompt_id         BIGINT       NOT NULL REFERENCES {SCHEMA}.prompt_config(id),
            kb_ids            JSONB        NOT NULL DEFAULT '[]'::jsonb,
            status            TEXT         NOT NULL DEFAULT 'idle',
            concurrent_limit  INTEGER      NOT NULL DEFAULT 1,
            allowed_hours     TEXT,
            redial_strategy   JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
            dept_id           TEXT,
            description       TEXT,
            create_time       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            create_user       TEXT         NOT NULL DEFAULT 'system',
            update_time       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            update_user       TEXT         NOT NULL DEFAULT 'system'
        )
    ''')
    op.execute(f'CREATE INDEX ix_call_task_tenant ON {SCHEMA}.call_task (tenant_id)')

    # ── inbound_route seed:对齐现有 dialplan(8001/8002/8003) ──
    op.execute(f'''
        INSERT INTO {SCHEMA}.inbound_route (did, tenant_id, biz_type, scenario, description) VALUES
        ('8001', 'default', 'customer_service', 'default', '客服呼入线'),
        ('8002', 'default', 'collection', 'default', '催收呼入线'),
        ('8003', 'default', 'marketing', 'default', '营销呼入线')
    ''')


def downgrade() -> None:
    op.execute(f'DROP TABLE IF EXISTS {SCHEMA}.call_task')
    op.execute(f'DROP TABLE IF EXISTS {SCHEMA}.inbound_route')
    op.execute(f'DROP TABLE IF EXISTS {SCHEMA}.prompt_version')
    op.execute(f'ALTER TABLE {SCHEMA}.call_session DROP COLUMN IF EXISTS scenario')
    op.execute(f'ALTER TABLE {SCHEMA}.call_session DROP COLUMN IF EXISTS tenant_id')
    op.execute(f'DROP INDEX IF EXISTS {SCHEMA}.ix_prompt_config_tenant_biz')
    op.execute(f'ALTER TABLE {SCHEMA}.prompt_config DROP CONSTRAINT IF EXISTS uq_prompt_config_tenant_biz_scenario')
    op.execute(f'ALTER TABLE {SCHEMA}.prompt_config DROP COLUMN IF EXISTS scenario')
    op.execute(f'ALTER TABLE {SCHEMA}.prompt_config RENAME COLUMN tenant_id TO biz_system')
    op.execute(
        f'ALTER TABLE {SCHEMA}.prompt_config '
        f'ADD CONSTRAINT uq_prompt_config_system_type UNIQUE (biz_system, biz_type)'
    )
