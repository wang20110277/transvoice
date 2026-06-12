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

    # Media (uuid_audio_fork 双向音频)
    media_sample_rate: int = 16000  # 16kHz HD Voice, FreeSWITCH internal resampling
    media_ws_host: str = "127.0.0.1"
    media_ws_port: int = 8000

    # RAG
    rag_top_k: int = 3
    rag_similarity_threshold: float = 0.7
    rag_max_retries: int = 2

    # Audio temp
    temp_dir: str = "/tmp/aiphone_tts"

    # VAD engine: "webrtc" (default, lightweight + RMS gating) or "silero" (neural network, needs louder audio)
    vad_type: str = "webrtc"

    # VAD — WebRTC params
    vad_aggressiveness: int = 3
    vad_silence_frames: int = 15

    # VAD — Silero params
    vad_silero_threshold: float = 0.3
    vad_silero_min_silence_ms: int = 300

    # VAD — common
    vad_min_audio_bytes: int = 3200
    # VAD RMS threshold: frame energy below this is treated as silence (filters SIP line noise)
    # 0 = disabled (WebRTC VAD only), 300 = match barge-in threshold
    vad_rms_threshold: float = 300.0

    # Barge-in
    barge_in_min_audio_bytes: int = 1600

    # VAD cooldown after barge-in (seconds): discard residual audio to prevent false positives
    vad_cooldown_after_bargein: float = 0.5

    # Jitter Buffer
    jitter_target_depth: int = 3
    jitter_max_depth: int = 10

    # Denoising (pre-VAD): "", "highpass", "noisereduce", "rnnoise"
    denoise_enabled: str = ""
    denoise_highpass_cutoff: float = 200.0

    # Audio gain (pre-ASR amplification for quiet SIP audio)
    audio_gain: float = 1.0

    # WebRTC AEC + NS + AGC (audio_processing.py) — 替换 denoise + 固定增益
    aec_enabled: bool = False
    aec_type: int = 2  # 1=AECM(移动端), 2=老AEC (AEC3 源码注释不可用)
    aec_ns_level: int = 2  # NS 抑制等级 0-3
    aec_agc_type: int = 1  # 0=关, 1=AdaptiveDigital, 2=AdaptiveAnalog
    aec_system_delay_ms: int = 80  # 回声延迟先验(毫秒)，has_echo 监控后标定

    # TTS skip (local testing without GPU)
    tts_skip: bool = False

    # ASR gRPC streaming
    asr_grpc_target: str = "127.0.0.1:50051"
    asr_use_grpc: bool = False

    # TTS gRPC streaming
    tts_grpc_target: str = "127.0.0.1:50052"
    tts_use_grpc: bool = False

    # ASR WebSocket streaming
    asr_use_ws: bool = False
    asr_ws_url: str = "ws://127.0.0.1:8080/ws/asr/streaming-recognize"

    # TTS WebSocket streaming
    tts_use_ws: bool = False
    tts_ws_url: str = "ws://127.0.0.1:8081/ws/tts/streaming-synthesize"

    # Streaming ASR (engine-level streaming, requires streaming-capable engine)
    asr_streaming_enabled: bool = False

    # Streaming TTS (chunk-level streaming, requires CosyVoice stream=True)
    tts_streaming_enabled: bool = False

    # TTS pre-buffering: accumulate N 30ms frames before starting playback
    # 0 = no pre-buffering, 10 = 300ms latency for smoother inter-sentence output
    tts_prebuffer_frames: int = 0

    # Sentence splitter tuning (streaming optimization)
    splitter_min_length: int = 2
    splitter_flush_timeout: float = 0.2
    splitter_eager_first: bool = True

    model_config = {"env_prefix": "CALLBOT_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
