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

    # ASR adapter
    asr_adapter_url: str = "http://127.0.0.1:8080"

    # TTS adapter
    tts_adapter_url: str = "http://127.0.0.1:8081"

    # 业务
    biz_types: list[str] = Field(
        default=["customer_service", "collection", "marketing"]
    )

    # 超时
    llm_timeout_sec: float = 30.0

    # LLM
    llm_device: str = "cpu"  # cpu=Ollama, gpu=vLLM
    llm_base_url: str = "http://127.0.0.1:8083/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen3.5:9b"
    llm_embedding_model: str = "nomic-embed-text"

    # MCP
    mcp_server_url: str = "http://127.0.0.1:9090/mcp"
    mcp_transport: str = "http"

    # ESL (FreeSWITCH Event Socket)
    esl_host: str = "127.0.0.1"
    esl_port: int = 8021
    esl_password: str = "ClueCon"
    handoff_extension: str = "1001"

    # RAG
    rag_top_k: int = 3
    rag_similarity_threshold: float = 0.7
    rag_max_retries: int = 2

    # Audio temp
    temp_dir: str = "/tmp/aiphone_tts"

    # VAD (WebRTC)
    vad_aggressiveness: int = 3
    vad_silence_frames: int = 15
    vad_min_audio_bytes: int = 3200

    # Barge-in
    barge_in_min_audio_bytes: int = 1600

    # Jitter Buffer
    jitter_target_depth: int = 3
    jitter_max_depth: int = 10

    # Denoising (pre-VAD): "", "highpass", "noisereduce", "rnnoise"
    denoise_enabled: str = ""
    denoise_highpass_cutoff: float = 200.0

    # TTS skip (local testing without GPU)
    tts_skip: bool = False

    # ASR gRPC streaming
    asr_grpc_target: str = "127.0.0.1:50051"
    asr_use_grpc: bool = False

    # TTS gRPC streaming
    tts_grpc_target: str = "127.0.0.1:50052"
    tts_use_grpc: bool = False

    model_config = {"env_prefix": "CALLBOT_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
