"""WebSocket 通话处理 — 流式音频回传 + ESL 通话控制 + Barge-in 打断"""
import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect

from ws.vad import SimpleVAD
from ws.jitter_buffer import JitterBuffer
from ws.registry import ActiveCallRegistry
from ws.denoise import BaseDenoiser, PassThroughDenoiser
from storage import minio_storage

if TYPE_CHECKING:
    from clients.esl import ESLClient
    from clients.asr_grpc_client import ASRGrpcClient, ASRStream
    from clients.asr_ws_client import ASRWebSocketClient, ASRWsStream
    from ws.registry import ActiveCall

logger = logging.getLogger(__name__)


class CallWebSocketHandler:
    """处理单通 WebSocket 通话 — 同步管线（HTTP 端点回退用）。"""

    def __init__(
        self,
        turn_fn,
        esl: "ESLClient | None" = None,
        handoff_extension: str = "1001",
        vad_aggressiveness: int = 3,
        vad_silence_frames: int = 15,
        vad_min_audio_bytes: int = 3200,
    ) -> None:
        self._turn_fn = turn_fn
        self._esl = esl
        self._handoff_extension = handoff_extension
        self._vad_aggressiveness = vad_aggressiveness
        self._vad_silence_frames = vad_silence_frames
        self._vad_min_audio_bytes = vad_min_audio_bytes

    async def handle(self, websocket: WebSocket, call_id: str, biz_type: str, user_key: str) -> None:
        await websocket.accept()
        logger.info("[%s] WS call connected biz_type=%s user_key=%s", call_id, biz_type, user_key)

        vad = SimpleVAD(
            aggressiveness=self._vad_aggressiveness,
            silence_frames=self._vad_silence_frames,
            min_audio_bytes=self._vad_min_audio_bytes,
        )
        audio_buffer = bytearray()
        turn_count = 0

        try:
            while True:
                data = await websocket.receive()

                if "bytes" in data and data["bytes"]:
                    frame = data["bytes"]
                    audio_buffer.extend(frame)

                    if vad.is_end_of_speech(frame, len(audio_buffer)):
                        turn_count += 1
                        logger.info("[%s] end-of-speech, processing turn %d (%d bytes)",
                                    call_id, turn_count, len(audio_buffer))

                        result = await self._process_turn(
                            call_id, biz_type, user_key, bytes(audio_buffer), turn_count
                        )

                        await self._send_response(websocket, result, call_id)

                        audio_buffer.clear()
                        vad.reset()

                elif "text" in data and data["text"]:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "stop":
                        logger.info("[%s] WS stop received", call_id)
                        break

        except WebSocketDisconnect:
            logger.info("[%s] WS disconnected after %d turns", call_id, turn_count)
        except Exception as e:
            logger.error("[%s] WS error: %s", call_id, e, exc_info=True)
        finally:
            logger.info("[%s] WS call ended, total turns=%d", call_id, turn_count)

    async def _process_turn(
        self, call_id: str, biz_type: str, user_key: str, audio: bytes, turn: int
    ) -> dict:
        try:
            result = await self._turn_fn(call_id, biz_type, user_key, audio)
            return result
        except Exception as e:
            logger.error("[%s] turn %d pipeline error: %s", call_id, turn, e, exc_info=True)
            return {"action": "say", "action_text": "抱歉，请再说一遍。", "tts_audio_path": None}

    async def _send_response(self, websocket: WebSocket, result: dict, call_id: str) -> None:
        action = result.get("action", "say")

        try:
            await websocket.send_json({"type": "action", "action": action, "turn": result.get("turn", 0)})
        except Exception as e:
            logger.error("[%s] send action failed: %s", call_id, e)
            return

        audio_path = result.get("tts_audio_path")
        if audio_path:
            try:
                audio_bytes = await asyncio.to_thread(self._read_file, audio_path)
                if audio_bytes:
                    await websocket.send_bytes(audio_bytes)
                    logger.debug("[%s] sent TTS audio %d bytes", call_id, len(audio_bytes))
            except Exception as e:
                logger.error("[%s] send audio failed: %s", call_id, e)

        if action in ("end", "handoff"):
            await self._execute_terminal_action(action, call_id)

    @staticmethod
    def _read_file(path: str) -> bytes | None:
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return None

    async def _execute_terminal_action(self, action: str, call_id: str) -> None:
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
        vad_aggressiveness: int = 3,
        vad_silence_frames: int = 15,
        vad_min_audio_bytes: int = 3200,
        barge_in_min_audio_bytes: int = 1600,
        jitter_target_depth: int = 3,
        jitter_max_depth: int = 10,
        denoiser: BaseDenoiser | None = None,
        asr_grpc_client: "ASRGrpcClient | None" = None,
        use_grpc_streaming: bool = False,
        asr_ws_client: "ASRWebSocketClient | None" = None,
        use_ws_streaming: bool = False,
        use_streaming_asr: bool = False,
    ) -> None:
        self._pre_llm_fn = pre_llm_fn
        self._streaming_fn = streaming_fn
        self._esl = esl
        self._handoff_extension = handoff_extension
        self._registry = registry
        self._vad_aggressiveness = vad_aggressiveness
        self._vad_silence_frames = vad_silence_frames
        self._vad_min_audio_bytes = vad_min_audio_bytes
        self._barge_in_min_audio_bytes = barge_in_min_audio_bytes
        self._jitter_target_depth = jitter_target_depth
        self._jitter_max_depth = jitter_max_depth
        self._denoiser = denoiser or PassThroughDenoiser()
        self._asr_grpc_client = asr_grpc_client
        self._use_grpc_streaming = use_grpc_streaming
        self._asr_ws_client = asr_ws_client
        self._use_ws_streaming = use_ws_streaming
        self._use_streaming_asr = use_streaming_asr

    # 30ms 静音帧 @ 16kHz 16-bit mono — 用于连接建立后的保活
    _SILENCE_FRAME = b'\x00' * 960

    async def handle(self, websocket: WebSocket, call_id: str, biz_type: str, user_key: str) -> None:
        await websocket.accept()
        logger.info("[%s] streaming WS connected biz_type=%s", call_id, biz_type)

        # 立即启动静音保活：在 TTS 音频到来前持续发送静音帧，防止 FreeSWITCH 媒体超时
        # 使用局部变量而非实例属性，避免并发通话互相覆盖
        _silence_stop = asyncio.Event()
        async def _silence_keepalive():
            while not _silence_stop.is_set():
                try:
                    await websocket.send_bytes(self._SILENCE_FRAME)
                    await asyncio.sleep(0.03)
                except Exception as e:
                    logger.warning("[%s] silence keepalive stopped: %s", call_id, e)
                    break
        _silence_task = asyncio.create_task(_silence_keepalive(), name="silence-keepalive")

        active_call = None
        if self._registry:
            active_call = self._registry.get(call_id)
            if not active_call:
                active_call = self._registry.register(call_id, biz_type, user_key)

        vad = SimpleVAD(
            aggressiveness=self._vad_aggressiveness,
            silence_frames=self._vad_silence_frames,
            min_audio_bytes=self._vad_min_audio_bytes,
        )
        jitter = JitterBuffer(
            target_depth=self._jitter_target_depth,
            max_depth=self._jitter_max_depth,
        )
        audio_buffer = bytearray()
        turn_count = 0
        # Barge-in state
        streaming_task: asyncio.Task | None = None
        barge_in_event = asyncio.Event()
        # gRPC ASR streaming state
        asr_stream: ASRStream | None = None
        speech_started = False
        precomputed_asr_result: dict | None = None
        # Streaming ASR partial text tracking
        asr_partial_text = ""

        try:
            while True:
                if active_call and active_call.cancel.is_set():
                    logger.info("[%s] call cancelled (CHANNEL_HANGUP), stopping", call_id)
                    break

                # If a streaming task is running, concurrently receive audio
                # to detect barge-in while AI is speaking
                if streaming_task and not streaming_task.done():
                    barge_detected = await self._receive_during_streaming(
                        websocket, call_id, vad, jitter, audio_buffer,
                        streaming_task, barge_in_event, active_call,
                    )
                    if barge_detected:
                        # Cancel streaming and start processing the interruption
                        turn_count += 1
                        logger.info("[%s] barge-in detected, processing turn %d", call_id, turn_count)

                        # Use buffered audio (user was speaking during AI playback)
                        await self._process_streaming_turn(
                            websocket, call_id, biz_type, user_key,
                            bytes(audio_buffer), turn_count, active_call,
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
                    jitter.insert(frame)

                    # Drain jitter buffer into VAD
                    while True:
                        smooth_frame = jitter.drain()
                        if not smooth_frame:
                            break
                        denoised_frame = self._denoiser.process(smooth_frame)
                        audio_buffer.extend(denoised_frame)

                        # Streaming ASR: WS > gRPC (same interface)
                        asr_provider = self._asr_ws_client if self._use_ws_streaming else (
                            self._asr_grpc_client if self._use_grpc_streaming else None
                        )
                        if asr_provider:
                            if not speech_started and vad.is_speech(denoised_frame):
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

                            # Launch streaming as a concurrent task so we can
                            # receive audio for barge-in while it runs
                            barge_in_event.clear()
                            streaming_task = asyncio.create_task(
                                self._process_streaming_turn(
                                    websocket, call_id, biz_type, user_key,
                                    bytes(audio_buffer), turn_count, active_call,
                                    barge_in_event=barge_in_event,
                                    precomputed_asr_result=precomputed_asr_result,
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
            _silence_stop.set()
            if _silence_task and not _silence_task.done():
                _silence_task.cancel()
            if self._registry:
                self._registry.unregister(call_id)
            logger.info("[%s] streaming WS ended, total turns=%d", call_id, turn_count)

    async def _receive_during_streaming(
        self,
        websocket: WebSocket,
        call_id: str,
        vad: SimpleVAD,
        jitter: JitterBuffer,
        audio_buffer: bytearray,
        streaming_task: asyncio.Task,
        barge_in_event: asyncio.Event,
        active_call: "ActiveCall | None",
    ) -> bool:
        """Receive audio while streaming TTS is in progress. Returns True if barge-in detected.

        This method polls the WebSocket for new audio frames, feeds them through the
        jitter buffer and VAD. If user speech is detected, it cancels the streaming
        task and signals barge-in.
        """
        try:
            # Use a short timeout so we can check streaming_task completion
            data = await asyncio.wait_for(websocket.receive(), timeout=0.1)
        except asyncio.TimeoutError:
            # No data received within timeout — check if streaming finished
            return False
        except WebSocketDisconnect:
            streaming_task.cancel()
            raise  # 向外层传播断连，让 handle() 的 except WebSocketDisconnect 统一处理
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

                # Barge-in VAD: use shorter thresholds for faster detection
                # during AI speech — just need to detect that user started talking
                if len(audio_buffer) >= self._barge_in_min_audio_bytes and self._is_speech_frame(denoised_frame, vad):
                    logger.info("[%s] barge-in: user speech detected (%d bytes buffered)",
                                call_id, len(audio_buffer))

                    # 1. Cancel streaming task
                    streaming_task.cancel()
                    barge_in_event.set()

                    # 2. Stop FreeSWITCH media playback via ESL
                    await self._break_media(call_id)

                    # 3. Drain remaining jitter buffer into audio_buffer
                    remaining = jitter.drain_all()
                    if remaining:
                        audio_buffer.extend(remaining)

                    return True

        elif "text" in data and data["text"]:
            msg = json.loads(data["text"])
            if msg.get("type") == "stop":
                streaming_task.cancel()
                return False

        return False

    def _is_speech_frame(self, frame: bytes, vad: SimpleVAD) -> bool:
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
    ) -> None:
        """运行流式管线：Pre-LLM → 流式 LLM+TTS → 音频直接回传。

        静音保活在 handle() 中全程运行，保证连接不断。
        TTS PCM 直接通过 WebSocket 发送，由 FreeSWITCH 内部缓冲处理 pacing。
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

            # Phase 2: Streaming LLM+TTS — TTS PCM 直接发送，不做帧 pacing
            next_to_send = 0
            pending: dict[int, list[bytes]] = {}
            _ws_broken = False

            async def audio_callback(pcm: bytes, index: int) -> None:
                nonlocal next_to_send, _ws_broken

                if _ws_broken or (barge_in_event and barge_in_event.is_set()):
                    return

                if index not in pending:
                    pending[index] = []
                pending[index].append(pcm)
                downstream_pcm.extend(pcm)

                # 按句序直接发送，FreeSWITCH 内部缓冲处理 pacing
                while next_to_send in pending:
                    chunks = pending.pop(next_to_send)
                    for chunk in chunks:
                        try:
                            await websocket.send_bytes(chunk)
                        except Exception as e:
                            logger.error("[%s] send audio error: %s", call_id, e)
                            _ws_broken = True
                            return
                    logger.debug("[%s] sent TTS sentence %d (%d chunks, %d bytes)",
                                 call_id, next_to_send, len(chunks), sum(len(c) for c in chunks))
                    next_to_send += 1

            async def action_callback(action: str) -> None:
                try:
                    await websocket.send_json({
                        "type": "action",
                        "action": action,
                        "turn": turn,
                    })
                except Exception as e:
                    logger.error("[%s] send action failed: %s", call_id, e)
                if action in ("end", "handoff") and self._esl:
                    await self._execute_terminal_action(action, call_id)

            await self._streaming_fn(state, audio_callback, action_callback)

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
