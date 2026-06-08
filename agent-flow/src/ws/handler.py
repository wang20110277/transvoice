"""WebSocket 通话处理 — 流式音频回传 + ESL 通话控制 + Barge-in 打断"""
import asyncio
import json
import logging
import time
import numpy as np
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect

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
    """流式 WebSocket handler — LLM 流式输出 → 句级 TTS → 音频按序回传 + Barge-in 打断。

    协议：
    - 连接: ws://host:port/ws/streaming-call?call_id=x&biz_type=marketing&user_key=138xxx
    - 接收: binary frames (PCM 16-bit raw bytes)
    - 发送: binary frames (PCM 16-bit TTS 音频，按句拆分流式发送)
    - 控制: JSON text frames {"type": "action", "action": "say|ask|handoff|end"}

    Barge-in 机制：
    - 流式 TTS 回传期间，并发接收用户音频并运行 VAD
    - VAD 检测到用户说话 → 取消当前 LLM/TTS 流 → ESL uuid_break 停止 FreeSWITCH 播放
    - 清空缓冲区，开始处理新的用户输入
    """

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

    # 30ms 静音帧 @ 16kHz 16-bit mono — 用于连接建立后的保活
    _SILENCE_FRAME = b'\x00' * 960

    async def handle(self, websocket: WebSocket, call_id: str, biz_type: str, user_key: str) -> None:
        await websocket.accept()
        logger.info("[%s] streaming WS connected biz_type=%s", call_id, biz_type)

        # TTSOutputBuffer: 单 writer 统一出口，将 TTS PCM 拆为固定 960B 帧匀速发送。
        # 无 TTS 数据时自动发静音帧保活，替代独立的 silence keepalive task。
        # 避免两个 writer 并发写 WebSocket 导致帧不对齐。
        tts_buffer = TTSOutputBuffer(
            send_fn=websocket.send_bytes,
            prebuffer_frames=self._tts_prebuffer_frames,
        )
        await tts_buffer.start()

        active_call = None
        if self._registry:
            active_call = self._registry.get(call_id)
            if not active_call:
                active_call = self._registry.register(call_id, biz_type, user_key)

        vad = self._vad_factory() if self._vad_factory else SimpleVAD()
        jitter = JitterBuffer(
            target_depth=self._jitter_target_depth,
            max_depth=self._jitter_max_depth,
        )
        audio_buffer = bytearray()
        turn_count = 0
        # Barge-in state
        streaming_task: asyncio.Task | None = None
        barge_in_event = asyncio.Event()
        # Barge-in grace period: end-of-speech 后短暂忽略音频，防止残余帧误判
        # list 包装让 _receive_during_streaming 能读到 AI 开口时更新的值
        _barge_in_grace_until: list[float] = [0.0]
        # 持续语音帧计数 — 需要连续 N 帧检测到语音才触发 barge-in，防止回声/噪声单帧误触发
        _barge_speech_counter: list[int] = [0]
        # AI 是否已发出过 TTS 音频 — 只有 AI 开口后才允许 barge-in
        _ai_has_spoken = asyncio.Event()
        # AI 开口后是否已清空累积音频 — 防止开口前的音频被误判为 barge-in
        _ai_spoken_buffer_cleared = False
        # gRPC ASR streaming state
        asr_stream: ASRStream | None = None
        speech_started = False
        precomputed_asr_result: dict | None = None
        # Streaming ASR partial text tracking
        asr_partial_text = ""
        # Audio gain (amplify quiet SIP audio before ASR)
        from config import settings as _settings
        _gain = _settings.audio_gain

        try:
            while True:
                if active_call and active_call.cancel.is_set():
                    logger.info("[%s] call cancelled (CHANNEL_HANGUP), stopping", call_id)
                    break

                # If a streaming task is running, concurrently receive audio
                # to detect barge-in while AI is speaking
                if streaming_task and not streaming_task.done():
                    # AI 开口瞬间，清空之前累积的音频（用户残余语音/噪声），
                    # 只保留 AI 开口之后收到的音频用于 barge-in 检测。
                    # 同时重置 grace period：AI 首句播放的头 1 秒内忽略用户音频，
                    # 避免 TTS 延迟期间累积的背景噪声被误判为 barge-in。
                    if _ai_has_spoken.is_set() and not _ai_spoken_buffer_cleared:
                        audio_buffer.clear()
                        jitter.reset()
                        self._denoiser.reset()
                        _ai_spoken_buffer_cleared = True
                        _barge_in_grace_until[0] = time.monotonic() + 1.0
                        _barge_speech_counter[0] = 0
                        logger.info("[%s] AI started speaking, cleared pre-speech audio buffer, grace 1.0s", call_id)

                    barge_detected = await self._receive_during_streaming(
                        websocket, call_id, vad, jitter, audio_buffer,
                        streaming_task, barge_in_event, active_call,
                        _barge_in_grace_until, _ai_has_spoken,
                        _barge_speech_counter,
                    )
                    if barge_detected:
                        # Cancel active ASR stream on barge-in
                        if asr_stream is not None:
                            try:
                                await asr_stream.cancel()
                            except Exception:
                                pass
                            asr_stream = None
                            speech_started = False

                        # Cancel streaming and start processing the interruption
                        turn_count += 1
                        logger.info("[%s] barge-in detected, processing turn %d", call_id, turn_count)

                        # Use buffered audio (user was speaking during AI playback)
                        _barge_audio = bytes(audio_buffer)
                        if _gain != 1.0 and len(_barge_audio) >= 2:
                            _bs = np.frombuffer(_barge_audio, dtype=np.int16).astype(np.float32)
                            _bs *= _gain
                            _barge_audio = np.clip(_bs, -32768, 32767).astype(np.int16).tobytes()
                        await self._process_streaming_turn(
                            websocket, call_id, biz_type, user_key,
                            _barge_audio, turn_count, active_call,
                            ai_spoken_event=_ai_has_spoken,
                            tts_buffer=tts_buffer,
                        )

                        audio_buffer.clear()
                        vad.reset()
                        jitter.reset()
                        self._denoiser.reset()
                        barge_in_event.clear()
                        streaming_task = None
                        continue
                    else:
                        # Streaming task finished normally or WebSocket closed
                        if streaming_task.done():
                            exc = streaming_task.exception()
                            if exc:
                                logger.error("[%s] streaming task error: %s", call_id, exc)
                            streaming_task = None
                            audio_buffer.clear()
                            vad.reset()
                            jitter.reset()
                            self._denoiser.reset()
                            barge_in_event.clear()
                        continue

                # Normal receive mode (AI not speaking)
                data = await websocket.receive()

                if "bytes" in data and data["bytes"]:
                    frame = data["bytes"]
                    # Debug: raw frame amplitude (first 10 frames only)
                    if not hasattr(self, '_raw_frame_count'):
                        self._raw_frame_count = 0
                    if self._raw_frame_count < 10 and len(frame) >= 2:
                        import numpy as _np2
                        _s = _np2.frombuffer(frame, dtype=_np2.int16).astype(_np2.float32)
                        logger.info("[%s] raw WS frame #%d: %d bytes, min=%.0f max=%.0f rms=%.1f",
                                    call_id, self._raw_frame_count, len(frame),
                                    _s.min(), _s.max(), _np2.sqrt(_np2.mean(_s**2)))
                        self._raw_frame_count += 1
                    jitter.insert(frame)

                    # Drain jitter buffer into VAD
                    while True:
                        smooth_frame = jitter.drain()
                        if not smooth_frame:
                            break
                        denoised_frame = self._denoiser.process(smooth_frame)
                        audio_buffer.extend(denoised_frame)

                        # Streaming ASR: 首帧即创建 stream，全量发送 PCM
                        asr_provider = self._asr_ws_client if self._use_ws_streaming else (
                            self._asr_grpc_client if self._use_grpc_streaming else None
                        )
                        if asr_provider:
                            if not speech_started:
                                speech_started = True

                                def _on_asr_partial(text: str, stability: float) -> None:
                                    nonlocal asr_partial_text
                                    asr_partial_text = text
                                    logger.debug("[%s] ASR partial: %s (stability=%.2f)", call_id, text, stability)

                                asr_stream = asr_provider.create_stream(
                                    call_id, streaming=self._use_streaming_asr,
                                    on_partial=_on_asr_partial if self._use_streaming_asr else None,
                                )
                                if asr_stream:
                                    await asr_stream.start()
                            if asr_stream:
                                asr_stream.send_audio(denoised_frame)

                        if vad.is_end_of_speech(denoised_frame, len(audio_buffer)):
                            if active_call and active_call.cancel.is_set():
                                break

                            turn_count += 1
                            t0 = time.monotonic()
                            logger.info("[%s] end-of-speech, streaming turn %d (%d bytes)",
                                        call_id, turn_count, len(audio_buffer))
                            # Debug: audio amplitude stats + gain for ASR
                            _raw_audio = bytes(audio_buffer)
                            if _gain != 1.0 and len(_raw_audio) >= 2:
                                _samples = np.frombuffer(_raw_audio, dtype=np.int16).astype(np.float32)
                                _samples *= _gain
                                _raw_audio = np.clip(_samples, -32768, 32767).astype(np.int16).tobytes()
                                logger.info("[%s] audio gain %.1fx applied", call_id, _gain)
                            if _raw_audio:
                                _s2 = np.frombuffer(_raw_audio, dtype=np.int16).astype(np.float32)
                                logger.info("[%s] audio stats: min=%.0f max=%.0f mean=%.1f rms=%.1f",
                                            call_id, _s2.min(), _s2.max(),
                                            _s2.mean(), np.sqrt(np.mean(_s2**2)))

                            # gRPC: finish stream and get ASR result
                            precomputed_asr_result = None
                            if asr_stream:
                                precomputed_asr_result = await asr_stream.finish()
                                asr_stream = None
                                speech_started = False
                                # Fallback: use partial text if finish returned nothing
                                if not precomputed_asr_result and asr_partial_text:
                                    precomputed_asr_result = {
                                        "text": asr_partial_text,
                                        "confidence": 0.8,
                                        "is_final": True,
                                    }
                                    logger.info("[%s] ASR using partial text fallback: %s", call_id, asr_partial_text[:50])
                                asr_partial_text = ""

                            # ASR 空文本 → VAD 在噪声上误触发，跳过本轮避免浪费 LLM/TTS
                            _asr_text = precomputed_asr_result.get("text", "").strip() if precomputed_asr_result else ""
                            if not _asr_text:
                                logger.info("[%s] ASR empty, skipping turn %d (noise VAD false positive)", call_id, turn_count)
                                audio_buffer.clear()
                                vad.reset()
                                jitter.reset()
                                self._denoiser.reset()
                                continue

                            # Launch streaming as a concurrent task so we can
                            # receive audio for barge-in while it runs
                            barge_in_event.clear()
                            _ai_has_spoken.clear()
                            _ai_spoken_buffer_cleared = False
                            # Grace period: 500ms 内忽略音频，防止 end-of-speech 残余帧误判 barge-in
                            _barge_in_grace_until[0] = time.monotonic() + 0.5
                            streaming_task = asyncio.create_task(
                                self._process_streaming_turn(
                                    websocket, call_id, biz_type, user_key,
                                    _raw_audio, turn_count, active_call,
                                    barge_in_event=barge_in_event,
                                    precomputed_asr_result=precomputed_asr_result,
                                    ai_spoken_event=_ai_has_spoken,
                                    tts_buffer=tts_buffer,
                                ),
                                name=f"stream-{call_id}-{turn_count}",
                            )

                            audio_buffer.clear()
                            vad.reset()
                            jitter.reset()
                            self._denoiser.reset()
                            break  # Exit to outer loop to enter barge-in mode

                elif "text" in data and data["text"]:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "stop":
                        logger.info("[%s] WS stop received", call_id)
                        break

        except WebSocketDisconnect:
            logger.info("[%s] WS disconnected after %d turns", call_id, turn_count)
        except RuntimeError:
            # WebSocket 已断连（disconnect 消息被其他 receive 消费后触发）
            logger.info("[%s] WS already disconnected after %d turns", call_id, turn_count)
        except Exception as e:
            logger.error("[%s] streaming WS error: %s", call_id, e, exc_info=True)
        finally:
            # Cancel any running streaming task
            if streaming_task and not streaming_task.done():
                streaming_task.cancel()
                try:
                    await streaming_task
                except (asyncio.CancelledError, Exception):
                    pass
            # Cancel any active ASR stream to prevent gRPC task leaks
            if asr_stream is not None:
                try:
                    await asr_stream.cancel()
                except Exception:
                    pass
                asr_stream = None
            await tts_buffer.stop()
            if self._registry:
                self._registry.unregister(call_id)
            logger.info("[%s] streaming WS ended, total turns=%d", call_id, turn_count)

    # Barge-in 需要连续检测到语音的帧数
    # 中文附和音（嗯/好/对）通常 300-500ms，打断意图至少 600ms+
    # 20 × 30ms = 600ms — 过滤附和音，保留真正打断
    _BARGE_IN_SPEECH_FRAMES = 20

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
        grace_until: list[float] | float = 0.0,
        ai_spoken_event: asyncio.Event | None = None,
        speech_counter: list[int] | None = None,
    ) -> bool:
        """Receive audio while streaming TTS is in progress. Returns True if barge-in detected.

        Barge-in 只在 AI 已经发出过 TTS 音频（ai_spoken_event.is_set()）后才触发。
        AI 未开口时用户不可能在"打断"，此时接收到的音频是残余语音/噪声，应丢弃。

        持续语音要求：需要连续 _BARGE_IN_SPEECH_FRAMES 帧同时满足 RMS > 阈值和 VAD
        才触发打断，防止单帧回声/噪声误触发。
        """
        try:
            # Use a short timeout so we can check streaming_task completion
            data = await asyncio.wait_for(websocket.receive(), timeout=0.1)
        except asyncio.TimeoutError:
            # 无数据 → 重置持续语音计数（中断了连续性）
            if speech_counter is not None:
                speech_counter[0] = 0
            return False
        except WebSocketDisconnect:
            streaming_task.cancel()
            raise
        except RuntimeError:
            # WS 已断连（disconnect 消息被其他 receive 消费后触发）— 向外层传播
            streaming_task.cancel()
            raise
        except Exception as e:
            logger.error("[%s] receive during streaming error: %s", call_id, e)
            return False

        if "bytes" in data and data["bytes"]:
            frame = data["bytes"]
            jitter.insert(frame)

            while True:
                smooth_frame = jitter.drain()
                if not smooth_frame:
                    break
                denoised_frame = self._denoiser.process(smooth_frame)
                audio_buffer.extend(denoised_frame)

                # Barge-in VAD: only trigger after AI has spoken (user can't interrupt silence)
                # Grace period: 忽略 end-of-speech 后的残余帧，防止误判
                # Amplitude check: 音频能量过低时忽略（防回声/噪声误判）
                _grace = grace_until[0] if isinstance(grace_until, list) else grace_until
                in_grace = time.monotonic() < _grace
                ai_speaking = ai_spoken_event is not None and ai_spoken_event.is_set()
                _frame_rms = 0.0
                _is_speech = False
                if not in_grace and ai_speaking and len(audio_buffer) >= self._barge_in_min_audio_bytes and len(denoised_frame) >= 320:
                    _f32 = np.frombuffer(denoised_frame, dtype=np.int16).astype(np.float32)
                    _frame_rms = float(np.sqrt(np.mean(_f32**2)))
                    _is_speech = _frame_rms > 300 and self._is_speech_frame(denoised_frame, vad)

                if _is_speech:
                    if speech_counter is not None:
                        speech_counter[0] += 1
                    else:
                        speech_counter_val = self._BARGE_IN_SPEECH_FRAMES
                    if speech_counter is not None and speech_counter[0] >= self._BARGE_IN_SPEECH_FRAMES:
                        logger.info(
                            "[%s] barge-in: sustained speech detected (%d consecutive frames, %d bytes buffered, rms=%.0f)",
                            call_id, speech_counter[0], len(audio_buffer), _frame_rms,
                        )
                        # 1. Cancel streaming task (stops TTS audio output)
                        streaming_task.cancel()
                        barge_in_event.set()

                        # 2. Drain remaining jitter buffer into audio_buffer
                        remaining = jitter.drain_all()
                        if remaining:
                            audio_buffer.extend(remaining)

                        return True
                elif speech_counter is not None and speech_counter[0] > 0:
                    # 非语音帧 → 重置持续计数，要求重新连续检测
                    speech_counter[0] = 0

        elif "text" in data and data["text"]:
            msg = json.loads(data["text"])
            if msg.get("type") == "stop":
                streaming_task.cancel()
                return False

        return False

    def _is_speech_frame(self, frame: bytes, vad: BaseVAD) -> bool:
        """Quick check if a frame contains speech. Used for barge-in detection."""
        return vad.is_speech(frame) if len(frame) >= 320 else False

    async def _break_media(self, call_id: str) -> None:
        """Stop FreeSWITCH media playback via ESL uuid_break."""
        if self._esl is None:
            return
        try:
            result = await self._esl.break_media(call_id)
            logger.info("[%s] ESL break_media (barge-in): %s", call_id, result)
        except Exception as e:
            logger.error("[%s] ESL break_media failed: %s", call_id, e)

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
        """运行流式管线：Pre-LLM → 流式 LLM+TTS → 音频通过 TTSOutputBuffer 回传。

        TTSOutputBuffer 在 handle() 中全程运行，统一所有音频输出：
        拆帧（固定 960B）、匀速 30ms、无数据时自动静音保活。
        """
        downstream_pcm = bytearray()
        try:
            if active_call and active_call.cancel.is_set():
                return

            # Phase 1: Pre-LLM (ASR + parallel MCP/Memory/RAG)
            state = await self._pre_llm_fn(
                call_id, biz_type, user_key, audio,
                precomputed_asr_result=precomputed_asr_result,
            )

            # Check barge-in after pre-LLM phase
            if barge_in_event and barge_in_event.is_set():
                logger.info("[%s] barge-in during pre-llm, aborting stream", call_id)
                return

            # Phase 2: Streaming LLM+TTS — TTS PCM 写入 tts_buffer 统一输出
            next_to_send = 0
            pending: dict[int, list[bytes]] = {}
            # 终端动作（end/handoff）延迟执行：先让 TTS 播完告别语再挂断/转接
            terminal_action: str | None = None

            async def audio_callback(pcm: bytes, index: int) -> None:
                nonlocal next_to_send

                if barge_in_event and barge_in_event.is_set():
                    return

                if index not in pending:
                    pending[index] = []
                pending[index].append(pcm)
                downstream_pcm.extend(pcm)

                # 按句序写入 tts_buffer，由其拆帧匀速发送
                while next_to_send in pending:
                    chunks = pending.pop(next_to_send)
                    for chunk in chunks:
                        if tts_buffer and tts_buffer.is_running:
                            tts_buffer.write(chunk)
                    # AI 首次写入 TTS 音频 — 通知 barge-in 检测可以开始
                    if ai_spoken_event and not ai_spoken_event.is_set():
                        ai_spoken_event.set()
                        logger.info("[%s] AI first audio queued, barge-in detection enabled", call_id)
                    logger.debug("[%s] queued TTS sentence %d (%d chunks, %d bytes)",
                                 call_id, next_to_send, len(chunks), sum(len(c) for c in chunks))
                    next_to_send += 1

            async def action_callback(action: str) -> None:
                nonlocal terminal_action
                try:
                    await websocket.send_json({
                        "type": "action",
                        "action": action,
                        "turn": turn,
                    })
                except Exception as e:
                    logger.error("[%s] send action failed: %s", call_id, e)
                # 延迟执行终端动作：先播完 TTS 再挂断/转接，确保告别语被听到
                if action in ("end", "handoff"):
                    terminal_action = action

            await self._streaming_fn(state, audio_callback, action_callback)

            # TTS 音频已全部写入 tts_buffer，等待缓冲区排空（音频播完）再执行终端动作
            if terminal_action and tts_buffer:
                logger.info("[%s] terminal action '%s': waiting for TTS to drain", call_id, terminal_action)
                await tts_buffer.wait_drained(timeout=10.0)
                await self._execute_terminal_action(terminal_action, call_id)

            # Save upstream + downstream audio for this turn (fire-and-forget)
            if audio or downstream_pcm:
                from config import settings
                downstream_sr = settings.media_sample_rate
                await minio_storage.save_turn_audio(
                    upstream_pcm=audio,
                    downstream_pcm=bytes(downstream_pcm),
                    call_id=call_id,
                    turn=turn,
                    downstream_sr=downstream_sr,
                )

        except asyncio.CancelledError:
            logger.info("[%s] streaming turn %d cancelled (barge-in)", call_id, turn)
        except Exception as e:
            logger.error("[%s] streaming turn %d error: %s", call_id, turn, e, exc_info=True)
            try:
                await websocket.send_json({
                    "type": "action",
                    "action": "say",
                    "text": "抱歉，请再说一遍。",
                    "turn": turn,
                })
            except Exception:
                pass

    async def _execute_terminal_action(self, action: str, call_id: str) -> None:
        """通过 ESL 执行终态动作（挂断/转接）。"""
        if self._esl is None:
            logger.warning("[%s] ESL not available, cannot execute action: %s", call_id, action)
            return
        try:
            if action == "end":
                result = await self._esl.hangup(call_id)
                logger.info("[%s] ESL hangup: %s", call_id, result)
            elif action == "handoff":
                result = await self._esl.transfer(call_id, self._handoff_extension)
                logger.info("[%s] ESL transfer to %s: %s", call_id, self._handoff_extension, result)
        except Exception as e:
            logger.error("[%s] ESL action %s failed: %s", call_id, action, e)
