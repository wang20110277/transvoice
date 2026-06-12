"""Agent Orchestrator — FastAPI WebSocket 入口。

事件驱动架构：
  FreeSWITCH CHANNEL_ANSWER → ESL handler → uuid_audio_fork → WS /media/{uuid}
  → StreamingCallHandler → JitterBuffer → VAD → ASR → LLM 流式 → TTS → 回传

服务启动顺序（lifespan）：
  ① 核心服务 (MCP, TTS, ASR, Memory)
  ② 可选 gRPC 客户端
  ③ 可选 WebSocket 客户端
  ④ 注入 flow.py 服务单例
  ⑤ ESL 连接 + 事件订阅
  ⑥ 创建 StreamingCallHandler
"""
import sys
from pathlib import Path

# 确保 src/ 在 sys.path 中，兼容 Docker 挂载和本地开发
_src = str(Path(__file__).resolve().parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket

from src.config import settings
from src.graph.flow import set_services, run_pre_llm_phase, run_streaming_pipeline
from src.memory.assembler import MemoryAssembler
from src.clients.mcp import MCPClient
from src.clients.tts import TTSClient
from src.clients.asr import ASRClient
from src.clients.esl import ESLClient
from src.ws.registry import ActiveCallRegistry
from src.ws.denoise import create_denoiser
from src.ws.audio_processing import create_audio_processing
from src.ws.vad import create_vad
from src.clients.asr_grpc_client import ASRGrpcClient
from src.clients.tts_grpc_client import TTSGrpcClient
from src.clients.asr_ws_client import ASRWebSocketClient
from src.clients.tts_ws_client import TTSWebSocketClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── 模块级状态 — 由 lifespan 管理 ──

_initialized = False
_streaming_handler = None
_call_registry = ActiveCallRegistry()
_audio_fork_started: set[str] = set()  # 防止 ESL 多连接重复触发 audio_fork_start


# ═══════════════════════════════════════════════════════════════════
# 服务初始化
# ═══════════════════════════════════════════════════════════════════

async def _init_core_services() -> tuple[MemoryAssembler, MCPClient, TTSClient, ASRClient]:
    """初始化核心服务：Memory、MCP、TTS、ASR。"""
    assembler = MemoryAssembler()
    logger.info("MemoryAssembler initialized")

    mcp = MCPClient(settings.mcp_server_url, settings.mcp_transport)
    try:
        await asyncio.wait_for(mcp.initialize(), timeout=10)
        logger.info("MCP client connected to %s", settings.mcp_server_url)
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning("MCP init failed (identity/credit queries will be skipped): %s", e)

    tts = TTSClient(settings.tts_adapter_url)
    await tts.start()
    logger.info("TTS client started → %s", settings.tts_adapter_url)

    asr = ASRClient(settings.asr_adapter_url)
    await asr.start()
    logger.info("ASR client started → %s", settings.asr_adapter_url)

    return assembler, mcp, tts, asr


async def _init_grpc_clients() -> tuple[ASRGrpcClient | None, TTSGrpcClient | None]:
    """初始化可选 gRPC 客户端（ASR + TTS）。"""
    asr_grpc = None
    if settings.asr_use_grpc:
        asr_grpc = ASRGrpcClient(settings.asr_grpc_target)
        await asr_grpc.start()
        logger.info("ASR gRPC client → %s", settings.asr_grpc_target)

    tts_grpc = None
    if settings.tts_use_grpc:
        tts_grpc = TTSGrpcClient(settings.tts_grpc_target)
        await tts_grpc.start()
        logger.info("TTS gRPC client → %s", settings.tts_grpc_target)

    return asr_grpc, tts_grpc


async def _init_ws_clients() -> tuple[ASRWebSocketClient | None, TTSWebSocketClient | None]:
    """初始化可选 WebSocket 客户端（ASR + TTS）。"""
    asr_ws = None
    if settings.asr_use_ws:
        asr_ws = ASRWebSocketClient(settings.asr_ws_url)
        await asr_ws.start()
        logger.info("ASR WS client → %s", settings.asr_ws_url)

    tts_ws = None
    if settings.tts_use_ws:
        tts_ws = TTSWebSocketClient(settings.tts_ws_url)
        await tts_ws.start()
        logger.info("TTS WS client → %s", settings.tts_ws_url)

    return asr_ws, tts_ws


# ═══════════════════════════════════════════════════════════════════
# ESL 事件处理
# ═══════════════════════════════════════════════════════════════════

def _create_esl_event_handlers(esl: ESLClient) -> None:
    """注册 CHANNEL_ANSWER / CHANNEL_HANGUP 事件处理。"""

    async def _on_channel_hangup(event):
        uuid = event.headers.get("Unique-ID", "")
        if not uuid:
            return
        logger.info("[%s] CHANNEL_HANGUP", uuid)
        _audio_fork_started.discard(uuid)
        try:
            await esl.audio_fork_stop(uuid)
        except Exception:
            pass
        _call_registry.cancel_call(uuid)

    async def _on_channel_answer(event):
        uuid = event.headers.get("Unique-ID", "")
        if not uuid:
            return

        # 防止 ESL 多连接重复触发（set.add 是同步原子操作）
        if uuid in _audio_fork_started:
            logger.info("[%s] CHANNEL_ANSWER duplicate, ignoring", uuid)
            return
        _audio_fork_started.add(uuid)

        biz_type = event.headers.get("variable_biz_type", "marketing")
        user_key = (
            event.headers.get("variable_user_key", "")
            or event.headers.get("Caller-Caller-ID-Number", "")
        )
        logger.info("[%s] CHANNEL_ANSWER biz_type=%s user_key=%s", uuid, biz_type, user_key)

        _call_registry.register(uuid, biz_type, user_key)

        ws_url = f"ws://{settings.media_ws_host}:{settings.media_ws_port}/media/{uuid}"
        try:
            result = await esl.audio_fork_start(uuid, ws_url, sample_rate=settings.media_sample_rate)
            logger.info("[%s] uuid_audio_fork start → %s: %s", uuid, ws_url, result)
        except Exception as e:
            logger.error("[%s] uuid_audio_fork start failed: %s", uuid, e)
            _call_registry.unregister(uuid)

    esl.on_event("CHANNEL_HANGUP", _on_channel_hangup)
    esl.on_event("CHANNEL_ANSWER", _on_channel_answer)


# ═══════════════════════════════════════════════════════════════════
# 生命周期
# ═══════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期：按顺序初始化所有服务，yield 后清理。"""
    global _initialized, _streaming_handler

    logger.info("══════════════════════════════════════")
    logger.info("  Agent Orchestrator starting up")
    logger.info("══════════════════════════════════════")

    # ── ① 核心服务 ──
    assembler, mcp, tts, asr = await _init_core_services()

    # ── ② 可选 gRPC 客户端 ──
    asr_grpc, tts_grpc = await _init_grpc_clients()

    # ── ③ 可选 WebSocket 客户端 ──
    asr_ws, tts_ws = await _init_ws_clients()

    # ── ④ 注入 flow.py 服务单例 ──
    set_services(assembler, mcp, tts, asr, tts_grpc=tts_grpc, asr_grpc=asr_grpc,
                 tts_ws=tts_ws, asr_ws=asr_ws)

    # ── ⑤ ESL 连接 + 事件订阅 ──
    esl = ESLClient(host=settings.esl_host, port=settings.esl_port, password=settings.esl_password)
    _create_esl_event_handlers(esl)

    subscribed_events = ["CHANNEL_HANGUP", "CHANNEL_ANSWER"]
    try:
        await esl.start()
        await esl.subscribe(subscribed_events)
        logger.info("ESL connected to %s:%d, subscribed to %s",
                     settings.esl_host, settings.esl_port, ", ".join(subscribed_events))
    except Exception as e:
        logger.warning("ESL connection failed (background reconnect started): %s", e)

    # ── ⑥ 创建 StreamingCallHandler ──
    from src.ws.handler import StreamingCallHandler

    denoiser = create_denoiser()
    apm = create_audio_processing(settings)
    vad_factory = lambda: create_vad(settings)

    _streaming_handler = StreamingCallHandler(
        pre_llm_fn=run_pre_llm_phase,
        streaming_fn=run_streaming_pipeline,
        esl=esl,
        handoff_extension=settings.handoff_extension,
        registry=_call_registry,
        vad_factory=vad_factory,
        barge_in_min_audio_bytes=settings.barge_in_min_audio_bytes,
        jitter_target_depth=settings.jitter_target_depth,
        jitter_max_depth=settings.jitter_max_depth,
        denoiser=denoiser,
        apm=apm,
        asr_grpc_client=asr_grpc,
        use_grpc_streaming=settings.asr_use_grpc,
        asr_ws_client=asr_ws,
        use_ws_streaming=settings.asr_use_ws,
        use_streaming_asr=settings.asr_streaming_enabled,
        tts_prebuffer_frames=settings.tts_prebuffer_frames,
    )

    _initialized = True
    _log_startup_summary()

    yield

    # ── 关闭 ──
    await _shutdown(mcp, asr_grpc, tts_grpc, asr_ws, tts_ws, asr, tts, esl)
    _initialized = False


def _log_startup_summary() -> None:
    """输出启动配置摘要。"""
    logger.info("──────────────────────────────────────")
    logger.info("  VAD: %s", settings.vad_type)
    logger.info("  Denoise: %s", settings.denoise_enabled or "disabled")
    logger.info("  AEC/APM: enabled=%s type=%d ns=%d agc=%d delay=%dms",
                settings.aec_enabled, settings.aec_type,
                settings.aec_ns_level, settings.aec_agc_type, settings.aec_system_delay_ms)
    logger.info("  ASR transport: grpc=%s ws=%s streaming=%s",
                settings.asr_use_grpc, settings.asr_use_ws, settings.asr_streaming_enabled)
    logger.info("  TTS transport: grpc=%s ws=%s streaming=%s",
                settings.tts_use_grpc, settings.tts_use_ws, settings.tts_streaming_enabled)
    logger.info("  Splitter: min=%d timeout=%.1fs eager_first=%s",
                settings.splitter_min_length, settings.splitter_flush_timeout, settings.splitter_eager_first)
    logger.info("  Audio: sample_rate=%d gain=%.1fx jitter=%d-%d",
                settings.media_sample_rate, settings.audio_gain,
                settings.jitter_target_depth, settings.jitter_max_depth)
    logger.info("  Barge-in: min_bytes=%d", settings.barge_in_min_audio_bytes)
    logger.info("──────────────────────────────────────")
    logger.info("  Agent Orchestrator ready (port %d)", settings.media_ws_port)
    logger.info("══════════════════════════════════════")


async def _shutdown(
    mcp: MCPClient,
    asr_grpc: ASRGrpcClient | None,
    tts_grpc: TTSGrpcClient | None,
    asr_ws: ASRWebSocketClient | None,
    tts_ws: TTSWebSocketClient | None,
    asr: ASRClient,
    tts: TTSClient,
    esl: ESLClient,
) -> None:
    """按逆序关闭所有服务。"""
    logger.info("Shutting down...")

    # 关闭 ESL
    try:
        await esl.close()
        logger.info("ESL closed")
    except Exception:
        pass

    # 关闭可选客户端
    for name, client in [("ASR gRPC", asr_grpc), ("TTS gRPC", tts_grpc),
                          ("ASR WS", asr_ws), ("TTS WS", tts_ws)]:
        if client:
            try:
                await client.close()
                logger.info("%s client closed", name)
            except Exception:
                pass

    # 关闭核心客户端
    for name, client in [("MCP", mcp), ("ASR", asr), ("TTS", tts)]:
        try:
            await client.close()
            logger.info("%s client closed", name)
        except Exception:
            pass

    logger.info("══════════════════════════════════════")
    logger.info("  Agent Orchestrator shut down")
    logger.info("══════════════════════════════════════")


# ═══════════════════════════════════════════════════════════════════
# FastAPI 应用
# ═══════════════════════════════════════════════════════════════════

app = FastAPI(title="Agent Orchestrator", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "ok" if _initialized else "initializing"}


@app.websocket("/media/{call_id}")
async def ws_media_fork(websocket: WebSocket, call_id: str):
    """uuid_audio_fork 端点 — FreeSWITCH 作为 WS 客户端连接。

    流程:
      1. FreeSWITCH CHANNEL_ANSWER → ESL handler → uuid_audio_fork start → FS 连接本端点
      2. 双向音频流: JitterBuffer → VAD → ASR → LLM 流式 → 句级 TTS → 回传
      3. CHANNEL_HANGUP → uuid_audio_fork stop → 清理
    """
    if _streaming_handler is None:
        await websocket.close(code=503, reason="Service not initialized")
        return

    call = _call_registry.get(call_id)
    biz_type = call.biz_type if call else "marketing"
    user_key = call.user_key if call else ""

    await _streaming_handler.handle(websocket, call_id, biz_type, user_key)
