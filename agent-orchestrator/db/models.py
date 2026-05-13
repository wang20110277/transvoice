"""SQLAlchemy 2.0 ORM 模型 - 智能外呼系统全表定义"""
from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, Integer, String, Text,
    CheckConstraint, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from pgvector.sqlalchemy import Vector


class Base(DeclarativeBase):
    """ORM 基类"""
    pass


class CallSession(Base):
    """通话会话事实表 - 记录每次通话的全生命周期"""
    __tablename__ = "call_session"
    __table_args__ = (
        CheckConstraint(
            "biz_type IN ('customer_service','collection','marketing')",
            name="ck_call_session_biz_type",
        ),
        UniqueConstraint("call_id", name="uq_call_session_call_id"),
        Index("ix_call_session_user_time", "user_id", "start_ts"),
        Index("ix_call_session_biz_time", "biz_type", "start_ts"),
        Index("ix_call_session_user_biz_time", "user_id", "biz_type", "start_ts"),
        Index("ix_call_session_task_id", "task_id"),
        {"schema": "callbot", "comment": "通话会话事实表"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False, comment="用户ID（分片键）")
    call_id: Mapped[str] = mapped_column(UUID, nullable=False, comment="通话业务ID")
    fs_uuid: Mapped[str] = mapped_column(String(64), nullable=False, comment="FreeSWITCH通道UUID")
    biz_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="业务类型")
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", comment="外呼任务ID")
    phone_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="", comment="号码哈希")
    user_key: Mapped[str] = mapped_column(String(256), nullable=False, default="", comment="用户标识 composite key")
    start_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="通话开始时间")
    end_ts: Mapped[datetime | None] = mapped_column(DateTime, comment="通话结束时间")
    hangup_cause: Mapped[str | None] = mapped_column(String(64), comment="挂断原因")
    result_code: Mapped[str | None] = mapped_column(String(32), comment="结果编码")
    identity_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="身份是否已核身")
    recording_notice_played: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="录音告知是否已播放")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, comment="创建时间")
    create_user: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="创建人")
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment="更新时间")
    update_user: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="更新人")


class CallTurn(Base):
    """通话轮次表 - 记录每轮对话"""
    __tablename__ = "call_turn"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user','assistant','system','tool')",
            name="ck_call_turn_role",
        ),
        Index("ix_call_turn_call_id", "call_id"),
        Index("ix_call_turn_user_time", "user_id", "ts"),
        Index("ix_call_turn_user_biz_time", "user_id", "biz_type", "ts"),
        Index("ix_call_turn_biz_time", "biz_type", "ts"),
        {"schema": "callbot", "comment": "通话轮次表"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False, comment="用户ID（分片键）")
    call_id: Mapped[str] = mapped_column(UUID, nullable=False, comment="通话业务ID")
    fs_uuid: Mapped[str] = mapped_column(String(64), nullable=False, comment="FreeSWITCH通道UUID")
    biz_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="业务类型")
    user_key: Mapped[str] = mapped_column(String(256), nullable=False, default="", comment="用户标识")
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="角色 user/assistant/system/tool")
    text: Mapped[str] = mapped_column(Text, nullable=False, comment="文本内容")
    asr_conf: Mapped[float | None] = mapped_column(Float, comment="ASR置信度")
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, comment="时间戳")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, comment="创建时间")
    create_user: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="创建人")
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment="更新时间")
    update_user: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="更新人")


class CallEvent(Base):
    """通话事件表 - 记录状态变更与告警"""
    __tablename__ = "call_event"
    __table_args__ = (
        Index("ix_call_event_call_id", "call_id"),
        Index("ix_call_event_user_time", "user_id", "ts"),
        Index("ix_call_event_user_biz_time", "user_id", "biz_type", "ts"),
        Index("ix_call_event_biz_time", "biz_type", "ts"),
        {"schema": "callbot", "comment": "通话事件表"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False, comment="用户ID（分片键）")
    call_id: Mapped[str] = mapped_column(UUID, nullable=False, comment="通话业务ID")
    fs_uuid: Mapped[str] = mapped_column(String(64), nullable=False, comment="FreeSWITCH通道UUID")
    biz_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="业务类型")
    user_key: Mapped[str] = mapped_column(String(256), nullable=False, default="", comment="用户标识")
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, comment="事件类型")
    event_detail: Mapped[dict | None] = mapped_column(JSONB, comment="事件详情JSON")
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, comment="时间戳")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, comment="创建时间")
    create_user: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="创建人")
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment="更新时间")
    update_user: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="更新人")


class CallArtifact(Base):
    """通话产物表 - 音频文件与TTS产物"""
    __tablename__ = "call_artifact"
    __table_args__ = (
        CheckConstraint(
            "storage IN ('nas','minio')",
            name="ck_call_artifact_storage",
        ),
        Index("ix_call_artifact_call_id", "call_id"),
        Index("ix_call_artifact_user_time", "user_id", "ts"),
        Index("ix_call_artifact_user_biz_time", "user_id", "biz_type", "ts"),
        Index("ix_call_artifact_biz_time", "biz_type", "ts"),
        {"schema": "callbot", "comment": "通话产物表"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False, comment="用户ID（分片键）")
    call_id: Mapped[str] = mapped_column(UUID, nullable=False, comment="通话业务ID")
    fs_uuid: Mapped[str] = mapped_column(String(64), nullable=False, comment="FreeSWITCH通道UUID")
    biz_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="业务类型")
    user_key: Mapped[str] = mapped_column(String(256), nullable=False, default="", comment="用户标识")
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="产物类型 caller_wav/bot_wav/mix_wav/tts_wav/meta_json")
    storage: Mapped[str] = mapped_column(String(16), nullable=False, default="nas", comment="存储位置 nas/minio")
    path: Mapped[str] = mapped_column(Text, nullable=False, comment="文件路径")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, comment="文件大小")
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, comment="时间戳")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, comment="创建时间")
    create_user: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="创建人")
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment="更新时间")
    update_user: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="更新人")


class ConfigSnapshot(Base):
    """配置快照表 - 通话级配置冻结（不分片，存 callbot_0）"""
    __tablename__ = "config_snapshot"
    __table_args__ = (
        Index("ix_config_snapshot_user_call", "user_id", "call_id"),
        {"schema": "callbot", "comment": "配置快照表"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(UUID, nullable=False, comment="通话业务ID")
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="用户ID")
    biz_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="业务类型")
    prompt_yaml: Mapped[str | None] = mapped_column(Text, comment="系统提示词YAML快照")
    flow_version: Mapped[str | None] = mapped_column(String(32), comment="流程版本")
    tts_profile: Mapped[str | None] = mapped_column(String(64), comment="TTS音色配置")
    dialplan_vars: Mapped[dict | None] = mapped_column(JSONB, comment="拨号计划变量快照")
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, comment="时间戳")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, comment="创建时间")
    create_user: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="创建人")
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment="更新时间")
    update_user: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="更新人")


class UserMemoryFact(Base):
    """用户记忆事实表 - 结构化事实（mem0提取）"""
    __tablename__ = "user_memory_fact"
    __table_args__ = (
        UniqueConstraint("biz_type", "user_key", "fact_type", name="uq_user_memory_fact_biz_user_type"),
        Index("ix_user_memory_fact_user_time", "user_id", "last_seen_ts"),
        Index("ix_user_memory_fact_user_biz_time", "user_id", "biz_type", "last_seen_ts"),
        {"schema": "callbot", "comment": "用户记忆事实表"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False, comment="用户ID（分片键）")
    biz_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="业务类型")
    user_key: Mapped[str] = mapped_column(String(256), nullable=False, comment="用户标识")
    fact_type: Mapped[str] = mapped_column(String(64), nullable=False, comment="事实类型")
    fact_value: Mapped[dict] = mapped_column(JSONB, nullable=False, comment="事实内容JSON")
    first_seen_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, comment="首次发现时间")
    last_seen_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, comment="最近发现时间")
    expire_ts: Mapped[datetime | None] = mapped_column(DateTime, comment="过期时间")
    source_call_id: Mapped[str | None] = mapped_column(UUID, comment="来源通话ID")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, comment="创建时间")
    create_user: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="创建人")
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment="更新时间")
    update_user: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="更新人")


class UserMemoryVector(Base):
    """用户记忆向量表 - 对话摘要语义检索"""
    __tablename__ = "user_memory_vector"
    __table_args__ = (
        Index("ix_user_memory_vector_user_time", "user_id", "ts"),
        Index("ix_user_memory_vector_user_biz_time", "user_id", "biz_type", "ts"),
        {"schema": "callbot", "comment": "用户记忆向量表"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False, comment="用户ID（分片键）")
    biz_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="业务类型")
    user_key: Mapped[str] = mapped_column(String(256), nullable=False, comment="用户标识")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="对话摘要文本")
    embedding = mapped_column(Vector(1536), comment="向量嵌入")
    tags: Mapped[list | None] = mapped_column(JSONB, comment="标签列表")
    source_call_id: Mapped[str | None] = mapped_column(UUID, comment="来源通话ID")
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, comment="时间戳")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, comment="创建时间")
    create_user: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="创建人")
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment="更新时间")
    update_user: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="更新人")


class ScriptLibrary(Base):
    """话术知识库 - Agentic RAG 话术检索"""
    __tablename__ = "script_library"
    __table_args__ = (
        Index(
            "ix_script_library_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where="embedding IS NOT NULL AND is_active = TRUE",
        ),
        {"schema": "callbot", "comment": "话术知识库"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    biz_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="业务类型")
    scene: Mapped[str] = mapped_column(String(64), nullable=False, default="default", comment="场景标识")
    title: Mapped[str] = mapped_column(String(256), nullable=False, comment="话术标题")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="话术正文")
    tags: Mapped[list | None] = mapped_column(JSONB, comment="标签列表")
    embedding = mapped_column(Vector(1536), comment="向量嵌入")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否启用")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="版本号")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, comment="创建时间")
    create_user: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="创建人")
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment="更新时间")
    update_user: Mapped[str] = mapped_column(String(64), nullable=False, default="system", comment="更新人")
