"""应用配置 - 基于 pydantic-settings，支持环境变量覆盖"""
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """智能外呼系统配置"""

    # FreeSWITCH ESL
    fs_esl_host: str = "127.0.0.1"
    fs_esl_port: int = 8021
    fs_esl_password: str = "ClueCon"

    # PostgreSQL (异步驱动用 asyncpg)
    pg_dsn: str = "postgresql+asyncpg://postgres@127.0.0.1:5432/callbot"
    pg_pool_size: int = 10
    pg_max_overflow: int = 20

    # Redis
    redis_url: str = "redis://127.0.0.1:6379/0"

    # MinIO
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "admin"
    minio_secret_key: str = "changeme123"

    # 业务
    biz_types: list[str] = Field(
        default=["customer_service", "collection", "marketing"]
    )
    handoff_extension: str = "1001"
    legal_notice_file: str = "/usr/local/freeswitch/sounds/legal_notice.wav"

    # 超时
    llm_timeout_sec: float = 3.0
    silence_timeout_sec: int = 5
    max_silence_count: int = 3

    # LLM
    llm_engine: str = "qwen"
    llm_base_url: str = "http://127.0.0.1:8080"
    llm_model: str = "qwen3.5-9b"
    llm_embedding_model: str = "text-embedding-v3"

    # MCP
    mcp_server_url: str = "http://127.0.0.1:9090"

    model_config = {"env_prefix": "CALLBOT_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
