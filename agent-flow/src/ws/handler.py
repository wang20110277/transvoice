"""WebSocket 通话处理 — 流式音频回传 + ESL 通话控制 + Barge-in 打断。

由 main.py 创建 StreamingCallHandler 实例，注入 flow.py 的两个函数：
  - pre_llm_fn  = flow.run_pre_llm_phase   (ASR + MCP/Memory/RAG 并行)
  - streaming_fn = flow.run_streaming_pipeline (LLM 流式 → 句级 TTS)

调用链路：
  main.py::ws_media_fork() → handler.handle()
    → 接收循环: near 音频全量喂 ASR → 服务端 FSMN-VAD 分段 → ASR final 回调(on_final)驱动轮次
    → TurnController.on_final(): pre_llm_fn → streaming_fn
    → _receive_during_streaming(): RMSGate 检测 barge-in
"""
import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

import numpy as np

from fastapi import WebSocket, WebSocketDisconnect

from config import settings as _settings
from ws.rms_gate import RMSGate
from ws.jitter_buffer import JitterBuffer, TTSOutputBuffer
from ws.registry import ActiveCallRegistry
from ws.denoise import BaseDenoiser, PassThroughDenoiser
from ws.audio_processing import WebRTCAPM
from ws.asr_streaming import AsrStreamingManager
from storage import minio_storage
from storage.persistence_helpers import fire_insert_event

if TYPE_CHECKING:
    from clients.esl import ESLClient
    from clients.asr_grpc_client import ASRGrpcClient
    from clients.asr_ws_client import ASRWebSocketClient, ASRWsStream
    from ws.registry import ActiveCall

logger = logging.getLogger(__name__)


class TurnController:
    """streaming_task 生命周期 + turn_lock —— 解耦 on_final/barge-in 并发,便于单测。

    lock 串行化 streaming_task 的 check-and-set:on_final launch vs cancel_for_barge 取消。
    reset_fn(清 audio_buffer/jitter/rms_gate)在锁内调用,确保 launch 快照后再清。
    """

    def __init__(self, launch_fn, reset_fn, min_text_len: int = 2):
        self._launch_fn = launch_fn   # (result_dict, turn:int) -> asyncio.Task
        self._reset_fn = reset_fn     # () -> None
        self._min_text_len = min_text_len
        self.lock = asyncio.Lock()
        self.streaming_task: asyncio.Task | None = None
        self.turn_count = 0

    async def on_final(self, result: dict) -> None:
        async with self.lock:
            if self.streaming_task and not self.streaming_task.done():
                logger.info("final while turn active, drop")
                return
            text = (result.get("text") or "").strip()
            if len(text) < self._min_text_len:
                logger.info("ASR final too short ('%s'), skip", text)
                return
            self.turn_count += 1
            self.streaming_task = self._launch_fn(result, self.turn_count)
            self._reset_fn()

    async def cancel_for_barge(self) -> "asyncio.Task | None":
        async with self.lock:
            task = self.streaming_task
            self.streaming_task = None
            self._reset_fn()
            return task


class StreamingCallHandler:
    """流式 WebSocket handler — LLM 流式 → 句级 TTS → 音频按序回传 + Barge-in。

    Barge-in 机制：
    - 流式 TTS 回传期间，并发接收用户音频并用 RMSGate 检测 barge-in
    - 连续 N 帧（600ms）检测到语音 → 取消 LLM/TTS 流 → 开始新一轮
    - AI 未开口前不允许 barge-in（用户不可能打断沉默）
    """

    # 语音帧阈值 — 5 × 30ms = 150ms，过滤极短附和音（嗯/啊），保留正常打断（你是谁/停/等一下）
    _BARGE_IN_SPEECH_FRAMES = 5
    # 允许的非语音帧数 — 音节间隙（"你~是~谁"之间的短暂停顿）不重置计数器
    _BARGE_IN_TOLERANCE_FRAMES = 3
    # RMS 阈值 — 低于此值的帧视为静音/噪声，不参与 barge-in 判定
    _BARGE_IN_RMS_THRESHOLD = 300

    def __init__(
        self,
        pre_llm_fn,
        streaming_fn,
        esl: "ESLClient | None" = None,
        handoff_extension: str = "1001",
        registry: ActiveCallRegistry | None = None,
        rms_gate_factory: "callable | None" = None,
        barge_in_min_audio_bytes: int = 1600,
        jitter_target_depth: int = 3,
        jitter_max_depth: int = 10,
        denoiser: BaseDenoiser | None = None,
        apm: "WebRTCAPM | None" = None,
        asr_grpc_client: "ASRGrpcClient | None" = None,
        use_grpc_streaming: bool = False,
        asr_ws_client: "ASRWebSocketClient | None" = None,
        use_ws_streaming: bool = False,
        use_streaming_asr: bool = False,
        tts_prebuffer_frames: int = 0,
    ) -> None:
        self._pre_llm_fn = pre_llm_fn
        self._streaming_fn = streaming_fn
        self._esl = esl
        self._handoff_extension = handoff_extension
        self._registry = registry
        self._rms_gate_factory = rms_gate_factory
        self._barge_in_min_audio_bytes = barge_in_min_audio_bytes
        # AEC 场景可调高 barge-in RMS 阈值，过滤 AEC 残留回声尖峰（实例属性覆盖类常量）
        self._BARGE_IN_RMS_THRESHOLD = _settings.barge_in_rms_threshold
        self._jitter_target_depth = jitter_target_depth
        self._jitter_max_depth = jitter_max_depth
        self._denoiser = denoiser or PassThroughDenoiser()
        self._apm = apm
        self._asr_grpc_client = asr_grpc_client
        self._use_grpc_streaming = use_grpc_streaming
        self._asr_ws_client = asr_ws_client
        self._use_ws_streaming = use_ws_streaming
        self._use_streaming_asr = use_streaming_asr
        self._tts_prebuffer_frames = tts_prebuffer_frames

    # ───────────────────────────────────────────────────────────────
    # 主循环
    # ───────────────────────────────────────────────────────────────

    async def handle(
        self,
        websocket: WebSocket,
        call_id: str,
        biz_type: str,
        user_key: str,
        tenant_id: str = "default",
        scenario: str = "default",
    ) -> None:
        """WebSocket 主循环：接收音频全量喂 ASR → ASR final 回调驱动轮次 → 流式管线 → RMSGate 检测 barge-in。"""
        await websocket.accept()
        logger.info(
            "[%s] WS connected tenant=%s biz_type=%s scenario=%s user_key=%s",
            call_id, tenant_id, biz_type, scenario, user_key,
        )

        # TTSOutputBuffer: 单 writer 统一出口，拆帧（960B）匀速 30ms 发送，无数据时静音保活
        tts_buffer = TTSOutputBuffer(
            send_fn=websocket.send_bytes,
            prebuffer_frames=self._tts_prebuffer_frames,
        )
        await tts_buffer.start()

        active_call = self._resolve_active_call(call_id, biz_type, user_key, tenant_id, scenario)
        rms_gate = self._rms_gate_factory() if self._rms_gate_factory else RMSGate()
        jitter = JitterBuffer(target_depth=self._jitter_target_depth, max_depth=self._jitter_max_depth)
        audio_buffer = bytearray()
        audio_gain = _settings.audio_gain

        # 非 WS 路径无端点触发器:gRPC/HTTP ASR 已移除本地 VAD endpoint,关闭 WS 等于无轮次启动
        if not self._use_ws_streaming:
            logger.warning(
                "[%s] ASR WS streaming disabled — endpoint detection needs WS-driven finals; no turns will launch",
                call_id,
            )

        # TurnController:端点由 ASR final 回调驱动(控制流反转)
        def _launch_turn(result: dict, turn: int):
            raw_audio = self._gain_audio(audio_buffer, audio_gain)
            return asyncio.create_task(
                self._process_streaming_turn(
                    websocket, call_id, biz_type, user_key,
                    raw_audio, turn, active_call,
                    barge_in_event=barge_in_event,
                    precomputed_asr_result=result,
                    ai_spoken_event=ai_has_spoken,
                    tts_buffer=tts_buffer,
                    tenant_id=tenant_id,
                    scenario=scenario,
                ),
                name=f"stream-{call_id}-{turn}",
            )

        turn_ctrl = TurnController(
            launch_fn=_launch_turn,
            reset_fn=lambda: self._reset_audio_state(audio_buffer, rms_gate, jitter),
        )

        # ASR streaming state —— on_final=turn_ctrl.on_final 驱动轮次(WS 服务端分段)
        asr = AsrStreamingManager(
            asr_grpc_client=self._asr_grpc_client,
            asr_ws_client=self._asr_ws_client,
            use_grpc_streaming=self._use_grpc_streaming,
            use_ws_streaming=self._use_ws_streaming,
            use_streaming_asr=self._use_streaming_asr,
            on_final=turn_ctrl.on_final if self._use_ws_streaming else None,
        )

        # Barge-in state
        barge_in_event = asyncio.Event()
        ai_has_spoken = asyncio.Event()
        ai_spoken_buffer_cleared = False
        # barge-in 后 VAD 冷却截止时间：此时间内丢弃音频，防止残余噪声误触发 VAD
        cooldown_until: float = 0.0
        # list 包装允许内部方法修改外部变量
        barge_grace_until: list[float] = [0.0]
        barge_speech_counter: list[int] = [0]
        barge_tolerance_counter: list[int] = [0]

        try:
            while True:
                if active_call and active_call.cancel.is_set():
                    logger.info("[%s] CHANNEL_HANGUP, stopping", call_id)
                    break

                # ── AI 说话中：并发接收用户音频检测 barge-in ──
                if turn_ctrl.streaming_task and not turn_ctrl.streaming_task.done():
                    if ai_has_spoken.is_set() and not ai_spoken_buffer_cleared:
                        # AI 首次开口：清空之前的残余音频，设置 1s grace period
                        audio_buffer.clear()
                        jitter.reset()
                        self._denoiser.reset()
                        ai_spoken_buffer_cleared = True
                        barge_grace_until[0] = time.monotonic() + 1.0
                        barge_speech_counter[0] = 0
                        barge_tolerance_counter[0] = 0
                        logger.info("[%s] AI first audio — cleared buffer, grace 1.0s", call_id)

                    barge_detected = await self._receive_during_streaming(
                        websocket, call_id, rms_gate, jitter, audio_buffer,
                        turn_ctrl.streaming_task, barge_in_event, active_call,
                        barge_grace_until, ai_has_spoken, barge_speech_counter,
                        barge_tolerance_counter, tts_buffer,
                    )

                    if barge_detected:
                        # 清空 TTS 缓冲，丢弃未播放的旧音频，自动切换为静音帧
                        # 不调用 uuid_break — 它会终止 dialplan 的 playback silence_stream://-1 导致挂断
                        tts_buffer.clear()
                        logger.info("[%s] barge-in: TTS buffer cleared", call_id)
                        # 关键事件落 PG（fire-and-forget，不延迟清空）
                        fire_insert_event(
                            call_id=call_id, fs_uuid=call_id,
                            biz_type=biz_type, user_id=user_key, user_key=user_key,
                            event_type="barge_in", payload={"turn": turn_ctrl.turn_count},
                        )
                        # WS 流为 per-call 生命周期:barge-in 仅 reset 服务端进行中段(连接保持),
                        # 下一个 utterance 复用同一连接。streaming_task 由 cancel_for_barge 单独取消。
                        await asr.reset_server_segment(call_id)
                        old_task = await turn_ctrl.cancel_for_barge()
                        if old_task and not old_task.done():
                            old_task.cancel()
                        # cooldown_until 是 handle() 局部变量,直接 rebind(非 nested 函数,无需 nonlocal)
                        cooldown_until = time.monotonic() + _settings.cooldown_after_bargein
                        barge_in_event.clear()
                        ai_has_spoken.clear()
                        ai_spoken_buffer_cleared = False
                        continue
                    elif turn_ctrl.streaming_task.done():
                        # 正常完成
                        exc = turn_ctrl.streaming_task.exception()
                        if exc:
                            logger.error("[%s] streaming task error: %s", call_id, exc)
                        else:
                            logger.info("[%s] streaming turn completed", call_id)
                        turn_ctrl.streaming_task = None
                        # 清本轮 barge-listening 残余,保下轮 raw_audio 干净
                        self._reset_audio_state(audio_buffer, rms_gate, jitter)
                        barge_in_event.clear()
                        continue
                    else:
                        # streaming task 仍在运行（LLM 处理或 TTS 播放中）
                        # 不能落到正常接收分支，否则会在等待期间误触发新轮次
                        continue

                # ── AI 未说话：正常接收用户音频 ──
                data = await websocket.receive()

                if "bytes" in data and data["bytes"]:
                    # barge-in 冷却期:丢弃残余音频,防止 RMS 误触发
                    if time.monotonic() < cooldown_until:
                        continue

                    frame = data["bytes"]
                    jitter.insert(frame)

                    while True:
                        smooth_frame = jitter.drain()
                        if not smooth_frame:
                            break
                        denoised_frame = self._process_near_frame(smooth_frame, tts_buffer)
                        audio_buffer.extend(denoised_frame)
                        # 全量喂 ASR —— 服务端 FSMN-VAD 分段,final 经 on_final 触发轮次
                        await asr.feed(denoised_frame, call_id)

                elif "text" in data and data["text"]:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "stop":
                        logger.info("[%s] WS stop received", call_id)
                        break

        except WebSocketDisconnect:
            logger.info("[%s] WS disconnected after %d turns", call_id, turn_ctrl.turn_count)
        except RuntimeError:
            logger.info("[%s] WS already disconnected after %d turns", call_id, turn_ctrl.turn_count)
        except Exception as e:
            logger.error("[%s] WS error: %s", call_id, e, exc_info=True)
        finally:
            await self._cleanup(turn_ctrl, asr, tts_buffer, call_id)

    # ───────────────────────────────────────────────────────────────
    # 音频处理辅助
    # ───────────────────────────────────────────────────────────────

    def _resolve_active_call(
        self, call_id: str, biz_type: str, user_key: str,
        tenant_id: str = "default", scenario: str = "default",
    ) -> "ActiveCall | None":
        """获取或注册 ActiveCall。"""
        if not self._registry:
            return None
        active_call = self._registry.get(call_id)
        if not active_call:
            active_call = self._registry.register(
                call_id, biz_type, user_key, tenant_id=tenant_id, scenario=scenario,
            )
        return active_call

    @staticmethod
    def _apply_gain(audio: bytes, gain: float) -> bytes:
        """对 PCM 音频应用增益（放大安静 SIP 音频）。"""
        if gain == 1.0 or len(audio) < 2:
            return audio
        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32)
        samples *= gain
        return np.clip(samples, -32768, 32767).astype(np.int16).tobytes()

    def _gain_audio(self, audio_buffer: bytearray, audio_gain: float) -> bytes:
        """端点检测后对整轮音频应用增益（返回 raw_audio）。

        AEC 开启时 WebRTCAPM 已逐帧 AGC，不再叠加固定增益（直接返回原始 PCM）；
        AEC 关闭时走 _apply_gain 放大安静 SIP 音频。
        """
        if self._apm is not None:
            return bytes(audio_buffer)
        return self._apply_gain(bytes(audio_buffer), audio_gain)

    def _process_near_frame(self, smooth_frame: bytes, tts_buffer: "TTSOutputBuffer") -> bytes:
        """near 端帧处理：AEC 开启时走 WebRTCAPM（near + reverse），否则走原 denoiser。"""
        if self._apm is not None:
            return self._apm.process(smooth_frame, tts_buffer.recent_reverse)
        return self._denoiser.process(smooth_frame)

    def _reset_audio_state(self, audio_buffer: bytearray, rms_gate: RMSGate, jitter: JitterBuffer) -> None:
        """重置所有音频处理状态，准备下一轮。"""
        audio_buffer.clear()
        rms_gate.reset()
        jitter.reset()
        self._denoiser.reset()

    # Barge-in 检测
    # ───────────────────────────────────────────────────────────────

    async def _receive_during_streaming(
        self,
        websocket: WebSocket,
        call_id: str,
        rms_gate: RMSGate,
        jitter: JitterBuffer,
        audio_buffer: bytearray,
        streaming_task: asyncio.Task,
        barge_in_event: asyncio.Event,
        active_call: "ActiveCall | None",
        grace_until: list[float],
        ai_spoken_event: asyncio.Event,
        speech_counter: list[int],
        tolerance_counter: list[int],
        tts_buffer: "TTSOutputBuffer",
    ) -> bool:
        """AI 说话时并发接收用户音频，检测 barge-in。

        条件：不在 grace period + AI 已开口 + 累积音频足够 + N 帧语音（允许短暂间隙）。
        """
        try:
            data = await asyncio.wait_for(websocket.receive(), timeout=0.1)
        except asyncio.TimeoutError:
            # 无数据 → 重置语音计数
            speech_counter[0] = 0
            tolerance_counter[0] = 0
            return False
        except (WebSocketDisconnect, RuntimeError):
            streaming_task.cancel()
            raise
        except Exception as e:
            logger.error("[%s] receive during streaming error: %s", call_id, e)
            return False

        if "bytes" in data and data["bytes"]:
            jitter.insert(data["bytes"])

            while True:
                smooth_frame = jitter.drain()
                if not smooth_frame:
                    break
                denoised_frame = self._process_near_frame(smooth_frame, tts_buffer)
                audio_buffer.extend(denoised_frame)

                # Barge-in 判定
                in_grace = time.monotonic() < grace_until[0]
                ai_speaking = ai_spoken_event.is_set()
                has_enough_audio = len(audio_buffer) >= self._barge_in_min_audio_bytes

                if not in_grace and ai_speaking and has_enough_audio and len(denoised_frame) >= 320:
                    _f32 = np.frombuffer(denoised_frame, dtype=np.int16).astype(np.float32)
                    frame_rms = float(np.sqrt(np.mean(_f32**2)))
                    is_speech = frame_rms > self._BARGE_IN_RMS_THRESHOLD and rms_gate.is_speech(denoised_frame)

                    if is_speech:
                        speech_counter[0] += 1
                        tolerance_counter[0] = 0
                        if speech_counter[0] >= self._BARGE_IN_SPEECH_FRAMES:
                            logger.info("[%s] barge-in: %d speech frames, %d bytes, rms=%.0f",
                                        call_id, speech_counter[0], len(audio_buffer), frame_rms)
                            streaming_task.cancel()
                            barge_in_event.set()
                            remaining = jitter.drain_all()
                            if remaining:
                                audio_buffer.extend(remaining)
                            return True
                    elif speech_counter[0] > 0:
                        tolerance_counter[0] += 1
                        if tolerance_counter[0] > self._BARGE_IN_TOLERANCE_FRAMES:
                            # 连续非语音帧超过容差，重置
                            speech_counter[0] = 0
                            tolerance_counter[0] = 0

        elif "text" in data and data["text"]:
            msg = json.loads(data["text"])
            if msg.get("type") == "stop":
                streaming_task.cancel()
                return False

        return False

    # ───────────────────────────────────────────────────────────────
    # 流式管线调用
    # ───────────────────────────────────────────────────────────────

    async def _process_streaming_turn(
        self,
        websocket: WebSocket,
        call_id: str,
        biz_type: str,
        user_key: str,
        audio: bytes,
        turn: int,
        active_call: "ActiveCall | None" = None,
        barge_in_event: asyncio.Event | None = None,
        precomputed_asr_result: dict | None = None,
        ai_spoken_event: asyncio.Event | None = None,
        tts_buffer: TTSOutputBuffer | None = None,
        tenant_id: str = "default",
        scenario: str = "default",
    ) -> None:
        """Phase 1 (pre-LLM) + Phase 2 (streaming LLM+TTS) → TTSOutputBuffer 回传。"""
        t0 = time.monotonic()
        try:
            if active_call and active_call.cancel.is_set():
                return

            # Phase 1: Pre-LLM
            state = await self._pre_llm_fn(
                call_id, biz_type, user_key, audio,
                precomputed_asr_result=precomputed_asr_result,
                tenant_id=tenant_id,
                scenario=scenario,
                call_task_vars=active_call.call_target_vars if active_call else None,
            )

            if barge_in_event and barge_in_event.is_set():
                logger.info("[%s] barge-in during pre-llm, aborting", call_id)
                return

            # ASR 空文本 — 跳过本轮（barge-in 音频可能包含 AI 回声导致 ASR 无法识别）
            if not state.get("user_input", "").strip():
                logger.info("[%s] turn %d ASR empty, skipping", call_id, turn)
                return

            # Phase 2: Streaming LLM+TTS
            next_to_send = 0
            pending: dict[int, list[bytes]] = {}
            terminal_action: str | None = None

            async def audio_callback(pcm: bytes, index: int) -> None:
                nonlocal next_to_send
                if barge_in_event and barge_in_event.is_set():
                    return

                pending.setdefault(index, []).append(pcm)

                # 按句序写入 tts_buffer
                while next_to_send in pending:
                    chunks = pending.pop(next_to_send)
                    for chunk in chunks:
                        if tts_buffer and tts_buffer.is_running:
                            tts_buffer.write(chunk)
                    if ai_spoken_event and not ai_spoken_event.is_set():
                        ai_spoken_event.set()
                        logger.info("[%s] AI first audio queued, barge-in enabled", call_id)
                    next_to_send += 1

            async def action_callback(action: str) -> None:
                nonlocal terminal_action
                try:
                    await websocket.send_json({"type": "action", "action": action, "turn": turn})
                except Exception as e:
                    logger.error("[%s] send action failed: %s", call_id, e)
                if action in ("end", "handoff"):
                    terminal_action = action

            await self._streaming_fn(state, audio_callback, action_callback)

            # 终端动作：等 TTS 播完再执行
            if terminal_action and tts_buffer:
                logger.info("[%s] terminal '%s': waiting for TTS to drain", call_id, terminal_action)
                await tts_buffer.wait_drained(timeout=10.0)
                await self._execute_terminal_action(terminal_action, call_id)

            elapsed = (time.monotonic() - t0) * 1000
            logger.info("[%s] turn %d done in %.0fms", call_id, turn, elapsed)

        except asyncio.CancelledError:
            logger.info("[%s] turn %d cancelled (barge-in)", call_id, turn)
        except Exception as e:
            logger.error("[%s] turn %d error: %s", call_id, turn, e, exc_info=True)
            try:
                await websocket.send_json({"type": "action", "action": "say", "text": "抱歉，请再说一遍。", "turn": turn})
            except Exception:
                pass

    # ───────────────────────────────────────────────────────────────
    # ESL 终态动作
    # ───────────────────────────────────────────────────────────────

    async def _execute_terminal_action(self, action: str, call_id: str) -> None:
        """通过 ESL 执行终态动作（挂断/转接）。"""
        if self._esl is None:
            logger.warning("[%s] ESL unavailable, cannot execute: %s", call_id, action)
            return
        # 关键事件落 PG（fire-and-forget）；biz_type/user_key 从 registry 取，取不到留空
        active = self._registry.get(call_id) if self._registry else None
        biz_type = active.biz_type if active else ""
        user_key = active.user_key if active else ""
        if action in ("end", "handoff"):
            fire_insert_event(
                call_id=call_id, fs_uuid=call_id, biz_type=biz_type,
                user_id=user_key, user_key=user_key,
                event_type="hangup_by_bot" if action == "end" else "handoff",
                payload={"extension": self._handoff_extension} if action == "handoff" else {},
            )
        try:
            if action == "end":
                result = await self._esl.hangup(call_id)
                logger.info("[%s] ESL hangup: %s", call_id, result)
            elif action == "handoff":
                result = await self._esl.transfer(call_id, self._handoff_extension)
                logger.info("[%s] ESL transfer to %s: %s", call_id, self._handoff_extension, result)
        except Exception as e:
            logger.error("[%s] ESL action '%s' failed: %s", call_id, action, e)

    # ───────────────────────────────────────────────────────────────
    # 清理
    # ───────────────────────────────────────────────────────────────

    async def _cleanup(
        self,
        turn_ctrl: "TurnController",
        asr: AsrStreamingManager,
        tts_buffer: TTSOutputBuffer,
        call_id: str,
    ) -> None:
        """清理所有资源。"""
        streaming_task = turn_ctrl.streaming_task
        if streaming_task and not streaming_task.done():
            streaming_task.cancel()
            try:
                await streaming_task
            except (asyncio.CancelledError, Exception):
                pass
        if asr.stream is not None:
            try:
                await asr.cancel()
            except Exception:
                pass
        await tts_buffer.stop()
        if self._registry:
            self._registry.unregister(call_id)
        logger.info("[%s] WS closed, total turns=%d", call_id, turn_ctrl.turn_count)
