"""prompt_config table + seed data

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-11
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
    op.execute(f'''
        CREATE TABLE {SCHEMA}.prompt_config (
            id          BIGSERIAL    PRIMARY KEY,
            biz_system  TEXT         NOT NULL DEFAULT 'default',
            biz_type    TEXT         NOT NULL,
            system_prompt TEXT       NOT NULL,
            max_reply_length INTEGER NOT NULL DEFAULT 80,
            extra       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
            is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
            version     INTEGER      NOT NULL DEFAULT 1,
            description TEXT,
            create_time TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            create_user TEXT         NOT NULL DEFAULT 'system',
            update_time TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            update_user TEXT         NOT NULL DEFAULT 'system',

            CONSTRAINT uq_prompt_config_system_type UNIQUE (biz_system, biz_type)
        )
    ''')

    op.execute(f'CREATE INDEX ix_prompt_config_biz_type ON {SCHEMA}.prompt_config (biz_type)')

    # 初始数据：从现有 YAML 提示词导入
    op.execute(f'''
        INSERT INTO {SCHEMA}.prompt_config (biz_system, biz_type, system_prompt, max_reply_length, description) VALUES
        ('default', 'customer_service',
         '你是一名电话客服AI助手。语气温柔、专业、有耐心。
回答用户问题，必要时转接人工。始终使用中文回复。

【语音对话规则】
- 每次回复不超过80字，口语化表达，禁止使用 markdown 格式（不要用标题、列表、代码块、加粗等）
- 一次只说一到两句话，说完后等待用户回应
- 不要主动罗列多项内容，用户问什么答什么
- 直接回复用户问题，不要自言自语或思考',
         80, '客服场景默认提示词'),

        ('default', 'collection',
         '你是一名催收专员AI助手。语气专业、不威胁。
严格规则：仅在身份核验通过后才能提及具体欠款金额。
每次回复不超过50字。始终使用中文回复。

【语音对话规则】
- 口语化表达，禁止使用 markdown 格式
- 直接回复用户问题，不要自言自语或思考',
         50, '催收场景默认提示词'),

        ('default', 'marketing',
         '你是一名营销AI助手。语气热情、活力、有感染力。
介绍产品优势，引导用户兴趣。每次回复不超过80字。始终使用中文回复。

【语音对话规则】
- 口语化表达，禁止使用 markdown 格式
- 一次只说一到两句话，说完后等待用户回应
- 直接回复用户问题，不要自言自语或思考',
         80, '营销场景默认提示词')
    ''')


def downgrade() -> None:
    op.execute(f'DROP TABLE IF EXISTS {SCHEMA}.prompt_config')
