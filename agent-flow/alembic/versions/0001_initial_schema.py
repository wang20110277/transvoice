"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = 'callbot'


def _comment(table: str, column: str, comment: str) -> None:
    op.execute(f"COMMENT ON COLUMN {SCHEMA}.{table}.{column} IS '{comment}'")


def upgrade() -> None:
    # --- extensions & schema ---
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.execute(f'CREATE SCHEMA IF NOT EXISTS {SCHEMA}')

    # ============================================================
    # call_session — 通话会话事实表
    # ============================================================
    op.create_table(
        'call_session',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('call_id', postgresql.UUID(), nullable=False),
        sa.Column('fs_uuid', postgresql.UUID(), nullable=False),
        sa.Column('biz_type', sa.Text(), nullable=False),
        sa.Column('task_id', sa.Text(), nullable=True),
        sa.Column('phone_hash', sa.Text(), nullable=False),
        sa.Column('user_key', sa.Text(), nullable=False),
        sa.Column('phone_masked', sa.Text(), nullable=True),
        sa.Column('start_ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_ts', sa.DateTime(timezone=True), nullable=True),
        sa.Column('result_code', sa.Text(), nullable=True),
        sa.Column('hangup_cause', sa.Text(), nullable=True),
        sa.Column('identity_verified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('verify_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('recording_notice_played', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('create_time', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('create_user', sa.Text(), nullable=False, server_default="'system'"),
        sa.Column('update_time', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('update_user', sa.Text(), nullable=False, server_default="'system'"),
        sa.PrimaryKeyConstraint('id', 'user_id', name='pk_call_session'),
        sa.CheckConstraint(
            "biz_type IN ('customer_service','collection','marketing')",
            name='ck_call_session_biz_type',
        ),
        sa.UniqueConstraint('call_id', name='uq_call_session_call_id'),
        schema=SCHEMA,
    )
    op.create_index('ix_call_session_user_start', 'call_session', ['user_id', 'start_ts'], schema=SCHEMA)
    op.create_index('ix_call_session_biz_start', 'call_session', ['biz_type', 'start_ts'], schema=SCHEMA)
    op.create_index('ix_call_session_user_biz_start', 'call_session', ['user_id', 'biz_type', 'start_ts'], schema=SCHEMA)
    op.create_index('ix_call_session_task_start', 'call_session', ['biz_type', 'task_id', 'start_ts'], schema=SCHEMA)

    op.execute(f"COMMENT ON TABLE {SCHEMA}.call_session IS '通话会话事实表'")
    _comment('call_session', 'id', '自增主键')
    _comment('call_session', 'user_id', '用户ID，分片键')
    _comment('call_session', 'call_id', '通话唯一标识(UUID)')
    _comment('call_session', 'fs_uuid', 'FreeSWITCH 通道标识(UUID)')
    _comment('call_session', 'biz_type', '业务类型: customer_service/collection/marketing')
    _comment('call_session', 'task_id', '外呼任务ID')
    _comment('call_session', 'phone_hash', '手机号哈希')
    _comment('call_session', 'user_key', '用户唯一标识(脱敏手机号+身份证后四位)')
    _comment('call_session', 'phone_masked', '脱敏手机号(138****1234)')
    _comment('call_session', 'start_ts', '通话开始时间')
    _comment('call_session', 'end_ts', '通话结束时间')
    _comment('call_session', 'result_code', '通话结果编码')
    _comment('call_session', 'hangup_cause', '挂机原因')
    _comment('call_session', 'identity_verified', '是否已完成身份验证')
    _comment('call_session', 'verify_attempts', '身份验证尝试次数')
    _comment('call_session', 'recording_notice_played', '是否已播放录音告知')
    _comment('call_session', 'create_time', '记录创建时间')
    _comment('call_session', 'create_user', '记录创建人')
    _comment('call_session', 'update_time', '记录更新时间')
    _comment('call_session', 'update_user', '记录更新人')

    # ============================================================
    # call_turn — 逐轮对话表
    # ============================================================
    op.create_table(
        'call_turn',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('call_id', postgresql.UUID(), nullable=False),
        sa.Column('fs_uuid', postgresql.UUID(), nullable=False),
        sa.Column('biz_type', sa.Text(), nullable=False),
        sa.Column('user_key', sa.Text(), nullable=False),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('asr_conf', sa.Float(), nullable=True),
        sa.Column('start_ms', sa.Integer(), nullable=True),
        sa.Column('end_ms', sa.Integer(), nullable=True),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('create_time', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('create_user', sa.Text(), nullable=False, server_default="'system'"),
        sa.Column('update_time', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('update_user', sa.Text(), nullable=False, server_default="'system'"),
        sa.PrimaryKeyConstraint('id', 'user_id', name='pk_call_turn'),
        sa.CheckConstraint(
            "role IN ('user','assistant','system','tool')",
            name='ck_call_turn_role',
        ),
        schema=SCHEMA,
    )
    op.create_index('ix_call_turn_call', 'call_turn', ['call_id', 'ts'], schema=SCHEMA)
    op.create_index('ix_call_turn_user_ts', 'call_turn', ['user_id', 'ts'], schema=SCHEMA)
    op.create_index('ix_call_turn_user_biz_ts', 'call_turn', ['user_id', 'biz_type', 'ts'], schema=SCHEMA)

    op.execute(f"COMMENT ON TABLE {SCHEMA}.call_turn IS '逐轮对话表'")
    _comment('call_turn', 'id', '自增主键')
    _comment('call_turn', 'user_id', '用户ID，分片键')
    _comment('call_turn', 'call_id', '通话唯一标识(UUID)')
    _comment('call_turn', 'fs_uuid', 'FreeSWITCH 通道标识(UUID)')
    _comment('call_turn', 'biz_type', '业务类型: customer_service/collection/marketing')
    _comment('call_turn', 'user_key', '用户唯一标识')
    _comment('call_turn', 'role', '对话角色: user/assistant/system/tool')
    _comment('call_turn', 'text', '对话文本内容')
    _comment('call_turn', 'asr_conf', 'ASR识别置信度')
    _comment('call_turn', 'start_ms', '音频起始偏移(毫秒)')
    _comment('call_turn', 'end_ms', '音频结束偏移(毫秒)')
    _comment('call_turn', 'ts', '对话发生时间')
    _comment('call_turn', 'create_time', '记录创建时间')
    _comment('call_turn', 'create_user', '记录创建人')
    _comment('call_turn', 'update_time', '记录更新时间')
    _comment('call_turn', 'update_user', '记录更新人')

    # ============================================================
    # call_event — 事件流表
    # ============================================================
    op.create_table(
        'call_event',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('call_id', postgresql.UUID(), nullable=False),
        sa.Column('fs_uuid', postgresql.UUID(), nullable=False),
        sa.Column('biz_type', sa.Text(), nullable=False),
        sa.Column('user_key', sa.Text(), nullable=False),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('payload', postgresql.JSONB(), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('create_time', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('create_user', sa.Text(), nullable=False, server_default="'system'"),
        sa.Column('update_time', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('update_user', sa.Text(), nullable=False, server_default="'system'"),
        sa.PrimaryKeyConstraint('id', 'user_id', name='pk_call_event'),
        schema=SCHEMA,
    )
    op.create_index('ix_call_event_call', 'call_event', ['call_id', 'ts'], schema=SCHEMA)
    op.create_index('ix_call_event_user_ts', 'call_event', ['user_id', 'ts'], schema=SCHEMA)
    op.create_index('ix_call_event_user_biz_ts', 'call_event', ['user_id', 'biz_type', 'ts'], schema=SCHEMA)
    op.create_index('ix_call_event_type_ts', 'call_event', ['biz_type', 'event_type', 'ts'], schema=SCHEMA)

    op.execute(f"COMMENT ON TABLE {SCHEMA}.call_event IS '事件流表'")
    _comment('call_event', 'id', '自增主键')
    _comment('call_event', 'user_id', '用户ID，分片键')
    _comment('call_event', 'call_id', '通话唯一标识(UUID)')
    _comment('call_event', 'fs_uuid', 'FreeSWITCH 通道标识(UUID)')
    _comment('call_event', 'biz_type', '业务类型: customer_service/collection/marketing')
    _comment('call_event', 'user_key', '用户唯一标识')
    _comment('call_event', 'event_type', '事件类型(如 dtmf, transfer, hangup 等)')
    _comment('call_event', 'payload', '事件详情(JSON)')
    _comment('call_event', 'ts', '事件发生时间')
    _comment('call_event', 'create_time', '记录创建时间')
    _comment('call_event', 'create_user', '记录创建人')
    _comment('call_event', 'update_time', '记录更新时间')
    _comment('call_event', 'update_user', '记录更新人')

    # ============================================================
    # call_artifact — 录音/音频产物表
    # ============================================================
    op.create_table(
        'call_artifact',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('call_id', postgresql.UUID(), nullable=False),
        sa.Column('fs_uuid', postgresql.UUID(), nullable=False),
        sa.Column('biz_type', sa.Text(), nullable=False),
        sa.Column('user_key', sa.Text(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('storage', sa.Text(), nullable=False),
        sa.Column('uri', sa.Text(), nullable=False),
        sa.Column('sha256', sa.Text(), nullable=True),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('content_type', sa.Text(), nullable=True),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('create_time', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('create_user', sa.Text(), nullable=False, server_default="'system'"),
        sa.Column('update_time', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('update_user', sa.Text(), nullable=False, server_default="'system'"),
        sa.PrimaryKeyConstraint('id', 'user_id', name='pk_call_artifact'),
        sa.CheckConstraint("storage IN ('nas','minio')", name='ck_call_artifact_storage'),
        schema=SCHEMA,
    )
    op.create_index('ix_artifact_call', 'call_artifact', ['call_id', 'kind'], schema=SCHEMA)
    op.create_index('ix_artifact_user_ts', 'call_artifact', ['user_id', 'ts'], schema=SCHEMA)
    op.create_index('ix_artifact_biz_ts', 'call_artifact', ['biz_type', 'ts'], schema=SCHEMA)

    op.execute(f"COMMENT ON TABLE {SCHEMA}.call_artifact IS '录音/音频产物表'")
    _comment('call_artifact', 'id', '自增主键')
    _comment('call_artifact', 'user_id', '用户ID，分片键')
    _comment('call_artifact', 'call_id', '通话唯一标识(UUID)')
    _comment('call_artifact', 'fs_uuid', 'FreeSWITCH 通道标识(UUID)')
    _comment('call_artifact', 'biz_type', '业务类型: customer_service/collection/marketing')
    _comment('call_artifact', 'user_key', '用户唯一标识')
    _comment('call_artifact', 'kind', '产物类型(如 recording, tts_audio 等)')
    _comment('call_artifact', 'storage', '存储类型: nas/minio')
    _comment('call_artifact', 'uri', '存储路径/对象键')
    _comment('call_artifact', 'sha256', '文件SHA256校验值')
    _comment('call_artifact', 'size_bytes', '文件大小(字节)')
    _comment('call_artifact', 'content_type', 'MIME类型(如 audio/wav)')
    _comment('call_artifact', 'ts', '产物生成时间')
    _comment('call_artifact', 'create_time', '记录创建时间')
    _comment('call_artifact', 'create_user', '记录创建人')
    _comment('call_artifact', 'update_time', '记录更新时间')
    _comment('call_artifact', 'update_user', '记录更新人')

    # ============================================================
    # config_snapshot — 配置快照表（不分片）
    # ============================================================
    op.create_table(
        'config_snapshot',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('call_id', postgresql.UUID(), nullable=False),
        sa.Column('fs_uuid', postgresql.UUID(), nullable=False),
        sa.Column('biz_type', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('user_key', sa.Text(), nullable=False),
        sa.Column('prompt_version', sa.Text(), nullable=True),
        sa.Column('flow_version', sa.Text(), nullable=True),
        sa.Column('tts_profile_version', sa.Text(), nullable=True),
        sa.Column('dialplan_version', sa.Text(), nullable=True),
        sa.Column('snapshot', postgresql.JSONB(), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('create_time', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('create_user', sa.Text(), nullable=False, server_default="'system'"),
        sa.Column('update_time', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('update_user', sa.Text(), nullable=False, server_default="'system'"),
        sa.PrimaryKeyConstraint('id'),
        schema=SCHEMA,
    )
    op.create_index('ix_snapshot_call', 'config_snapshot', ['call_id', 'ts'], schema=SCHEMA)
    op.create_index('ix_snapshot_user_ts', 'config_snapshot', ['user_id', 'ts'], schema=SCHEMA)

    op.execute(f"COMMENT ON TABLE {SCHEMA}.config_snapshot IS '配置快照表（不分片）'")
    _comment('config_snapshot', 'id', '自增主键')
    _comment('config_snapshot', 'call_id', '通话唯一标识(UUID)')
    _comment('config_snapshot', 'fs_uuid', 'FreeSWITCH 通道标识(UUID)')
    _comment('config_snapshot', 'biz_type', '业务类型: customer_service/collection/marketing')
    _comment('config_snapshot', 'user_id', '用户ID')
    _comment('config_snapshot', 'user_key', '用户唯一标识')
    _comment('config_snapshot', 'prompt_version', 'Prompt模板版本号')
    _comment('config_snapshot', 'flow_version', '流程图版本号')
    _comment('config_snapshot', 'tts_profile_version', 'TTS语音配置版本号')
    _comment('config_snapshot', 'dialplan_version', '拨号计划版本号')
    _comment('config_snapshot', 'snapshot', '配置快照内容(JSON)')
    _comment('config_snapshot', 'ts', '快照时间')
    _comment('config_snapshot', 'create_time', '记录创建时间')
    _comment('config_snapshot', 'create_user', '记录创建人')
    _comment('config_snapshot', 'update_time', '记录更新时间')
    _comment('config_snapshot', 'update_user', '记录更新人')

    # ============================================================
    # user_memory_fact — 结构化记忆表（mem0 facts）
    # ============================================================
    op.create_table(
        'user_memory_fact',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('biz_type', sa.Text(), nullable=False),
        sa.Column('user_key', sa.Text(), nullable=False),
        sa.Column('fact_type', sa.Text(), nullable=False),
        sa.Column('fact_value', postgresql.JSONB(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('first_seen_ts', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('last_seen_ts', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('source_call_id', postgresql.UUID(), nullable=True),
        sa.Column('expire_ts', sa.DateTime(timezone=True), nullable=True),
        sa.Column('create_time', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('create_user', sa.Text(), nullable=False, server_default="'system'"),
        sa.Column('update_time', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('update_user', sa.Text(), nullable=False, server_default="'system'"),
        sa.PrimaryKeyConstraint('id', 'user_id', name='pk_user_memory_fact'),
        schema=SCHEMA,
    )
    op.create_index('ix_mem_fact_user', 'user_memory_fact', ['user_id', 'fact_type'], schema=SCHEMA)
    op.create_index('ix_mem_fact_user_biz', 'user_memory_fact', ['user_id', 'biz_type'], schema=SCHEMA)
    op.create_index('ix_mem_fact_lastseen', 'user_memory_fact', ['biz_type', 'user_key', 'last_seen_ts'], schema=SCHEMA)

    op.execute(f"COMMENT ON TABLE {SCHEMA}.user_memory_fact IS '结构化记忆表（mem0 facts）'")
    _comment('user_memory_fact', 'id', '自增主键')
    _comment('user_memory_fact', 'user_id', '用户ID，分片键')
    _comment('user_memory_fact', 'biz_type', '业务类型: customer_service/collection/marketing')
    _comment('user_memory_fact', 'user_key', '用户唯一标识')
    _comment('user_memory_fact', 'fact_type', '记忆事实类型(如 preference, personal_info 等)')
    _comment('user_memory_fact', 'fact_value', '记忆事实内容(JSON)')
    _comment('user_memory_fact', 'confidence', '置信度(0.0~1.0)')
    _comment('user_memory_fact', 'first_seen_ts', '首次发现时间')
    _comment('user_memory_fact', 'last_seen_ts', '最近确认时间')
    _comment('user_memory_fact', 'source_call_id', '来源通话ID(UUID)')
    _comment('user_memory_fact', 'expire_ts', '过期时间')
    _comment('user_memory_fact', 'create_time', '记录创建时间')
    _comment('user_memory_fact', 'create_user', '记录创建人')
    _comment('user_memory_fact', 'update_time', '记录更新时间')
    _comment('user_memory_fact', 'update_user', '记录更新人')

    # ============================================================
    # user_memory_vector — 向量记忆表（pgvector 语义检索）
    # ============================================================
    op.execute(f'''
        CREATE TABLE {SCHEMA}.user_memory_vector (
            id BIGSERIAL NOT NULL,
            user_id TEXT NOT NULL,
            biz_type TEXT NOT NULL,
            user_key TEXT NOT NULL,
            content TEXT NOT NULL,
            embedding vector(1536) NOT NULL,
            tags JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            source_call_id UUID,
            source_turn_id BIGINT,
            ts TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            create_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            create_user TEXT NOT NULL DEFAULT 'system',
            update_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            update_user TEXT NOT NULL DEFAULT 'system',
            CONSTRAINT pk_user_memory_vector PRIMARY KEY (id, user_id)
        )
    ''')
    op.create_index('ix_mem_vec_user_ts', 'user_memory_vector', ['user_id', 'ts'], schema=SCHEMA)
    op.create_index('ix_mem_vec_user_biz_ts', 'user_memory_vector', ['user_id', 'biz_type', 'ts'], schema=SCHEMA)

    op.execute(f"COMMENT ON TABLE {SCHEMA}.user_memory_vector IS '向量记忆表（pgvector 语义检索）'")
    _comment('user_memory_vector', 'id', '自增主键')
    _comment('user_memory_vector', 'user_id', '用户ID，分片键')
    _comment('user_memory_vector', 'biz_type', '业务类型: customer_service/collection/marketing')
    _comment('user_memory_vector', 'user_key', '用户唯一标识')
    _comment('user_memory_vector', 'content', '原始文本内容')
    _comment('user_memory_vector', 'embedding', '文本向量(1536维)')
    _comment('user_memory_vector', 'tags', '标签(JSON)')
    _comment('user_memory_vector', 'source_call_id', '来源通话ID(UUID)')
    _comment('user_memory_vector', 'source_turn_id', '来源对话轮次ID')
    _comment('user_memory_vector', 'ts', '记录时间')
    _comment('user_memory_vector', 'create_time', '记录创建时间')
    _comment('user_memory_vector', 'create_user', '记录创建人')
    _comment('user_memory_vector', 'update_time', '记录更新时间')
    _comment('user_memory_vector', 'update_user', '记录更新人')

    # ============================================================
    # script_library — 话术知识库（Agentic RAG，不分片）
    # ============================================================
    op.execute(f'''
        CREATE TABLE {SCHEMA}.script_library (
            id BIGSERIAL NOT NULL PRIMARY KEY,
            biz_type TEXT NOT NULL,
            scene TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            conditions JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            tags JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            embedding vector(1536),
            version INTEGER NOT NULL DEFAULT 1,
            is_active BOOLEAN NOT NULL DEFAULT true,
            create_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            create_user TEXT NOT NULL DEFAULT 'system',
            update_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            update_user TEXT NOT NULL DEFAULT 'system'
        )
    ''')
    op.create_index('ix_script_biz_scene', 'script_library', ['biz_type', 'scene'], schema=SCHEMA)
    op.create_index('ix_script_biz_version', 'script_library', ['biz_type', 'version'], schema=SCHEMA)

    op.execute(f"COMMENT ON TABLE {SCHEMA}.script_library IS '话术知识库（Agentic RAG，不分片）'")
    _comment('script_library', 'id', '自增主键')
    _comment('script_library', 'biz_type', '业务类型: customer_service/collection/marketing')
    _comment('script_library', 'scene', '话术场景(如 greeting, verification 等)')
    _comment('script_library', 'title', '话术标题')
    _comment('script_library', 'content', '话术正文内容')
    _comment('script_library', 'conditions', '适用条件(JSON)')
    _comment('script_library', 'tags', '标签(JSON)')
    _comment('script_library', 'embedding', '文本向量(1536维)，用于语义检索')
    _comment('script_library', 'version', '话术版本号')
    _comment('script_library', 'is_active', '是否启用')
    _comment('script_library', 'create_time', '记录创建时间')
    _comment('script_library', 'create_user', '记录创建人')
    _comment('script_library', 'update_time', '记录更新时间')
    _comment('script_library', 'update_user', '记录更新人')

    # ============================================================
    # 向量索引 — IVFFlat 余弦相似度
    # ============================================================
    op.execute(f'''
        CREATE INDEX ix_mem_vec_embedding
        ON {SCHEMA}.user_memory_vector
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)
    ''')
    op.execute(f'''
        CREATE INDEX ix_script_embedding
        ON {SCHEMA}.script_library
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)
    ''')


def downgrade() -> None:
    op.execute(f'DROP TABLE IF EXISTS {SCHEMA}.script_library')
    op.execute(f'DROP TABLE IF EXISTS {SCHEMA}.user_memory_vector')
    op.drop_table('user_memory_fact', schema=SCHEMA)
    op.drop_table('config_snapshot', schema=SCHEMA)
    op.drop_table('call_artifact', schema=SCHEMA)
    op.drop_table('call_event', schema=SCHEMA)
    op.drop_table('call_turn', schema=SCHEMA)
    op.drop_table('call_session', schema=SCHEMA)
    op.execute(f'DROP SCHEMA IF EXISTS {SCHEMA}')
