"""应用配置 - pydantic-settings，环境变量覆盖"""
import socket

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


def _detect_local_ip() -> str:
    """探测本机默认路由出口 IP（agent-flow 与 FS 同机时即 FS local_ip_v4 / SIP 注册域）。

    UDP connect 不实际发包，仅让内核依路由表选定源 IP——比 gethostbyname(gethostname())
    可靠（后者常返回 127.0.0.1 或 hostname 解析失败）。多网卡机器返回默认路由那张。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))  # 任意公网地址即可，无需可达
        return sock.getsockname()[0]
    finally:
        sock.close()


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
    minio_bucket: str = "audio-archive"
    minio_secure: bool = False

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

    # RMS 门禁(barge-in 低延迟语音检测,RMS+SNR 自适应底噪)
    # 帧能量低于 threshold 视为静音(过滤 SIP 底噪);snr_factor>0 时门限=noise_floor*snr_factor
    rms_gate_threshold: float = 300.0
    # 自适应噪声底噪:门限随环境底噪浮动(安静时低、嘈杂时抬高),解决固定门限在嘈杂环境失效
    rms_gate_snr_factor: float = 3.0
    # 初始噪声底噪估计(启动/换通话的 warm-up 基线)
    rms_gate_noise_floor_init: float = 300.0
    # 底噪 EMA 更新率(0-1,越大越快收敛);0.1 ≈ 1s 收敛
    rms_gate_noise_adapt_rate: float = 0.1

    # Barge-in
    barge_in_min_audio_bytes: int = 1600
    # Barge-in RMS 阈值:AEC 场景调高(过滤残留回声尖峰),默认 300;.env 实测调优 1500
    barge_in_rms_threshold: int = 300

    # Barge-in 后冷却(秒):丢弃残余音频防 RMS 误触发
    cooldown_after_bargein: float = 0.5

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

    # ASR WebSocket streaming(主传输)。
    # 注意:WS 是端点检测的唯一来源——服务端 FSMN-VAD 分段后回推 result,经 on_final 触发轮次。
    # HTTP ASR 传输无本地端点触发器,关闭 asr_use_ws 会导致无轮次启动(仅批量 ASR 可用)。
    asr_use_ws: bool = True
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

    # 录音归档（FS record_session 写入路径，agent-flow 读取路径）
    recordings_dir: str = "/Users/lindaw/freeswitch/var/lib/freeswitch/recordings"
    recording_notice_enabled: bool = True
    recording_archive_timeout: int = 30
    # 挂断后间隔秒数再上传录音（等 FS flush 完 wav）；用户要求 3 秒
    recording_archive_delay_sec: int = 3
    recording_notice_sound: str = "ivr/recording_notice.wav"

    # ── 外呼执行引擎 — 测试模式(当前,呼内部分机) / 生产模式(呼真实手机号)切换 ──
    # 【测试模式 · 当前】softphone 注册到 FS internal profile，直拨分机号
    #   endpoint_template = user/{phone}@{domain}   {phone}=分机号(如1000)  {domain}=outbound_domain(本机IP)
    #   caller_id 可空；前提：freeswitch/sip_profiles/internal.xml + 软电话已注册
    # 【生产模式 · 呼真实号码】走运营商 SIP 中继(gateway)，改 4 处 + FS 侧加 external 网关：
    #   1. endpoint_template → sofia/gateway/<gw_name>/{phone}   ← 模板不含 {domain}，outbound_domain 随之失效
    #      <gw_name> 对应 FS external profile 里的 <gateway name>；当前 sip_profiles/ 无 external.xml，需新增
    #   2. {phone} 含义：分机号 → 真实号码(国家码按网关要求)，值来自 call_target.user_key(console 录入)
    #   3. caller_id 必填：运营商分配主叫号，未报备会被拒呼(本字段仅设 origination_caller_id_number；
    #      若要主叫名/P-Asserted-Identity，需在 originate.py build_originate_command 扩展 channel vars)
    #   4. codec 按运营商调整：G.711(PCMA/PCMU) 通用；省带宽可上 G.729(FS 需 mod_g729 授权或仅 passthru)
    #   合规前置：主叫号报备、白名单、外呼时段(call_task.allowed_hours)、录音告知 —— 上线前必须就绪
    #
    # originate 端点模板 {phone}/{domain} 占位。验证结论：本地注册分机必须用 user/{phone}@{domain}
    # 直连（sofia/internal/{phone} 会重新进 dialplan 导致循环）。
    outbound_endpoint_template: str = "user/{phone}@{domain}"
    # 端点模板含 {domain}（测试模式 user/{phone}@{domain}）时必填：= FS local_ip_v4，每台机器不同。
    # 不给默认值（避免把某台机器 IP 当通用配置）；留空则启动时自动取本机主网卡 IP——agent-flow 与
    # FS 同机即 SIP 注册域，零配置可用；FS 在远端时须显式设 CALLBOT_OUTBOUND_DOMAIN=<FS主机IP>。
    # 生产切 gateway 模板（sofia/gateway/<gw>/{phone}，无 {domain}）后此项失效。
    outbound_domain: str = ""
    # 强制编解码：实测 G.722 会让 mod_audio_fork 抓帧格式不对、ASR 收不到有效音频，必须强制线性 PCMA。空串=不强制。
    outbound_codec_string: str = "PCMA"
    # 主叫号：测试(内部分机)可空；生产走网关必填且需运营商报备，否则拒呼。
    outbound_caller_id: str = ""
    outbound_scheduler_tick_sec: int = 10
    outbound_global_concurrency: int = 0  # 0=不限，仅 per-task concurrent_limit 生效

    @model_validator(mode="after")
    def _resolve_outbound_domain(self) -> "Settings":
        # 端点模板含 {domain}（测试模式）且未显式配置时，自动取本机主网卡 IP——FS $${domain}=
        # $${local_ip_v4}，agent-flow 与 FS 同机即 SIP 注册域，零配置可用。生产 gateway 模板（无 {domain}
        # 占位）不触发，outbound_domain 留空也无妨（模板不渲染它）。
        if "{domain}" in self.outbound_endpoint_template and not self.outbound_domain:
            self.outbound_domain = _detect_local_ip()
        return self

    model_config = {"env_prefix": "CALLBOT_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
