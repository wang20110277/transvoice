"""WebSocket TTS 服务 — 语音合成，支持批量模式和流式模式。"""
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from ttsadapter.base import TTSEngine

logger = logging.getLogger(__name__)


class TTSWebSocketHandler:
    """WebSocket 语音合成 — 支持连接复用和流式/批量两种模式。

    协议:
        客户端 → 服务端:
            Text JSON: {"type":"synthesize","text":"...","call_id":"...","biz_type":"...",
                        "request_id":"...","streaming":false}
        服务端 → 客户端 (批量模式):
            Binary:    WAV 音频数据
            Text JSON: {"type":"result","duration_ms":...,"request_id":"..."}
        服务端 → 客户端 (流式模式):
            Binary:    PCM int16 音频块 (多个)
            Text JSON: {"type":"result","chunks_sent":N,"duration_ms":...,"request_id":"..."}
        错误:
            Text JSON: {"type":"error","message":"...","request_id":"..."}
    """

    def __init__(self, engine: TTSEngine):
        self._engine = engine

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()
        logger.info("[WS-TTS] client connected")

        try:
            while True:
                data = await websocket.receive()

                if "text" in data and data["text"]:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "synthesize":
                        await self._synthesize(websocket, msg)

        except WebSocketDisconnect:
            logger.info("[WS-TTS] client disconnected")
        except Exception as e:
            logger.error("[WS-TTS] error: %s", e)

    async def _synthesize(self, websocket: WebSocket, msg: dict) -> None:
        text = msg.get("text", "")
        call_id = msg.get("call_id", "")
        biz_type = msg.get("biz_type", "marketing")
        request_id = msg.get("request_id", "")
        streaming = msg.get("streaming", False)

        if not text:
            await websocket.send_json({
                "type": "error", "message": "text is required",
                "request_id": request_id,
            })
            return

        params = {"call_id": call_id, "biz_type": biz_type}

        if streaming and self._engine.supports_streaming:
            await self._synthesize_streaming(websocket, text, params, request_id, call_id)
        else:
            await self._synthesize_batch(websocket, text, params, request_id, call_id)

    async def _synthesize_batch(
        self, websocket: WebSocket, text: str, params: dict,
        request_id: str, call_id: str,
    ) -> None:
        try:
            result = await self._engine.synthesize(text, params)
        except Exception as e:
            logger.error("[WS-TTS] synthesize error call_id=%s: %s", call_id, e)
            await websocket.send_json({
                "type": "error", "message": str(e),
                "request_id": request_id,
            })
            return

        if result.audio:
            await websocket.send_bytes(result.audio)

        await websocket.send_json({
            "type": "result",
            "duration_ms": result.duration_ms,
            "request_id": request_id,
        })

    async def _synthesize_streaming(
        self, websocket: WebSocket, text: str, params: dict,
        request_id: str, call_id: str,
    ) -> None:
        chunks_sent = 0
        total_duration_ms = 0
        try:
            async for chunk in self._engine.synthesize_stream(text, params):
                if chunk.audio:
                    await websocket.send_bytes(chunk.audio)
                    chunks_sent += 1
                    total_duration_ms += chunk.duration_ms
                if chunk.is_final:
                    break
        except Exception as e:
            logger.error("[WS-TTS] streaming error call_id=%s: %s", call_id, e)
            await websocket.send_json({
                "type": "error", "message": str(e),
                "request_id": request_id,
            })
            return

        await websocket.send_json({
            "type": "result",
            "chunks_sent": chunks_sent,
            "duration_ms": total_duration_ms,
            "request_id": request_id,
        })
