"""ASR WebSocket 客户端 — 流式音频上传，接口与 gRPC ASRStream 一致。"""
import asyncio
import json
import logging
from collections.abc import Callable

import websockets

logger = logging.getLogger(__name__)


class ASRWebSocketClient:
    """ASR WebSocket 客户端 — 每次流式识别创建独立连接。

    接口镜像 ASRGrpcClient: start/close/recognize/create_stream。
    """

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")
        self._started = False

    async def start(self) -> None:
        self._started = True
        logger.info("ASR WS client ready, target=%s", self._base_url)

    async def close(self) -> None:
        self._started = False

    async def recognize(self, audio_bytes: bytes, call_id: str) -> dict | None:
        """Batch 模式: 建立临时连接，发送完整音频，返回识别结果。"""
        if not self._started:
            return None
        try:
            async with websockets.connect(self._base_url) as ws:
                await ws.send(json.dumps({
                    "type": "config", "call_id": call_id, "language": "zh",
                    "streaming": False,
                }))
                await ws.send(audio_bytes)
                await ws.send(json.dumps({"type": "end"}))
                response = await ws.recv()
                result = json.loads(response)
                if result.get("type") == "result":
                    text = result.get("text", "")
                    logger.info(
                        "[WS-ASR] recognize call_id=%s text=%s confidence=%.2f",
                        call_id, text, result.get("confidence", 0.0),
                    )
                    return {
                        "text": text,
                        "confidence": result.get("confidence", 0.0),
                        "is_final": result.get("is_final", True),
                        "minio_key": result.get("minio_key") or None,
                    }
                return None
        except Exception as e:
            logger.error("ASR WS recognize failed call_id=%s: %s", call_id, e)
            return None

    def create_stream(self, call_id: str, streaming: bool = False, on_partial=None) -> "ASRWsStream | None":
        """创建流式会话 — 返回与 gRPC ASRStream 接口一致的对象。"""
        if not self._started:
            return None
        return ASRWsStream(self._base_url, call_id, streaming=streaming, on_partial=on_partial)


class ASRWsStream:
    """WebSocket 流式 ASR 会话 — 接口与 gRPC ASRStream 完全一致。

    send_audio() 为同步方法（内部用 asyncio.Queue + 后台 sender task），
    与 handler.py 中不 await 的调用方式兼容。

    streaming=True 时，后台 receiver_task 处理 partial 消息并触发 on_partial 回调。
    """

    def __init__(
        self,
        base_url: str,
        call_id: str,
        streaming: bool = False,
        on_partial: Callable[[str, float], None] | None = None,
    ):
        self._base_url = base_url
        self._call_id = call_id
        self._streaming = streaming
        self._on_partial = on_partial
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._queue: asyncio.Queue | None = None
        self._sender_task: asyncio.Task | None = None
        self._receiver_task: asyncio.Task | None = None
        self._partial_text = ""
        self._result: dict | None = None
        self._result_event = asyncio.Event()

    @property
    def partial_text(self) -> str:
        return self._partial_text

    async def start(self) -> None:
        """建立 WebSocket 连接，发送 config。"""
        self._ws = await websockets.connect(self._base_url)
        self._queue = asyncio.Queue()
        self._sender_task = asyncio.create_task(self._sender_loop())
        await self._queue.put(json.dumps({
            "type": "config", "call_id": self._call_id, "language": "zh",
            "streaming": self._streaming,
        }))
        if self._streaming:
            self._receiver_task = asyncio.create_task(self._receiver_loop())

    async def _sender_loop(self) -> None:
        """后台任务: 从 queue 取数据发往 WebSocket。"""
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    break
                if self._ws is None:
                    break
                await self._ws.send(item)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("[WS-ASR] sender error call_id=%s: %s", self._call_id, e)

    async def _receiver_loop(self) -> None:
        """后台任务: 流式模式下接收 partial 和 result 消息。"""
        try:
            while self._ws:
                response = await self._ws.recv()
                msg = json.loads(response)
                msg_type = msg.get("type")

                if msg_type == "partial":
                    self._partial_text = msg.get("text", "")
                    stability = msg.get("stability", 0.0)
                    if self._on_partial:
                        self._on_partial(self._partial_text, stability)

                elif msg_type == "result":
                    self._result = {
                        "text": msg.get("text", ""),
                        "confidence": msg.get("confidence", 0.0),
                        "is_final": True,
                        "minio_key": msg.get("minio_key") or None,
                    }
                    logger.info(
                        "[WS-ASR] result call_id=%s text=%s confidence=%.2f",
                        self._call_id, self._result["text"], self._result["confidence"],
                    )
                    self._result_event.set()
                    break

                elif msg_type == "error":
                    logger.error(
                        "[WS-ASR] server error call_id=%s: %s",
                        self._call_id, msg.get("message", ""),
                    )
                    break
        except asyncio.CancelledError:
            pass
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.error("[WS-ASR] receiver error call_id=%s: %s", self._call_id, e)

    def send_audio(self, chunk: bytes) -> None:
        """发送音频帧（同步，内部排队）。"""
        if self._queue is None:
            return
        self._queue.put_nowait(chunk)

    async def finish(self) -> dict | None:
        """发送结束信号，等待识别结果。"""
        if self._queue is not None:
            self._queue.put_nowait(json.dumps({"type": "end"}))

        if self._streaming and self._receiver_task:
            # 流式模式: receiver_task 负责接收 result
            try:
                await asyncio.wait_for(self._result_event.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                logger.warning("[WS-ASR] timeout waiting for result call_id=%s", self._call_id)
            if self._receiver_task and not self._receiver_task.done():
                self._receiver_task.cancel()
        else:
            # 批量模式: 直接等待服务端响应（sender_loop 是无限循环，不能 await）
            if self._ws:
                try:
                    response = await asyncio.wait_for(self._ws.recv(), timeout=15.0)
                    result = json.loads(response)
                    if result.get("type") == "result":
                        self._result = {
                            "text": result.get("text", ""),
                            "confidence": result.get("confidence", 0.0),
                            "is_final": True,
                            "minio_key": result.get("minio_key") or None,
                        }
                        logger.info(
                            "[WS-ASR] finish result call_id=%s text=%s",
                            self._call_id, self._result["text"],
                        )
                except Exception as e:
                    logger.error("[WS-ASR] finish failed call_id=%s: %s", self._call_id, e)
                except Exception as e:
                    logger.error("[WS-ASR] finish failed call_id=%s: %s", self._call_id, e)

        await self._close_ws()
        return self._result

    async def cancel(self) -> None:
        """取消流式会话。"""
        if self._queue is not None:
            self._queue.put_nowait(None)
        for task in (self._sender_task, self._receiver_task):
            if task and not task.done():
                task.cancel()
        await self._close_ws()

    async def _close_ws(self) -> None:
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
