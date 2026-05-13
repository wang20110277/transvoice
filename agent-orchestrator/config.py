import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    # FreeSWITCH ESL
    fs_esl_host: str = os.getenv("FS_ESL_HOST", "127.0.0.1")
    fs_esl_port: int = int(os.getenv("FS_ESL_PORT", "8021"))
    fs_esl_password: str = os.getenv("FS_ESL_PASSWORD", "ClueCon")

    # Redis
    redis_url: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

    # PostgreSQL
    pg_dsn: str = os.getenv("PG_DSN", "postgresql://postgres@127.0.0.1:5432/callbot")

    # MinIO
    minio_endpoint: str = os.getenv("MINIO_ENDPOINT", "127.0.0.1:9000")
    minio_access_key: str = os.getenv("MINIO_ACCESS_KEY", "admin")
    minio_secret_key: str = os.getenv("MINIO_SECRET_KEY", "changeme123")

    # 业务
    biz_types: list = field(default_factory=lambda: ["customer_service", "collection", "marketing"])
    handoff_extension: str = os.getenv("HANDOFF_EXTENSION", "1001")
    legal_notice_file: str = os.getenv("LEGAL_NOTICE_FILE", "/usr/local/freeswitch/sounds/legal_notice.wav")

    # 超时
    llm_timeout_sec: float = float(os.getenv("LLM_TIMEOUT_SEC", "3.0"))
    silence_timeout_sec: int = int(os.getenv("SILENCE_TIMEOUT_SEC", "5"))
    max_silence_count: int = int(os.getenv("MAX_SILENCE_COUNT", "3"))

    # LLM
    llm_engine: str = os.getenv("LLM_ENGINE", "qwen")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080")

    # MCP
    mcp_server_url: str = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:9090")


settings = Settings()
