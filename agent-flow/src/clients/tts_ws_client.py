"""TTS WebSocket 客户端 — 持久连接复用，支持并发请求。

去掉全局 asyncio.Lock，改为后台 reader task 按 request_id 解复用响应。
客户端发送 "protocol_version": 2，服务端对每个请求返回 audio_header + binary + result，
reader 据此将 binary 帧路由到对应的 pending request。

batch 模式: _PendingRequest 用 asyncio.Future 等待完整音频。
streaming 模式: _PendingRequest 用 asyncio.Queue 逐块投递。
"""
import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import websockets

logger = logging.getLogger(__name__)


@dataclass
class _PendingRequest:
    """跟踪一个等待响应的 TTS 请求。"""
    mode: str  # "batch" | "streaming"
    # batch: reader 收集 binary 块，完成后 resolve Future
    audio_chunks: list[bytes] = field(default_factory=list)
    result_future: asyncio.Future = field(default=None)
    # streaming: reader 将 binary 块逐个 put 到 Queue，None = 结束
    chunk_queue: asyncio.Queue = field(default=None)

    def __post_init__(self) -> None:
        if self.mode == "batch":
            loop = asyncio.get_running_loop()
            self.result_future = loop.create_future()
        else:
            self.chunk_queue = asyncio.Queue()


class TTSWebSocketClient:
    """TTS WebSocket 客户端 — 持久连接，支持并发请求-响应复用。

    后台 _reader_loop 持续接收消息，按 request_id 路由到对应的 _PendingRequest。
    发送端用 _send_lock 序列化写操作（不影响接收端并发）。
    内置重连: 连接断开时自动重建，失败所有 pending 请求。
    """

    def __init__(self, base_url: str, timeout: float = 60.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._send_lock = asyncio.Lock()
        self._reconnect_lock = asyncio.Lock()
        self._request_counter = 0
        self._pending: dict[str, _PendingRequest] = {}
        self._reader_task: asyncio.Task | None = None
        self._current_request_id: str | None = None

    async def start(self) -> None:
        """建立持久 WebSocket 连接并启动后台 reader。"""
        try:
            self._ws = await websockets.connect(
                self._base_url, max_size=None, open_timeout=30,
                # GPU 推理期间事件循环可能阻塞，需要较长 ping 间隔避免超时断连
                ping_interval=120, ping_timeout=180,
            )
            self._reader_task = asyncio.create_task(self._reader_loop(), name="tts-ws-reader")
            logger.info("TTS WS client connected to %s", self._base_url)
        except Exception as e:
            logger.warning("TTS WS client connect deferred (will retry on first use): %s", e)
            self._ws = None

    async def close(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def synthesize_raw(
        self, text: str, call_id: str, biz_type: str,
    ) -> bytes | None:
        """合成文本为音频，返回原始 WAV bytes。支持并发调用。"""
        # 连接健康检查：_ws 存在但 reader 死了 → 连接已断，需重连
        if self._ws is None or self._reader_task is None or self._reader_task.done():
            await self._reconnect()
            if self._ws is None:
                return None

        self._request_counter += 1
        request_id = str(self._request_counter)
        pending = _PendingRequest(mode="batch")
        self._pending[request_id] = pending

        try:
            async with self._send_lock:
                await self._ws.send(json.dumps({
                    "type": "synthesize",
                    "text": text,
                    "call_id": call_id,
                    "biz_type": biz_type,
                    "request_id": request_id,
                    "streaming": False,
                    "protocol_version": 2,
                }))

            return await asyncio.wait_for(pending.result_future, timeout=self._timeout)

        except Exception as e:
            self._pending.pop(request_id, None)
            logger.error("[WS-TTS] synthesize_raw failed call_id=%s: %s", call_id, e)
            await self._reconnect()
            return None

    async def synthesize_streaming_raw(
        self, text: str, call_id: str, biz_type: str,
    ) -> AsyncIterator[bytes]:
        """流式合成: 返回 PCM int16 音频块异步迭代器。支持并发调用。"""
        if self._ws is None or self._reader_task is None or self._reader_task.done():
            await self._reconnect()
            if self._ws is None:
                return

        self._request_counter += 1
        request_id = str(self._request_counter)
        pending = _PendingRequest(mode="streaming")
        self._pending[request_id] = pending

        try:
            async with self._send_lock:
                await self._ws.send(json.dumps({
                    "type": "synthesize",
                    "text": text,
                    "call_id": call_id,
                    "biz_type": biz_type,
                    "request_id": request_id,
                    "streaming": True,
                    "protocol_version": 2,
                }))

            while True:
                chunk = await asyncio.wait_for(pending.chunk_queue.get(), timeout=self._timeout)
                if chunk is None:
                    break  # sentinel: result 或 error
                if chunk:
                    yield chunk

        except Exception as e:
            logger.error(
                "[WS-TTS] synthesize_streaming_raw failed call_id=%s: %s",
                call_id, e,
            )
            await self._reconnect()
        finally:
            self._pending.pop(request_id, None)

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

    # ── 后台 reader ──

    async def _reader_loop(self) -> None:
        """持续接收 WS 消息，按 request_id 路由到对应的 pending request。"""
        try:
            while self._ws:
                try:
                    data = await self._ws.recv()
                except websockets.ConnectionClosed:
                    logger.warning("[WS-TTS] connection closed in reader")
                    self._fail_all_pending("connection closed")
                    return

                if isinstance(data, bytes):
                    self._route_binary(data)
                elif isinstance(data, str):
                    self._route_text(data)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("[WS-TTS] reader error: %s", e)
            self._fail_all_pending(str(e))

    def _route_binary(self, data: bytes) -> None:
        """将 binary 帧路由到当前活跃的 pending request。"""
        rid = self._current_request_id
        if not rid or rid not in self._pending:
            logger.warning("[WS-TTS] orphan binary frame, %d bytes", len(data))
            return
        pending = self._pending[rid]
        if pending.mode == "batch":
            pending.audio_chunks.append(data)
        else:
            pending.chunk_queue.put_nowait(data)

    def _route_text(self, data: str) -> None:
        """解析 JSON 文本帧并路由到对应的 pending request。"""
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type")
        request_id = msg.get("request_id", "")

        if msg_type == "audio_header":
            self._current_request_id = request_id

        elif msg_type == "result":
            pending = self._pending.pop(request_id, None)
            self._current_request_id = None
            if pending:
                if pending.mode == "batch":
                    wav = b"".join(pending.audio_chunks)
                    if not pending.result_future.done():
                        pending.result_future.set_result(wav)
                else:
                    pending.chunk_queue.put_nowait(None)  # sentinel
                logger.debug(
                    "[WS-TTS] result request_id=%s mode=%s",
                    request_id, pending.mode,
                )

        elif msg_type == "error":
            pending = self._pending.pop(request_id, None)
            self._current_request_id = None
            if pending:
                if pending.mode == "batch" and not pending.result_future.done():
                    pending.result_future.set_result(None)
                else:
                    pending.chunk_queue.put_nowait(None)
            logger.error(
                "[WS-TTS] error request_id=%s: %s",
                request_id, msg.get("message"),
            )

    def _fail_all_pending(self, reason: str) -> None:
        """连接断开时，将所有 pending 请求标记为失败。"""
        for req_id, pending in list(self._pending.items()):
            if pending.mode == "batch" and not pending.result_future.done():
                pending.result_future.set_result(None)
            elif pending.mode == "streaming" and pending.chunk_queue:
                pending.chunk_queue.put_nowait(None)
        self._pending.clear()
        self._current_request_id = None
        logger.warning("[WS-TTS] failed all pending requests: %s", reason)

    async def _reconnect(self) -> None:
        """重连：取消旧 reader → 失败所有 pending → 重建连接 → 启动新 reader。

        用 _reconnect_lock 保护，防止多个 synthesize_raw 并发失败时重复重连。
        """
        async with self._reconnect_lock:
            # 已被其他协程重连完成，直接返回
            if self._ws and self._reader_task and not self._reader_task.done():
                return

            # 取消旧 reader
            if self._reader_task and not self._reader_task.done():
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._reader_task = None

            self._fail_all_pending("reconnecting")

            try:
                if self._ws:
                    await self._ws.close()
            except Exception:
                pass

            try:
                self._ws = await websockets.connect(
                    self._base_url, max_size=None, open_timeout=30,
                    ping_interval=120, ping_timeout=180,
                )
                self._reader_task = asyncio.create_task(self._reader_loop(), name="tts-ws-reader")
                logger.info("[WS-TTS] reconnected")
            except Exception as e:
                logger.error("[WS-TTS] reconnect failed: %s", e)
                self._ws = None
