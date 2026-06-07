"""WebSocket TTS 服务 — 语音合成，支持并发请求和流式/批量两种模式。

协议版本 2 (并发复用):
    客户端发送 {"protocol_version": 2} 后，服务端对每个 synthesize 请求
    启动独立 task 并发处理。通过 send_lock 序列化 WS 写操作，并在每个请求的
    binary 数据前发送 audio_header 文本帧，客户端可按 request_id 解复用。

协议版本 1 (旧客户端兼容):
    不带 protocol_version 的请求走内联顺序处理，不发送 audio_header。
"""
import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from ttsadapter.base import TTSEngine

logger = logging.getLogger(__name__)


class TTSWebSocketHandler:
    """WebSocket 语音合成 — 支持并发请求复用和流式/批量两种模式。

    协议:
        客户端 → 服务端:
            Text JSON: {"type":"synthesize","text":"...","call_id":"...","biz_type":"...",
                        "request_id":"...","streaming":false,"protocol_version":2}
        服务端 → 客户端 (v2 并发模式):
            Text JSON: {"type":"audio_header","request_id":"..."}
            Binary:    音频数据
            Text JSON: {"type":"result","duration_ms":...,"request_id":"..."}
        服务端 → 客户端 (v1 兼容模式):
            Binary:    WAV 音频数据
            Text JSON: {"type":"result","duration_ms":...,"request_id":"..."}
        错误:
            Text JSON: {"type":"error","message":"...","request_id":"..."}
    """

    def __init__(self, engine: TTSEngine):
        self._engine = engine

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()
        send_lock = asyncio.Lock()
        active_tasks: set[asyncio.Task] = set()
        protocol_version = 1
        logger.info("[WS-TTS] client connected")

        try:
            while True:
                data = await websocket.receive()

                # Starlette 收到 disconnect 后不再返回数据，直接退出
                if data.get("type") == "websocket.disconnect":
                    break

                if "text" in data and data["text"]:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "synthesize":
                        # 首次请求检测协议版本
                        if protocol_version < 2 and msg.get("protocol_version", 0) >= 2:
                            protocol_version = 2
                            logger.info("[WS-TTS] client negotiated protocol v2 (concurrent)")

                        if protocol_version >= 2:
                            # v2: 并发模式 — spawn task，TTS 计算并发，WS 写序列化
                            task = asyncio.create_task(
                                self._synthesize(websocket, msg, send_lock),
                                name=f"tts-synth-{msg.get('request_id', '?')}",
                            )
                            active_tasks.add(task)
                            task.add_done_callback(active_tasks.discard)
                        else:
                            # v1: 内联顺序处理（旧客户端兼容）
                            await self._synthesize_legacy(websocket, msg)

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error("[WS-TTS] error: %s", e)
        finally:
            for t in active_tasks:
                if not t.done():
                    t.cancel()
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)

    # ── v2 并发路径 ──

    async def _synthesize(self, websocket: WebSocket, msg: dict, send_lock: asyncio.Lock) -> None:
        text = msg.get("text", "")
        call_id = msg.get("call_id", "")
        biz_type = msg.get("biz_type", "marketing")
        request_id = msg.get("request_id", "")
        streaming = msg.get("streaming", False)

        if not text:
            try:
                async with send_lock:
                    await websocket.send_json({
                        "type": "error", "message": "text is required",
                        "request_id": request_id,
                    })
            except WebSocketDisconnect:
                return
            return

        params = {"call_id": call_id, "biz_type": biz_type}

        try:
            if streaming and self._engine.supports_streaming:
                await self._synthesize_streaming(websocket, text, params, request_id, call_id, send_lock)
            else:
                await self._synthesize_batch(websocket, text, params, request_id, call_id, send_lock)
        except WebSocketDisconnect:
            logger.debug("[WS-TTS] client disconnected during synthesis request_id=%s", request_id)

    async def _synthesize_batch(
        self, websocket: WebSocket, text: str, params: dict,
        request_id: str, call_id: str, send_lock: asyncio.Lock,
    ) -> None:
        try:
            result = await self._engine.synthesize(text, params)
        except Exception as e:
            logger.error("[WS-TTS] synthesize error call_id=%s: %s", call_id, e)
            try:
                async with send_lock:
                    await websocket.send_json({
                        "type": "error", "message": str(e),
                        "request_id": request_id,
                    })
            except WebSocketDisconnect:
                pass
            return

        try:
            async with send_lock:
                if result.audio:
                    await websocket.send_json({"type": "audio_header", "request_id": request_id})
                    await websocket.send_bytes(result.audio)
                await websocket.send_json({
                    "type": "result",
                    "duration_ms": result.duration_ms,
                    "request_id": request_id,
                })
        except WebSocketDisconnect:
            logger.debug("[WS-TTS] client gone, discarding synthesis result request_id=%s", request_id)

    async def _synthesize_streaming(
        self, websocket: WebSocket, text: str, params: dict,
        request_id: str, call_id: str, send_lock: asyncio.Lock,
    ) -> None:
        chunks_sent = 0
        total_duration_ms = 0
        try:
            async with send_lock:
                await websocket.send_json({"type": "audio_header", "request_id": request_id})
                async for chunk in self._engine.synthesize_stream(text, params):
                    if chunk.audio:
                        await websocket.send_bytes(chunk.audio)
                        chunks_sent += 1
                        total_duration_ms += chunk.duration_ms
                    if chunk.is_final:
                        break
                await websocket.send_json({
                    "type": "result",
                    "chunks_sent": chunks_sent,
                    "duration_ms": total_duration_ms,
                    "request_id": request_id,
                })
        except Exception as e:
            logger.error("[WS-TTS] streaming error call_id=%s: %s", call_id, e, exc_info=True)
            try:
                async with send_lock:
                    await websocket.send_json({
                        "type": "error", "message": str(e),
                        "request_id": request_id,
                    })
            except Exception:
                pass

    # ── v1 内联路径（旧客户端兼容） ──

    async def _synthesize_legacy(self, websocket: WebSocket, msg: dict) -> None:
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
            await self._synthesize_streaming_legacy(websocket, text, params, request_id, call_id)
        else:
            await self._synthesize_batch_legacy(websocket, text, params, request_id, call_id)

    async def _synthesize_batch_legacy(
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

    async def _synthesize_streaming_legacy(
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
            logger.error("[WS-TTS] streaming error call_id=%s: %s", call_id, e, exc_info=True)
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
