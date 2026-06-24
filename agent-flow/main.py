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

# 把整个 .env 灌进 os.environ —— pydantic-settings(env_prefix=CALLBOT_) 只加载
# CALLBOT_ 前缀字段，无前缀的 MINIO_* 不会进 os.environ，而 minio_storage 在 import 时
# 用 os.environ.get 读 MINIO_*。load_dotenv 必须在任何 src.storage import 之前执行。
from dotenv import load_dotenv
load_dotenv()

import asyncio
import hashlib
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, WebSocket

from src.config import settings
from src.storage import minio_storage, repository
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
from sqlalchemy import select
from src.database import async_session
from src.db.models import InboundRoute

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
_ongoing_archives: set = set()  # 强引用持有 _archive_recording task，防 fire-and-forget 被 GC
_outbound_executor = None  # OutboundExecutor 单例，lifespan 启停


def _mask_phone(s: str) -> str:
    """手机号脱敏：首3末4，中间掩码（138****5678）。短串原样返回。"""
    return f"{s[:3]}****{s[-4:]}" if len(s) >= 7 else s


def _phone_hash(s: str) -> str:
    """手机号 sha256（脱敏存储，供跨通话关联同一用户；canonical user_id 待 MCP 重启）。"""
    return hashlib.sha256(s.encode()).hexdigest()


async def _archive_recording(fs_uuid: str, biz_type: str, tenant_id: str, user_key: str) -> None:
    """挂断后间隔 3s → 读 FS 录音 → 上传 MinIO → insert_artifact(kind='recording')。

    fire-and-forget 由 CHANNEL_HANGUP 调起；任何异常仅记日志，不阻断下一通。
    """
    # 用户要求：挂断后间隔 3 秒再上传（等 FS flush 完 wav）
    await asyncio.sleep(settings.recording_archive_delay_sec)
    path = os.path.join(settings.recordings_dir, f"{fs_uuid}.wav")
    logger.info("[%s] _archive_recording start: %s (exists=%s)",
                fs_uuid, path, os.path.exists(path))
    if not os.path.exists(path):
        logger.warning("[%s] recording file not found after %ds: %s",
                       fs_uuid, settings.recording_archive_delay_sec, path)
        return

    try:
        with open(path, "rb") as f:
            wav_bytes = f.read()
    except OSError as e:
        logger.warning("[%s] read recording failed: %s", fs_uuid, e)
        return

    key = await minio_storage.upload_recording(fs_uuid, wav_bytes, biz_type, tenant_id)
    if key is None:
        logger.info("[%s] upload_recording returned None (MinIO 未配置或 endpoint=%s)，跳过归档",
                    fs_uuid, os.environ.get("MINIO_ENDPOINT", ""))
        return  # MinIO 未配置，静默跳过

    try:
        await repository.insert_artifact(
            call_id=fs_uuid, fs_uuid=fs_uuid, biz_type=biz_type,
            user_id=user_key, user_key=user_key,
            kind="recording", storage="minio", uri=key,
            size_bytes=len(wav_bytes), content_type="audio/wav",
        )
        logger.info("[%s] recording archived: %s (%d bytes)", fs_uuid, key, len(wav_bytes))
    except Exception as e:
        logger.error("[%s] insert_artifact(recording) failed: %s", fs_uuid, e)


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

def _parse_int_var(value: str | None) -> int | None:
    """ESL channel vars 是字符串；把 None/空/非数字安全解析为 int（失败返回 None）。"""
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _hangup_cause_to_outbound_status(hangup_cause: str) -> str:
    """外呼挂断原因 → call_target 状态。

    NORMAL_CLEARING=被叫接通后挂断（接通成功）；NO_ANSWER/RECOVERY_ON_TIMER_EXPIRE=振铃未接；
    其余按失败。重拨与否由 hangup 分支按 redial_strategy 再判。
    """
    if hangup_cause == "NORMAL_CLEARING":
        return "answered"
    if hangup_cause in ("NO_ANSWER", "RECOVERY_ON_TIMER_EXPIRE"):
        return "no_answer"
    return "failed"


async def _resolve_inbound_dimensions(did: str) -> tuple[str, str, str] | None:
    """查 inbound_route 把 DID 解析为 (tenant_id, biz_type, scenario)。

    匹配顺序:精确 did 优先,号段 did_pattern 正则兜底。无匹配返回 None(由调用方回落)。
    """
    if not did:
        return None
    try:
        async with async_session() as session:
            stmt = select(
                InboundRoute.tenant_id, InboundRoute.biz_type, InboundRoute.scenario
            ).where(InboundRoute.did == did, InboundRoute.is_active.is_(True))
            row = (await session.execute(stmt)).first()
            if row:
                return row[0], row[1], row[2]

            # 号段正则兜底
            import re
            stmt2 = select(
                InboundRoute.tenant_id, InboundRoute.biz_type, InboundRoute.scenario,
                InboundRoute.did_pattern,
            ).where(InboundRoute.did_pattern.is_not(None), InboundRoute.is_active.is_(True))
            for r in (await session.execute(stmt2)).all():
                try:
                    if re.fullmatch(r[3], did):
                        return r[0], r[1], r[2]
                except re.error:
                    continue
    except Exception as e:
        logger.error("[%s] inbound_route resolve failed: %s", did, e)
    return None


def _create_esl_event_handlers(esl: ESLClient) -> None:
    """注册 CHANNEL_ANSWER / CHANNEL_HANGUP 事件处理。"""

    async def _on_channel_hangup(event):
        uuid = event.headers.get("Unique-ID", "")
        if not uuid:
            return
        logger.info("[%s] CHANNEL_HANGUP", uuid)
        _audio_fork_started.discard(uuid)

        hangup_cause = event.headers.get("Hangup-Cause", "")
        result_code = event.headers.get("Variable-Hangup-Cause", "")
        end_ts = datetime.now()
        active = _call_registry.get(uuid)

        # session end + 录音归档（fire-and-forget，不阻塞 hangup 清理）
        if active:
            try:
                await repository.update_call_session_end(
                    uuid, end_ts, hangup_cause, result_code,
                )
            except Exception as e:
                logger.error("[%s] update_call_session_end failed: %s", uuid, e)
            archive_task = asyncio.create_task(
                _archive_recording(uuid, active.biz_type, active.tenant_id, active.user_key)
            )
            _ongoing_archives.add(archive_task)  # 强引用，防 GC 回收未完成的 task
            archive_task.add_done_callback(_ongoing_archives.discard)
            archive_task.add_done_callback(
                lambda t: t.exception() and logger.error(
                    "[%s] _archive_recording failed: %s", uuid, t.exception(),
                )
            )

        # 外呼结果回写 + 重拨判定：按 Hangup-Cause + redial_strategy 决定重拨 or 终态
        call_target_id = _parse_int_var(event.headers.get("variable_call_target_id"))
        if call_target_id is not None:
            session_id = None
            try:
                sess = await repository.get_call_session_by_fs_uuid(uuid)
                if sess is not None:
                    session_id = sess.id
            except Exception as e:
                logger.error("[%s] get_call_session for target outcome failed: %s", uuid, e)
            try:
                target = await repository.get_call_target(call_target_id)
                if target is not None:
                    redial_strategy = await repository.get_redial_strategy(target.task_id)
                    from src.outbound.redial import decide_redial
                    decision = decide_redial(
                        hangup_cause, target.attempt_count, target.max_attempts,
                        redial_strategy.get("retry_on_causes", []),
                    )
                    if decision.redial:
                        interval = float(redial_strategy.get("interval_min", 1) or 1)
                        await repository.reset_call_target_for_redial(call_target_id, interval)
                        logger.info("[%s] target %s redial scheduled (cause=%s attempt=%s/%s)",
                                    uuid, call_target_id, hangup_cause,
                                    target.attempt_count + 1, target.max_attempts)
                    else:
                        await repository.update_call_target_outcome(
                            call_target_id, decision.final_status, hangup_cause, session_id,
                        )
                        logger.info("[%s] target %s final=%s (cause=%s)",
                                    uuid, call_target_id, decision.final_status, hangup_cause)
            except Exception as e:
                logger.error("[%s] call_target redial/outcome failed: %s", uuid, e)

        try:
            await esl.audio_fork_stop(uuid)
        except Exception:
            pass
        # 停止 FS 录制并 flush（channel 可能已 hangup，失败忽略；FS 挂断本会自动落盘）
        try:
            await esl.record_stop(uuid)
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

        # 三维度来源分两路：外呼从 channel vars 注入读（originate 时塞），呼入走 DID 解析。
        # downstream register/audio_fork/record/session 不区分呼入呼出，只认 uuid + 三元组。
        call_task_id: int | None = None
        call_target_id: int | None = None

        if event.headers.get("variable_ai_outbound"):
            # 外呼：三元组 + task/target ID 全从 channel vars 读，跳过 DID 解析
            tenant_id = event.headers.get("variable_tenant_id", "default")
            biz_type = event.headers.get("variable_biz_type", "marketing")
            scenario = event.headers.get("variable_scenario", "default")
            user_key = event.headers.get("variable_user_key", "")
            call_task_id = _parse_int_var(event.headers.get("variable_call_task_id"))
            call_target_id = _parse_int_var(event.headers.get("variable_call_target_id"))
        else:
            # 呼入：DID → (tenant_id, biz_type, scenario)；失败回落 dialplan 静态 variable_biz_type
            biz_type = event.headers.get("variable_biz_type", "marketing")
            user_key = (
                event.headers.get("variable_user_key", "")
                or event.headers.get("Caller-Caller-ID-Number", "")
            )
            did = (
                event.headers.get("variable_did")
                or event.headers.get("Caller-Destination-Number")
                or ""
            )
            resolved = await _resolve_inbound_dimensions(did)
            if resolved:
                tenant_id, biz_type, scenario = resolved
            else:
                tenant_id = "default"
                scenario = "default"
                if did:
                    logger.warning(
                        "[%s] no inbound_route match for did=%s, fallback biz_type=%s",
                        uuid, did, biz_type,
                    )

        logger.info(
            "[%s] CHANNEL_ANSWER tenant=%s biz_type=%s scenario=%s user_key=%s "
            "outbound=%s task=%s target=%s",
            uuid, tenant_id, biz_type, scenario, user_key,
            bool(call_target_id), call_task_id, call_target_id,
        )

        _call_registry.register(uuid, biz_type, user_key, tenant_id=tenant_id, scenario=scenario)

        # session 写入 PG（fire-and-forget，DB 异常仅记日志不阻断通话）
        try:
            await repository.insert_call_session({
                "call_id": uuid, "fs_uuid": uuid,          # 决策 1：同值
                "user_id": user_key,                        # 本期 fallback（MCP 禁用，见 design §7）
                "biz_type": biz_type, "tenant_id": tenant_id, "scenario": scenario,
                "phone_hash": _phone_hash(user_key), "user_key": user_key,
                "phone_masked": _mask_phone(user_key),
                "start_ts": datetime.now(),
                "recording_notice_played": settings.recording_notice_enabled,
                "call_task_id": call_task_id, "call_target_id": call_target_id,
            })
        except Exception as e:
            logger.error("[%s] insert_call_session failed: %s", uuid, e)

        ws_url = f"ws://{settings.media_ws_host}:{settings.media_ws_port}/media/{uuid}"
        try:
            result = await esl.audio_fork_start(uuid, ws_url, sample_rate=settings.media_sample_rate)
            logger.info("[%s] uuid_audio_fork start → %s: %s", uuid, ws_url, result)

            # FS uuid_record 录整通双声道到标准 recordings 路径（与 _archive_recording 一致）。
            # 顺序关键：必须在 audio_fork_start 之后发起，record bug 才排在 WRITE_REPLACE 之后，
            # 从而 tap 到被 dub 后的 AI 下行音频（L=caller / R=AI）。失败 non-fatal，该通无录音。
            fs_rec_path = os.path.join(settings.recordings_dir, f"{uuid}.wav")
            try:
                await esl.record_start(uuid, fs_rec_path)
                logger.info("[%s] uuid_record start → %s", uuid, fs_rec_path)
            except Exception as e:
                logger.error("[%s] uuid_record start failed (non-fatal): %s", uuid, e)
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

    # ── ⑦ 外呼执行器（进程内 asyncio，tick 调度）──
    global _outbound_executor
    from src.outbound.executor import OutboundExecutor
    _outbound_executor = OutboundExecutor(esl, settings)
    _outbound_executor.start()

    _initialized = True
    _log_startup_summary()

    yield

    # ── 关闭 ──
    if _outbound_executor is not None:
        await _outbound_executor.stop()
        _outbound_executor = None
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
    tenant_id = call.tenant_id if call else "default"
    scenario = call.scenario if call else "default"

    await _streaming_handler.handle(websocket, call_id, biz_type, user_key, tenant_id, scenario)
