"""WebSocket 通话处理 — 流式音频回传 + ESL 通话控制 + Barge-in 打断。

由 main.py 创建 StreamingCallHandler 实例，注入 flow.py 的两个函数：
  - pre_llm_fn  = flow.run_pre_llm_phase   (ASR + MCP/Memory/RAG 并行)
  - streaming_fn = flow.run_streaming_pipeline (LLM 流式 → 句级 TTS)

调用链路：
  main.py::ws_media_fork() → handler.handle()
    → 接收循环: JitterBuffer → Denoiser → VAD → 端点检测
    → _handle_end_of_speech(): ASR 流式/批量 → pre_llm_fn → streaming_fn
    → _receive_during_streaming(): barge-in 并发检测
"""
import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

import numpy as np

from fastapi import WebSocket, WebSocketDisconnect

from config import settings as _settings
from ws.vad import BaseVAD, SimpleVAD
from ws.jitter_buffer import JitterBuffer, TTSOutputBuffer
from ws.registry import ActiveCallRegistry
from ws.denoise import BaseDenoiser, PassThroughDenoiser
from storage import minio_storage

if TYPE_CHECKING:
    from clients.esl import ESLClient
    from clients.asr_grpc_client import ASRGrpcClient, ASRStream
    from clients.asr_ws_client import ASRWebSocketClient, ASRWsStream
    from ws.registry import ActiveCall

logger = logging.getLogger(__name__)


class StreamingCallHandler:
    """流式 WebSocket handler — LLM 流式 → 句级 TTS → 音频按序回传 + Barge-in。

    Barge-in 机制：
    - 流式 TTS 回传期间，并发接收用户音频并运行 VAD
    - 连续 N 帧（600ms）检测到语音 → 取消 LLM/TTS 流 → 开始新一轮
    - AI 未开口前不允许 barge-in（用户不可能打断沉默）
    """

    # 连续语音帧阈值 — 20 × 30ms = 600ms，过滤附和音（嗯/好/对），保留真正打断
    _BARGE_IN_SPEECH_FRAMES = 20
    # RMS 阈值 — 低于此值的帧视为静音/噪声，不参与 barge-in 判定
    _BARGE_IN_RMS_THRESHOLD = 300

    def __init__(
        self,
        pre_llm_fn,
        streaming_fn,
        esl: "ESLClient | None" = None,
        handoff_extension: str = "1001",
        registry: ActiveCallRegistry | None = None,
        vad_factory: "callable | None" = None,
        barge_in_min_audio_bytes: int = 1600,
        jitter_target_depth: int = 3,
        jitter_max_depth: int = 10,
        denoiser: BaseDenoiser | None = None,
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
        self._vad_factory = vad_factory
        self._barge_in_min_audio_bytes = barge_in_min_audio_bytes
        self._jitter_target_depth = jitter_target_depth
        self._jitter_max_depth = jitter_max_depth
        self._denoiser = denoiser or PassThroughDenoiser()
        self._asr_grpc_client = asr_grpc_client
        self._use_grpc_streaming = use_grpc_streaming
        self._asr_ws_client = asr_ws_client
        self._use_ws_streaming = use_ws_streaming
        self._use_streaming_asr = use_streaming_asr
        self._tts_prebuffer_frames = tts_prebuffer_frames

    # ───────────────────────────────────────────────────────────────
    # 主循环
    # ───────────────────────────────────────────────────────────────

    async def handle(self, websocket: WebSocket, call_id: str, biz_type: str, user_key: str) -> None:
        """WebSocket 主循环：接收音频 → VAD 端点检测 → 流式管线 → barge-in 并发检测。"""
        await websocket.accept()
        logger.info("[%s] WS connected biz_type=%s user_key=%s", call_id, biz_type, user_key)

        # TTSOutputBuffer: 单 writer 统一出口，拆帧（960B）匀速 30ms 发送，无数据时静音保活
        tts_buffer = TTSOutputBuffer(
            send_fn=websocket.send_bytes,
            prebuffer_frames=self._tts_prebuffer_frames,
        )
        await tts_buffer.start()

        active_call = self._resolve_active_call(call_id, biz_type, user_key)
        vad = self._vad_factory() if self._vad_factory else SimpleVAD()
        jitter = JitterBuffer(target_depth=self._jitter_target_depth, max_depth=self._jitter_max_depth)
        audio_buffer = bytearray()
        audio_gain = _settings.audio_gain
        turn_count = 0

        # ASR streaming state
        asr_stream: ASRStream | None = None
        speech_started = False
        asr_partial_text = ""
        precomputed_asr_result: dict | None = None

        # Barge-in state
        streaming_task: asyncio.Task | None = None
        barge_in_event = asyncio.Event()
        ai_has_spoken = asyncio.Event()
        ai_spoken_buffer_cleared = False
        # list 包装允许内部方法修改外部变量
        barge_grace_until: list[float] = [0.0]
        barge_speech_counter: list[int] = [0]

        try:
            while True:
                if active_call and active_call.cancel.is_set():
                    logger.info("[%s] CHANNEL_HANGUP, stopping", call_id)
                    break

                # ── AI 说话中：并发接收用户音频检测 barge-in ──
                if streaming_task and not streaming_task.done():
                    if ai_has_spoken.is_set() and not ai_spoken_buffer_cleared:
                        # AI 首次开口：清空之前的残余音频，设置 1s grace period
                        audio_buffer.clear()
                        jitter.reset()
                        self._denoiser.reset()
                        ai_spoken_buffer_cleared = True
                        barge_grace_until[0] = time.monotonic() + 1.0
                        barge_speech_counter[0] = 0
                        logger.info("[%s] AI first audio — cleared buffer, grace 1.0s", call_id)

                    barge_detected = await self._receive_during_streaming(
                        websocket, call_id, vad, jitter, audio_buffer,
                        streaming_task, barge_in_event, active_call,
                        barge_grace_until, ai_has_spoken, barge_speech_counter,
                    )

                    if barge_detected:
                        # 取消 ASR 流
                        asr_stream, speech_started = await self._cancel_asr_stream(asr_stream, speech_started)
                        # 处理打断音频
                        turn_count += 1
                        barge_audio = self._apply_gain(bytes(audio_buffer), audio_gain)
                        logger.info("[%s] barge-in → turn %d (%d bytes)", call_id, turn_count, len(barge_audio))
                        await self._process_streaming_turn(
                            websocket, call_id, biz_type, user_key,
                            barge_audio, turn_count, active_call,
                            ai_spoken_event=ai_has_spoken, tts_buffer=tts_buffer,
                        )
                        self._reset_audio_state(audio_buffer, vad, jitter)
                        barge_in_event.clear()
                        streaming_task = None
                        continue
                    elif streaming_task.done():
                        # 正常完成
                        exc = streaming_task.exception()
                        if exc:
                            logger.error("[%s] streaming task error: %s", call_id, exc)
                        else:
                            logger.info("[%s] streaming turn completed", call_id)
                        streaming_task = None
                        self._reset_audio_state(audio_buffer, vad, jitter)
                        barge_in_event.clear()
                        continue

                # ── AI 未说话：正常接收用户音频 ──
                data = await websocket.receive()

                if "bytes" in data and data["bytes"]:
                    frame = data["bytes"]
                    jitter.insert(frame)

                    while True:
                        smooth_frame = jitter.drain()
                        if not smooth_frame:
                            break
                        denoised_frame = self._denoiser.process(smooth_frame)
                        audio_buffer.extend(denoised_frame)

                        # 流式 ASR：实时发送 PCM
                        asr_stream, speech_started = await self._feed_asr_stream(
                            asr_stream, speech_started, denoised_frame,
                            call_id, asr_partial_text,
                        )

                        # VAD 端点检测
                        if vad.is_end_of_speech(denoised_frame, len(audio_buffer)):
                            if active_call and active_call.cancel.is_set():
                                break

                            turn_count += 1
                            # 获取 ASR 结果 + 应用增益
                            raw_audio, precomputed_asr_result, asr_stream, speech_started, asr_partial_text = \
                                await self._finalize_asr_and_gain(
                                    audio_buffer, audio_gain, asr_stream,
                                    speech_started, asr_partial_text, call_id, turn_count,
                                )

                            if not raw_audio:
                                # ASR 空文本 — VAD 误触发，跳过
                                self._reset_audio_state(audio_buffer, vad, jitter)
                                continue

                            # 启动流式管线（并发 task，允许 barge-in）
                            barge_in_event.clear()
                            ai_has_spoken.clear()
                            ai_spoken_buffer_cleared = False
                            barge_grace_until[0] = time.monotonic() + 0.5

                            streaming_task = asyncio.create_task(
                                self._process_streaming_turn(
                                    websocket, call_id, biz_type, user_key,
                                    raw_audio, turn_count, active_call,
                                    barge_in_event=barge_in_event,
                                    precomputed_asr_result=precomputed_asr_result,
                                    ai_spoken_event=ai_has_spoken,
                                    tts_buffer=tts_buffer,
                                ),
                                name=f"stream-{call_id}-{turn_count}",
                            )
                            logger.info("[%s] streaming task launched for turn %d", call_id, turn_count)

                            self._reset_audio_state(audio_buffer, vad, jitter)
                            break  # 回到外层循环进入 barge-in 检测模式

                elif "text" in data and data["text"]:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "stop":
                        logger.info("[%s] WS stop received", call_id)
                        break

        except WebSocketDisconnect:
            logger.info("[%s] WS disconnected after %d turns", call_id, turn_count)
        except RuntimeError:
            logger.info("[%s] WS already disconnected after %d turns", call_id, turn_count)
        except Exception as e:
            logger.error("[%s] WS error: %s", call_id, e, exc_info=True)
        finally:
            await self._cleanup(streaming_task, asr_stream, tts_buffer, call_id, turn_count)

    # ───────────────────────────────────────────────────────────────
    # 音频处理辅助
    # ───────────────────────────────────────────────────────────────

    def _resolve_active_call(self, call_id: str, biz_type: str, user_key: str) -> "ActiveCall | None":
        """获取或注册 ActiveCall。"""
        if not self._registry:
            return None
        active_call = self._registry.get(call_id)
        if not active_call:
            active_call = self._registry.register(call_id, biz_type, user_key)
        return active_call

    @staticmethod
    def _apply_gain(audio: bytes, gain: float) -> bytes:
        """对 PCM 音频应用增益（放大安静 SIP 音频）。"""
        if gain == 1.0 or len(audio) < 2:
            return audio
        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32)
        samples *= gain
        return np.clip(samples, -32768, 32767).astype(np.int16).tobytes()

    def _reset_audio_state(self, audio_buffer: bytearray, vad: BaseVAD, jitter: JitterBuffer) -> None:
        """重置所有音频处理状态，准备下一轮。"""
        audio_buffer.clear()
        vad.reset()
        jitter.reset()
        self._denoiser.reset()

    # ───────────────────────────────────────────────────────────────
    # ASR 流式传输
    # ───────────────────────────────────────────────────────────────

    def _get_asr_provider(self):
        """返回 ASR 流式传输提供者（WS 或 gRPC）。"""
        if self._use_ws_streaming and self._asr_ws_client:
            return self._asr_ws_client
        if self._use_grpc_streaming and self._asr_grpc_client:
            return self._asr_grpc_client
        return None

    async def _feed_asr_stream(
        self, asr_stream: "ASRStream | None", speech_started: bool,
        frame: bytes, call_id: str, asr_partial_text: str,
    ) -> tuple["ASRStream | None", bool]:
        """向 ASR 流发送音频帧。首帧时创建流。"""
        provider = self._get_asr_provider()
        if not provider:
            return asr_stream, speech_started

        if not speech_started:
            speech_started = True

            def _on_asr_partial(text: str, stability: float) -> None:
                nonlocal asr_partial_text
                asr_partial_text = text
                logger.debug("[%s] ASR partial: %s (stability=%.2f)", call_id, text, stability)

            asr_stream = provider.create_stream(
                call_id, streaming=self._use_streaming_asr,
                on_partial=_on_asr_partial if self._use_streaming_asr else None,
            )
            if asr_stream:
                await asr_stream.start()
                logger.info("[%s] ASR stream created", call_id)

        if asr_stream:
            asr_stream.send_audio(frame)

        return asr_stream, speech_started

    async def _finalize_asr_and_gain(
        self, audio_buffer: bytearray, audio_gain: float,
        asr_stream: "ASRStream | None", speech_started: bool,
        asr_partial_text: str, call_id: str, turn: int,
    ) -> tuple[bytes, dict | None, "ASRStream | None", bool, str]:
        """完成 ASR 流获取结果，应用音频增益。

        Returns: (raw_audio, precomputed_asr_result, asr_stream, speech_started, asr_partial_text)
        """
        raw_audio = self._apply_gain(bytes(audio_buffer), audio_gain)
        if audio_gain != 1.0:
            logger.debug("[%s] audio gain %.1fx applied", call_id, audio_gain)

        # ASR 帧级别 amplitude 统计（debug）
        if raw_audio:
            _s = np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32)
            logger.debug("[%s] audio stats: rms=%.0f min=%.0f max=%.0f",
                         call_id, float(np.sqrt(np.mean(_s**2))), float(_s.min()), float(_s.max()))

        precomputed_asr_result = None
        if asr_stream:
            precomputed_asr_result = await asr_stream.finish()
            asr_stream = None
            speech_started = False
            if not precomputed_asr_result and asr_partial_text:
                precomputed_asr_result = {"text": asr_partial_text, "confidence": 0.8, "is_final": True}
                logger.info("[%s] ASR partial fallback: %s", call_id, asr_partial_text[:50])
            asr_partial_text = ""

        # ASR 空文本 → VAD 误触发
        asr_text = precomputed_asr_result.get("text", "").strip() if precomputed_asr_result else ""
        if not asr_text:
            logger.info("[%s] ASR empty, skipping turn %d (VAD false positive)", call_id, turn)
            return b"", precomputed_asr_result, asr_stream, speech_started, asr_partial_text

        return raw_audio, precomputed_asr_result, asr_stream, speech_started, asr_partial_text

    @staticmethod
    async def _cancel_asr_stream(asr_stream: "ASRStream | None", speech_started: bool):
        """取消 ASR 流。"""
        if asr_stream is not None:
            try:
                await asr_stream.cancel()
            except Exception:
                pass
        return None, False

    # ───────────────────────────────────────────────────────────────
    # Barge-in 检测
    # ───────────────────────────────────────────────────────────────

    async def _receive_during_streaming(
        self,
        websocket: WebSocket,
        call_id: str,
        vad: BaseVAD,
        jitter: JitterBuffer,
        audio_buffer: bytearray,
        streaming_task: asyncio.Task,
        barge_in_event: asyncio.Event,
        active_call: "ActiveCall | None",
        grace_until: list[float],
        ai_spoken_event: asyncio.Event,
        speech_counter: list[int],
    ) -> bool:
        """AI 说话时并发接收用户音频，检测 barge-in。

        条件：不在 grace period + AI 已开口 + 累积音频足够 + 连续 N 帧语音。
        """
        try:
            data = await asyncio.wait_for(websocket.receive(), timeout=0.1)
        except asyncio.TimeoutError:
            # 无数据 → 重置持续语音计数
            speech_counter[0] = 0
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
                denoised_frame = self._denoiser.process(smooth_frame)
                audio_buffer.extend(denoised_frame)

                # Barge-in 判定
                in_grace = time.monotonic() < grace_until[0]
                ai_speaking = ai_spoken_event.is_set()
                has_enough_audio = len(audio_buffer) >= self._barge_in_min_audio_bytes

                if not in_grace and ai_speaking and has_enough_audio and len(denoised_frame) >= 320:
                    _f32 = np.frombuffer(denoised_frame, dtype=np.int16).astype(np.float32)
                    frame_rms = float(np.sqrt(np.mean(_f32**2)))
                    is_speech = frame_rms > self._BARGE_IN_RMS_THRESHOLD and vad.is_speech(denoised_frame)

                    if is_speech:
                        speech_counter[0] += 1
                        if speech_counter[0] >= self._BARGE_IN_SPEECH_FRAMES:
                            logger.info("[%s] barge-in: %d consecutive speech frames, %d bytes, rms=%.0f",
                                        call_id, speech_counter[0], len(audio_buffer), frame_rms)
                            streaming_task.cancel()
                            barge_in_event.set()
                            remaining = jitter.drain_all()
                            if remaining:
                                audio_buffer.extend(remaining)
                            return True
                    elif speech_counter[0] > 0:
                        speech_counter[0] = 0

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
    ) -> None:
        """Phase 1 (pre-LLM) + Phase 2 (streaming LLM+TTS) → TTSOutputBuffer 回传。"""
        downstream_pcm = bytearray()
        t0 = time.monotonic()
        try:
            if active_call and active_call.cancel.is_set():
                return

            # Phase 1: Pre-LLM
            state = await self._pre_llm_fn(
                call_id, biz_type, user_key, audio,
                precomputed_asr_result=precomputed_asr_result,
            )

            if barge_in_event and barge_in_event.is_set():
                logger.info("[%s] barge-in during pre-llm, aborting", call_id)
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
                downstream_pcm.extend(pcm)

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

            # 保存本轮音频（fire-and-forget）
            if audio or downstream_pcm:
                await minio_storage.save_turn_audio(
                    upstream_pcm=audio,
                    downstream_pcm=bytes(downstream_pcm),
                    call_id=call_id,
                    turn=turn,
                    downstream_sr=_settings.media_sample_rate,
                )

            elapsed = (time.monotonic() - t0) * 1000
            logger.info("[%s] turn %d done in %.0fms, %d bytes downstream",
                        call_id, turn, elapsed, len(downstream_pcm))

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
        self, streaming_task: asyncio.Task | None, asr_stream: "ASRStream | None",
        tts_buffer: TTSOutputBuffer, call_id: str, turn_count: int,
    ) -> None:
        """清理所有资源。"""
        if streaming_task and not streaming_task.done():
            streaming_task.cancel()
            try:
                await streaming_task
            except (asyncio.CancelledError, Exception):
                pass
        if asr_stream is not None:
            try:
                await asr_stream.cancel()
            except Exception:
                pass
        await tts_buffer.stop()
        if self._registry:
            self._registry.unregister(call_id)
        logger.info("[%s] WS closed, total turns=%d", call_id, turn_count)
