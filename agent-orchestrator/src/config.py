"""应用配置 - pydantic-settings，环境变量覆盖"""
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """智能外呼系统配置"""

    # PostgreSQL (asyncpg)
    pg_dsn: str = "postgresql+asyncpg://postgres@127.0.0.1:5432/callbot"
    pg_pool_size: int = 10
    pg_max_overflow: int = 20

    # Redis
    redis_url: str = "redis://127.0.0.1:6379/0"

    # MinIO
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "admin"
    minio_secret_key: str = "changeme123"

    # TTS adapter
    tts_adapter_url: str = "http://127.0.0.1:8081"

    # 业务
    biz_types: list[str] = Field(
        default=["customer_service", "collection", "marketing"]
    )

    # 超时
    llm_timeout_sec: float = 3.0

    # LLM
    llm_engine: str = "qwen"
    llm_base_url: str = "http://127.0.0.1:8080/v1"
    llm_model: str = "qwen3.5-9b"
    llm_embedding_model: str = "text-embedding-v3"

    # MCP
    mcp_server_url: str = "http://127.0.0.1:9090/mcp/"
    mcp_transport: str = "http"

    # RAG
    rag_top_k: int = 3
    rag_similarity_threshold: float = 0.7
    rag_max_retries: int = 2

    model_config = {"env_prefix": "CALLBOT_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
