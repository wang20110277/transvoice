"""WebSocket 通话处理 — 流式音频回传 + ESL 通话控制"""
import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect

from ws.vad import SimpleVAD
from ws.registry import ActiveCallRegistry

if TYPE_CHECKING:
    from clients.esl import ESLClient
    from ws.registry import ActiveCall

logger = logging.getLogger(__name__)


class CallWebSocketHandler:
    """处理单通 WebSocket 通话 — 同步管线（HTTP 端点回退用）。"""

    def __init__(
        self,
        turn_fn,
        esl: "ESLClient | None" = None,
        handoff_extension: str = "1001",
        vad_silence_threshold: float = 500.0,
        vad_silence_frames: int = 15,
        vad_min_audio_bytes: int = 3200,
    ) -> None:
        self._turn_fn = turn_fn
        self._esl = esl
        self._handoff_extension = handoff_extension
        self._vad_silence_threshold = vad_silence_threshold
        self._vad_silence_frames = vad_silence_frames
        self._vad_min_audio_bytes = vad_min_audio_bytes

    async def handle(self, websocket: WebSocket, call_id: str, biz_type: str, user_key: str) -> None:
        await websocket.accept()
        logger.info("[%s] WS call connected biz_type=%s user_key=%s", call_id, biz_type, user_key)

        vad = SimpleVAD(
            silence_threshold=self._vad_silence_threshold,
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


class StreamingCallHandler:
    """流式 WebSocket handler — LLM 流式输出 → 句级 TTS → 音频按序回传 FreeSWITCH。

    协议：
    - 连接: ws://host:port/ws/call?call_id=x&biz_type=marketing&user_key=138xxx
    - 接收: binary frames (PCM 16-bit raw bytes)
    - 发送: binary frames (PCM 16-bit TTS 音频，按句拆分流式发送)
    - 控制: JSON text frames {"type": "action", "action": "say|ask|handoff|end"}
    """

    def __init__(
        self,
        pre_llm_fn,
        streaming_fn,
        esl: "ESLClient | None" = None,
        handoff_extension: str = "1001",
        registry: ActiveCallRegistry | None = None,
        vad_silence_threshold: float = 500.0,
        vad_silence_frames: int = 15,
        vad_min_audio_bytes: int = 3200,
    ) -> None:
        self._pre_llm_fn = pre_llm_fn
        self._streaming_fn = streaming_fn
        self._esl = esl
        self._handoff_extension = handoff_extension
        self._registry = registry
        self._vad_silence_threshold = vad_silence_threshold
        self._vad_silence_frames = vad_silence_frames
        self._vad_min_audio_bytes = vad_min_audio_bytes

    async def handle(self, websocket: WebSocket, call_id: str, biz_type: str, user_key: str) -> None:
        await websocket.accept()
        logger.info("[%s] streaming WS connected biz_type=%s", call_id, biz_type)

        # Register active call for CHANNEL_HANGUP cancellation
        active_call = None
        if self._registry:
            active_call = self._registry.register(call_id, biz_type)

        vad = SimpleVAD(
            silence_threshold=self._vad_silence_threshold,
            silence_frames=self._vad_silence_frames,
            min_audio_bytes=self._vad_min_audio_bytes,
        )
        audio_buffer = bytearray()
        turn_count = 0

        try:
            while True:
                # Check if caller hung up (signaled by CHANNEL_HANGUP event)
                if active_call and active_call.cancel.is_set():
                    logger.info("[%s] call cancelled (CHANNEL_HANGUP), stopping", call_id)
                    break

                data = await websocket.receive()

                if "bytes" in data and data["bytes"]:
                    frame = data["bytes"]
                    audio_buffer.extend(frame)

                    if vad.is_end_of_speech(frame, len(audio_buffer)):
                        # Check cancellation before processing
                        if active_call and active_call.cancel.is_set():
                            logger.info("[%s] call cancelled during turn processing", call_id)
                            break

                        turn_count += 1
                        t0 = time.monotonic()
                        logger.info("[%s] end-of-speech, streaming turn %d (%d bytes)",
                                    call_id, turn_count, len(audio_buffer))

                        await self._process_streaming_turn(
                            websocket, call_id, biz_type, user_key,
                            bytes(audio_buffer), turn_count, active_call,
                        )

                        elapsed = (time.monotony() - t0) * 1000
                        logger.info("[%s] turn %d done in %.0fms", call_id, turn_count, elapsed)

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
            logger.error("[%s] streaming WS error: %s", call_id, e, exc_info=True)
        finally:
            if self._registry:
                self._registry.unregister(call_id)
            logger.info("[%s] streaming WS ended, total turns=%d", call_id, turn_count)

    async def _process_streaming_turn(
        self,
        websocket: WebSocket,
        call_id: str,
        biz_type: str,
        user_key: str,
        audio: bytes,
        turn: int,
        active_call: "ActiveCall | None" = None,
    ) -> None:
        """运行流式管线：Pre-LLM 阶段 → 流式 LLM+TTS → 音频按序回传。"""
        try:
            # Check cancellation before starting pipeline
            if active_call and active_call.cancel.is_set():
                return
            # Phase 1: Pre-LLM (ASR + parallel MCP/Memory/RAG)
            state = await self._pre_llm_fn(call_id, biz_type, user_key, audio)

            # Phase 2: Streaming LLM+TTS with ordered audio delivery
            next_to_send = 0
            pending: dict[int, bytes] = {}
            stream_done = asyncio.Event()

            async def audio_callback(pcm: bytes, index: int) -> None:
                nonlocal next_to_send
                pending[index] = pcm

                # Send all consecutive chunks starting from next_to_send
                while next_to_send in pending:
                    chunk = pending.pop(next_to_send)
                    try:
                        await websocket.send_bytes(chunk)
                        logger.debug("[%s] sent audio chunk %d (%d bytes)",
                                     call_id, next_to_send, len(chunk))
                    except Exception as e:
                        logger.error("[%s] send audio chunk %d failed: %s", call_id, next_to_send, e)
                        break
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
