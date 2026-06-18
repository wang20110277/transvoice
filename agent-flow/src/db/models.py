"""SQLAlchemy 2.0 ORM 模型 - 与 init_db.sql 精确对齐"""
from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, Integer, String, Text, func,
    CheckConstraint, ForeignKey, Index, UniqueConstraint, PrimaryKeyConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from pgvector.sqlalchemy import Vector


class Base(DeclarativeBase):
    pass


class CallSession(Base):
    """通话会话事实表"""
    __tablename__ = "call_session"
    __table_args__ = (
        PrimaryKeyConstraint("id", "user_id", name="pk_call_session"),
        CheckConstraint(
            "biz_type IN ('customer_service','collection','marketing')",
            name="ck_call_session_biz_type",
        ),
        UniqueConstraint("call_id", name="uq_call_session_call_id"),
        Index("ix_call_session_user_start", "user_id", "start_ts"),
        Index("ix_call_session_biz_start", "biz_type", "start_ts"),
        Index("ix_call_session_user_biz_start", "user_id", "biz_type", "start_ts"),
        Index("ix_call_session_task_start", "biz_type", "task_id", "start_ts"),
        {"schema": "callbot"},
    )

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    call_id: Mapped[str] = mapped_column(UUID, nullable=False)
    fs_uuid: Mapped[str] = mapped_column(UUID, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(Text)
    biz_type: Mapped[str] = mapped_column(Text, nullable=False)
    scenario: Mapped[str | None] = mapped_column(Text)
    task_id: Mapped[str | None] = mapped_column(Text)
    phone_hash: Mapped[str] = mapped_column(Text, nullable=False)
    user_key: Mapped[str] = mapped_column(Text, nullable=False)
    phone_masked: Mapped[str | None] = mapped_column(Text)
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_code: Mapped[str | None] = mapped_column(Text)
    hangup_cause: Mapped[str | None] = mapped_column(Text)
    identity_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verify_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recording_notice_played: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    create_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    update_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")


class CallTurn(Base):
    """逐轮对话表"""
    __tablename__ = "call_turn"
    __table_args__ = (
        PrimaryKeyConstraint("id", "user_id", name="pk_call_turn"),
        CheckConstraint(
            "role IN ('user','assistant','system','tool')",
            name="ck_call_turn_role",
        ),
        Index("ix_call_turn_call", "call_id", "ts"),
        Index("ix_call_turn_user_ts", "user_id", "ts"),
        Index("ix_call_turn_user_biz_ts", "user_id", "biz_type", "ts"),
        {"schema": "callbot"},
    )

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    call_id: Mapped[str] = mapped_column(UUID, nullable=False)
    fs_uuid: Mapped[str] = mapped_column(UUID, nullable=False)
    biz_type: Mapped[str] = mapped_column(Text, nullable=False)
    user_key: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    asr_conf: Mapped[float | None] = mapped_column(Float)
    start_ms: Mapped[int | None] = mapped_column(Integer)
    end_ms: Mapped[int | None] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    create_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    update_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")


class CallEvent(Base):
    """事件流表"""
    __tablename__ = "call_event"
    __table_args__ = (
        PrimaryKeyConstraint("id", "user_id", name="pk_call_event"),
        Index("ix_call_event_call", "call_id", "ts"),
        Index("ix_call_event_user_ts", "user_id", "ts"),
        Index("ix_call_event_user_biz_ts", "user_id", "biz_type", "ts"),
        Index("ix_call_event_type_ts", "biz_type", "event_type", "ts"),
        {"schema": "callbot"},
    )

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    call_id: Mapped[str] = mapped_column(UUID, nullable=False)
    fs_uuid: Mapped[str] = mapped_column(UUID, nullable=False)
    biz_type: Mapped[str] = mapped_column(Text, nullable=False)
    user_key: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    create_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    update_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")


class CallArtifact(Base):
    """录音/音频产物表"""
    __tablename__ = "call_artifact"
    __table_args__ = (
        PrimaryKeyConstraint("id", "user_id", name="pk_call_artifact"),
        CheckConstraint("storage IN ('nas','minio')", name="ck_call_artifact_storage"),
        Index("ix_artifact_call", "call_id", "kind"),
        Index("ix_artifact_user_ts", "user_id", "ts"),
        Index("ix_artifact_biz_ts", "biz_type", "ts"),
        {"schema": "callbot"},
    )

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    call_id: Mapped[str] = mapped_column(UUID, nullable=False)
    fs_uuid: Mapped[str] = mapped_column(UUID, nullable=False)
    biz_type: Mapped[str] = mapped_column(Text, nullable=False)
    user_key: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    storage: Mapped[str] = mapped_column(Text, nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    content_type: Mapped[str | None] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    create_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    update_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")


class ConfigSnapshot(Base):
    """配置快照表（不分片）"""
    __tablename__ = "config_snapshot"
    __table_args__ = (
        Index("ix_snapshot_call", "call_id", "ts"),
        Index("ix_snapshot_user_ts", "user_id", "ts"),
        {"schema": "callbot"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(UUID, nullable=False)
    fs_uuid: Mapped[str] = mapped_column(UUID, nullable=False)
    biz_type: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_key: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    flow_version: Mapped[str | None] = mapped_column(Text)
    tts_profile_version: Mapped[str | None] = mapped_column(Text)
    dialplan_version: Mapped[str | None] = mapped_column(Text)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    create_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    update_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")


class UserMemoryFact(Base):
    """结构化记忆表（mem0 facts）"""
    __tablename__ = "user_memory_fact"
    __table_args__ = (
        PrimaryKeyConstraint("id", "user_id", name="pk_user_memory_fact"),
        Index("ix_mem_fact_user", "user_id", "fact_type"),
        Index("ix_mem_fact_user_biz", "user_id", "biz_type"),
        Index("ix_mem_fact_lastseen", "biz_type", "user_key", "last_seen_ts"),
        {"schema": "callbot"},
    )

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    biz_type: Mapped[str] = mapped_column(Text, nullable=False)
    user_key: Mapped[str] = mapped_column(Text, nullable=False)
    fact_type: Mapped[str] = mapped_column(Text, nullable=False)
    fact_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    first_seen_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    last_seen_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    source_call_id: Mapped[str | None] = mapped_column(UUID)
    expire_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    create_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    update_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")


class UserMemoryVector(Base):
    """向量记忆表（pgvector 语义检索）"""
    __tablename__ = "user_memory_vector"
    __table_args__ = (
        PrimaryKeyConstraint("id", "user_id", name="pk_user_memory_vector"),
        Index("ix_mem_vec_user_ts", "user_id", "ts"),
        Index("ix_mem_vec_user_biz_ts", "user_id", "biz_type", "ts"),
        {"schema": "callbot"},
    )

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    biz_type: Mapped[str] = mapped_column(Text, nullable=False)
    user_key: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(1536), nullable=False)
    tags: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_call_id: Mapped[str | None] = mapped_column(UUID)
    source_turn_id: Mapped[int | None] = mapped_column(BigInteger)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    create_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    update_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")


class ScriptLibrary(Base):
    """话术知识库（Agentic RAG，不分片）"""
    __tablename__ = "script_library"
    __table_args__ = (
        Index("ix_script_biz_scene", "biz_type", "scene"),
        Index("ix_script_biz_version", "biz_type", "version"),
        {"schema": "callbot"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    biz_type: Mapped[str] = mapped_column(Text, nullable=False)
    scene: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    conditions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    tags: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    embedding = mapped_column(Vector(1536))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    create_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    update_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")


class PromptConfig(Base):
    """提示词配置表 — 按 (租户, 业务场景, 话术场景) 三维度管理系统提示词。

    Console(Drizzle)与 agent-flow(SQLAlchemy)共用本表,列名一致,杜绝双词汇表。
    """
    __tablename__ = "prompt_config"
    __table_args__ = (
        UniqueConstraint("tenant_id", "biz_type", "scenario", name="uq_prompt_config_tenant_biz_scenario"),
        Index("ix_prompt_config_tenant_biz", "tenant_id", "biz_type"),
        Index("ix_prompt_config_biz_type", "biz_type"),
        {"schema": "callbot"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, default="default")
    biz_type: Mapped[str] = mapped_column(Text, nullable=False)
    scenario: Mapped[str] = mapped_column(Text, nullable=False, default="default")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    max_reply_length: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str | None] = mapped_column(Text)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    create_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    update_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")


class PromptVersion(Base):
    """提示词版本快照表 — 支撑版本历史查看与回滚(跨重启持久化)。"""
    __tablename__ = "prompt_version"
    __table_args__ = (
        Index("ix_prompt_version_lookup", "tenant_id", "biz_type", "scenario", "version"),
        {"schema": "callbot"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    biz_type: Mapped[str] = mapped_column(Text, nullable=False)
    scenario: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    update_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class InboundRoute(Base):
    """呼入 DID 路由表 — 被叫号/号段 → (租户, 业务场景, 话术场景)。"""
    __tablename__ = "inbound_route"
    __table_args__ = (
        UniqueConstraint("did", name="uq_inbound_route_did"),
        {"schema": "callbot"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    did: Mapped[str] = mapped_column(Text, nullable=False)
    did_pattern: Mapped[str | None] = mapped_column(Text)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    biz_type: Mapped[str] = mapped_column(Text, nullable=False)
    scenario: Mapped[str] = mapped_column(Text, nullable=False, default="default")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[str | None] = mapped_column(Text)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    create_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    update_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")


class CallTask(Base):
    """外呼任务定义表 — 仅定义层(promptId 绑定 + 策略参数),不含执行态。

    originate/调度/重拨属独立后续变更;本期策略字段为声明性存储,无执行器消费。
    """
    __tablename__ = "call_task"
    __table_args__ = (
        Index("ix_call_task_tenant", "tenant_id"),
        {"schema": "callbot"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("callbot.prompt_config.id"), nullable=False)
    kb_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="idle")
    concurrent_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    allowed_hours: Mapped[str | None] = mapped_column(Text)
    redial_strategy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dept_id: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    create_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    update_user: Mapped[str] = mapped_column(Text, nullable=False, default="system")
