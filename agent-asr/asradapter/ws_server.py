"""WebSocket ASR 服务 — 流式音频识别，支持批量模式和流式模式。"""
import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from asradapter.base import ASREngine

logger = logging.getLogger(__name__)


class ASRWebSocketHandler:
    """WebSocket 流式语音识别。

    协议:
        客户端 → 服务端:
            Text JSON: {"type":"config","call_id":"...","language":"zh","streaming":false}
            Binary:    PCM 16-bit 8/16kHz mono 音频帧
            Text JSON: {"type":"end"}  (批量模式结束标记)
        服务端 → 客户端:
            Text JSON: {"type":"partial","text":"...","stability":0.6}  (仅流式模式)
            Text JSON: {"type":"result","text":"...","confidence":0.95,"is_final":true}
            Text JSON: {"type":"error","message":"..."}
    """

    def __init__(self, engine: ASREngine):
        self._engine = engine

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()
        call_id = ""
        language = "zh"
        streaming = False
        audio_chunks: list[bytes] = []
        config_received = False
        stream_ctx = None

        try:
            while True:
                data = await websocket.receive()

                if "text" in data and data["text"]:
                    msg = json.loads(data["text"])
                    msg_type = msg.get("type")

                    if msg_type == "config":
                        call_id = msg.get("call_id", "")
                        language = msg.get("language", "zh")
                        streaming = msg.get("streaming", False)
                        config_received = True
                        logger.info(
                            "[WS-ASR] config call_id=%s streaming=%s",
                            call_id, streaming,
                        )

                        if streaming and self._engine.supports_streaming:
                            stream_ctx = await self._engine.start_stream(
                                {"call_id": call_id, "language": language}
                            )
                            await stream_ctx.start()

                    elif msg_type == "end":
                        if streaming and stream_ctx:
                            result = await stream_ctx.finish()
                            await websocket.send_json({
                                "type": "result",
                                "text": result.text,
                                "confidence": result.confidence,
                                "is_final": True,
                            })
                        else:
                            audio_bytes = b"".join(audio_chunks)
                            await self._recognize_and_respond(
                                websocket, audio_bytes, call_id, language,
                            )
                        return

                elif "bytes" in data and data["bytes"]:
                    if not config_received:
                        config_received = True
                    chunk = data["bytes"]

                    if streaming and stream_ctx:
                        stream_ctx.send_audio(chunk)
                        partial = await stream_ctx.get_partial()
                        if partial:
                            await websocket.send_json({
                                "type": "partial",
                                "text": partial.text,
                                "stability": partial.stability,
                            })
                    else:
                        audio_chunks.append(chunk)

        except WebSocketDisconnect:
            logger.info("[WS-ASR] client disconnected call_id=%s", call_id)
        except Exception as e:
            logger.error("[WS-ASR] error call_id=%s: %s", call_id, e)
            try:
                await websocket.send_json({"type": "error", "message": str(e)})
            except Exception:
                pass
        finally:
            if stream_ctx:
                try:
                    await stream_ctx.cancel()
                except Exception:
                    pass

    async def _recognize_and_respond(
        self,
        websocket: WebSocket,
        audio_bytes: bytes,
        call_id: str,
        language: str,
    ) -> None:
        if not audio_bytes:
            await websocket.send_json({
                "type": "result", "text": "",
                "confidence": 0.0, "is_final": True,
            })
            return

        params = {"call_id": call_id, "language": language}
        try:
            result = await self._engine.recognize(audio_bytes, params)
        except Exception as e:
            logger.error("[WS-ASR] recognize error call_id=%s: %s", call_id, e)
            await websocket.send_json({"type": "error", "message": str(e)})
            return

        await websocket.send_json({
            "type": "result",
            "text": result.text,
            "confidence": result.confidence,
            "is_final": True,
        })
