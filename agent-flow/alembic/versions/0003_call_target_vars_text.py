"""call_target.vars JSONB → TEXT（key:value|key:value 字符串格式）

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-26

0002 把 vars 建成 JSONB；本期改为 key:value|key:value 纯字符串（不使用 JSON），
由 agent-flow render.parse_call_target_vars() 解析、console serializeVars() 序列化入库。
存量行 vars（默认 '{}'）回填 ''，非空 JSONB 退化为原文本（本期无此类数据）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = 'callbot'


def upgrade() -> None:
    op.alter_column(
        'call_target',
        'vars',
        type_=sa.Text(),
        postgresql_using="CASE WHEN vars = '{}'::jsonb THEN '' ELSE vars::text END",
        existing_nullable=False,
        schema=SCHEMA,
    )
    # 显式重置默认值（alter_column 的 server_default 在类型变更时不可靠，单独 SET 最稳）
    op.execute("ALTER TABLE callbot.call_target ALTER COLUMN vars SET DEFAULT ''")
    op.execute(
        "COMMENT ON COLUMN callbot.call_target.vars IS "
        "'每号码 render 变量，格式 key:value|key:value（TEXT），摘机后 parse_call_target_vars 解析进 graph state call_task_vars'"
    )


def downgrade() -> None:
    # TEXT→JSONB 无法可靠还原 kv 字符串，统一退回 '{}'（best-effort，可能丢变量）
    op.alter_column(
        'call_target',
        'vars',
        type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="'{}'::jsonb",
        postgresql_server_default=sa.text("'{}'::jsonb"),
        existing_nullable=False,
        schema=SCHEMA,
    )
