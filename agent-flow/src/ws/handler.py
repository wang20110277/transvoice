"""WebSocket 通话处理 — 接收 PCM 音频，运行全流程，返回 TTS 音频"""
import asyncio
import base64
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from ws.vad import SimpleVAD

logger = logging.getLogger(__name__)


class CallWebSocketHandler:
    """处理单通 WebSocket 通话的全流程。

    协议：
    - 连接: ws://host:port/ws/call?call_id=x&biz_type=marketing&user_key=138xxx
    - 接收: binary frames (PCM 16-bit raw bytes)
    - 发送: binary frames (WAV PCM 16-bit TTS 音频)
    - 控制: JSON text frames {"type": "action", "action": "say|ask|handoff|end"}
    """

    def __init__(
        self,
        turn_fn,  # async (call_id, biz_type, user_key, audio_bytes) -> dict
        vad_silence_threshold: float = 500.0,
        vad_silence_frames: int = 15,
        vad_min_audio_bytes: int = 3200,
    ) -> None:
        self._turn_fn = turn_fn
        self._vad_silence_threshold = vad_silence_threshold
        self._vad_silence_frames = vad_silence_frames
        self._vad_min_audio_bytes = vad_min_audio_bytes

    async def handle(self, websocket: WebSocket, call_id: str, biz_type: str, user_key: str) -> None:
        """处理一通 WebSocket 通话。"""
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
                # Receive raw data (binary or text)
                data = await websocket.receive()

                if "bytes" in data and data["bytes"]:
                    frame = data["bytes"]
                    audio_buffer.extend(frame)

                    # Check end-of-speech via VAD
                    if vad.is_end_of_speech(frame, len(audio_buffer)):
                        turn_count += 1
                        logger.info("[%s] end-of-speech, processing turn %d (%d bytes)",
                                    call_id, turn_count, len(audio_buffer))

                        # Run full pipeline: ASR → ... → TTS
                        result = await self._process_turn(
                            call_id, biz_type, user_key, bytes(audio_buffer), turn_count
                        )

                        # Send response
                        await self._send_response(websocket, result, call_id)

                        # Reset for next turn
                        audio_buffer.clear()
                        vad.reset()

                elif "text" in data and data["text"]:
                    # Text control message
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
        """调用全流程 pipeline 并返回结果。"""
        try:
            result = await self._turn_fn(call_id, biz_type, user_key, audio)
            return result
        except Exception as e:
            logger.error("[%s] turn %d pipeline error: %s", call_id, turn, e, exc_info=True)
            return {"action": "say", "action_text": "抱歉，请再说一遍。", "tts_audio_path": None}

    async def _send_response(self, websocket: WebSocket, result: dict, call_id: str) -> None:
        """发送 TTS 音频和 action 给 FreeSWITCH。"""
        action = result.get("action", "say")

        # Send action as JSON text frame
        try:
            await websocket.send_json({"type": "action", "action": action, "turn": result.get("turn", 0)})
        except Exception as e:
            logger.error("[%s] send action failed: %s", call_id, e)
            return

        # Send TTS audio as binary frame
        audio_path = result.get("tts_audio_path")
        if audio_path:
            try:
                audio_bytes = await asyncio.to_thread(self._read_file, audio_path)
                if audio_bytes:
                    await websocket.send_bytes(audio_bytes)
                    logger.debug("[%s] sent TTS audio %d bytes", call_id, len(audio_bytes))
            except Exception as e:
                logger.error("[%s] send audio failed: %s", call_id, e)

        # Handle terminal actions
        if action in ("end", "handoff"):
            logger.info("[%s] terminal action: %s", call_id, action)

    @staticmethod
    def _read_file(path: str) -> bytes | None:
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return None
