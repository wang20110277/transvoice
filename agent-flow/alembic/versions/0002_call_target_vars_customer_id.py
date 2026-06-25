"""call_target vars + customer_id（结构化导入 + 每号码变量渲染）

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-25

call_target 加两列：
  - vars JSONB NOT NULL DEFAULT '{}'::jsonb —— 每号码 render 变量负载（摘机后透传进
    graph state call_task_vars，由现有 render() 渲染进各自话术）。
  - customer_id TEXT —— 结构化导入的「客户id」列，仅展示/审计，不进渲染。
存量行 vars 回填 '{}'，customer_id 可空，兼容。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = 'callbot'


def upgrade() -> None:
    op.add_column(
        'call_target',
        sa.Column(
            'vars',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        'call_target',
        sa.Column('customer_id', sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.execute("COMMENT ON COLUMN callbot.call_target.vars IS '每号码 render 变量（JSONB），摘机后透传进 graph state call_task_vars'")
    op.execute("COMMENT ON COLUMN callbot.call_target.customer_id IS '结构化导入客户id，仅展示/审计，不进渲染'")


def downgrade() -> None:
    op.drop_column('call_target', 'customer_id', schema=SCHEMA)
    op.drop_column('call_target', 'vars', schema=SCHEMA)
