"""TTS WebSocket 客户端 — 持久连接复用，支持批量与流式合成。"""
import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator

import websockets

logger = logging.getLogger(__name__)


class TTSWebSocketClient:
    """TTS WebSocket 客户端 — 持久连接，多次请求-响应复用。

    用 asyncio.Lock 保证请求-响应配对。
    内置重连: 连接断开时自动重建。
    """

    def __init__(self, base_url: str, timeout: float = 120.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._lock = asyncio.Lock()
        self._request_counter = 0

    async def start(self) -> None:
        """建立持久 WebSocket 连接。"""
        self._ws = await websockets.connect(self._base_url, max_size=None)
        logger.info("TTS WS client connected to %s", self._base_url)

    async def close(self) -> None:
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def synthesize_raw(
        self, text: str, call_id: str, biz_type: str,
    ) -> bytes | None:
        """合成文本为音频，返回原始 WAV bytes。"""
        if self._ws is None:
            return None
        async with self._lock:
            try:
                self._request_counter += 1
                request_id = str(self._request_counter)
                await self._ws.send(json.dumps({
                    "type": "synthesize",
                    "text": text,
                    "call_id": call_id,
                    "biz_type": biz_type,
                    "request_id": request_id,
                    "streaming": False,
                }))

                data = await asyncio.wait_for(self._ws.recv(), timeout=self._timeout)
                if isinstance(data, bytes):
                    # Read the metadata frame that follows
                    try:
                        meta_data = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
                        if isinstance(meta_data, str):
                            meta = json.loads(meta_data)
                            logger.debug(
                                "[WS-TTS] synthesize ok call_id=%s duration_ms=%s request_id=%s",
                                call_id, meta.get("duration_ms"), request_id,
                            )
                    except Exception:
                        pass
                    return data

                # Got text instead of binary — likely an error
                if isinstance(data, str):
                    msg = json.loads(data)
                    logger.error("[WS-TTS] synthesize error: %s", msg.get("message"))
                return None
            except Exception as e:
                logger.error("[WS-TTS] synthesize_raw failed call_id=%s: %s", call_id, e)
                await self._reconnect()
                return None

    async def synthesize_streaming_raw(
        self, text: str, call_id: str, biz_type: str,
    ) -> AsyncIterator[bytes]:
        """流式合成: 返回 PCM int16 音频块异步迭代器。"""
        if self._ws is None:
            return
        async with self._lock:
            try:
                self._request_counter += 1
                request_id = str(self._request_counter)
                await self._ws.send(json.dumps({
                    "type": "synthesize",
                    "text": text,
                    "call_id": call_id,
                    "biz_type": biz_type,
                    "request_id": request_id,
                    "streaming": True,
                }))

                while True:
                    data = await asyncio.wait_for(
                        self._ws.recv(), timeout=self._timeout,
                    )
                    if isinstance(data, bytes):
                        if data:
                            yield data
                    elif isinstance(data, str):
                        msg = json.loads(data)
                        msg_type = msg.get("type")
                        if msg_type == "result":
                            logger.debug(
                                "[WS-TTS] streaming done call_id=%s chunks=%s duration_ms=%s",
                                call_id, msg.get("chunks_sent"), msg.get("duration_ms"),
                            )
                            break
                        if msg_type == "error":
                            logger.error(
                                "[WS-TTS] streaming error call_id=%s: %s",
                                call_id, msg.get("message"),
                            )
                            break
            except Exception as e:
                logger.error(
                    "[WS-TTS] synthesize_streaming_raw failed call_id=%s: %s",
                    call_id, e,
                )
                await self._reconnect()

    async def synthesize(
        self, text: str, call_id: str, biz_type: str,
    ) -> dict | None:
        """合成文本为音频，返回 dict (base64 audio)，兼容 TTSClient 接口。"""
        wav = await self.synthesize_raw(text, call_id, biz_type)
        if wav is None:
            return None
        return {
            "audio": base64.b64encode(wav).decode("ascii"),
            "content_type": "audio/wav",
            "duration_ms": 0,
            "minio_key": None,
        }

    async def _reconnect(self) -> None:
        """尝试重连。"""
        try:
            if self._ws:
                await self._ws.close()
        except Exception:
            pass
        try:
            self._ws = await websockets.connect(self._base_url, max_size=None)
            logger.info("[WS-TTS] reconnected")
        except Exception as e:
            logger.error("[WS-TTS] reconnect failed: %s", e)
            self._ws = None
