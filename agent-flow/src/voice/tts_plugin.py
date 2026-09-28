"""TransvoiceTTS —— agent-tts WS（整句合成，22050Hz PCM）→ livekit TTS。

服务端协议约束：每次 synthesize 需完整文本（无增量合成）→ 插件在 flush 边界聚合整句。
打断防线：segment 取消即从存活集合移除，reader 丢弃其迟到音频（防串台）。

错误分类：传输故障（连接/发送）→ APIConnectionError（SDK 默认 retryable，_main_task
按 conn_options 重试）；服务端 error 应答 → APIError（引擎错误多为瞬态，同样可重试，
流级重试受 pushed_duration==0 保护不会重复已播音频）。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

import websockets
from livekit.agents import APIConnectOptions, APIConnectionError, APIError, utils
from livekit.agents.tts import (
    AudioEmitter,
    ChunkedStream,
    SynthesizeStream,
    TTS,
    TTSCapabilities,
)

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 22050  # 服务端 CosyVoice 实际输出率，字节原样推送，SDK 负责下游重采样

# AgentSession 建流时总会显式传 conn_options（voice/agent.py tts_node），由基类
# _main_task 重试；未显式传入（直接调用/测试）时单次尝试（同 Task 4 STT 插件做法）。
_SINGLE_ATTEMPT_CONN_OPTIONS = APIConnectOptions(max_retry=0)


class _SharedTtsConnection:
    """每 call 一条共享 WS + request_id 解复用（句级并发合成共用连接，按 id 路由回包）。

    GPU 推理会长时间阻塞 recv，连接参数须容忍慢响应（ping_interval/timeout 放宽、
    max_size 不限），与服务端 ws_server.py 的配置对齐。
    """

    def __init__(self, *, ws_url: str) -> None:
        self._ws_url = ws_url
        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._queues: dict[str, asyncio.Queue] = {}
        self._current_rid: str | None = None

    async def connect(self) -> None:
        if self._ws is not None:
            return
        try:
            self._ws = await websockets.connect(
                self._ws_url, max_size=None, ping_interval=120, ping_timeout=180)
            self._reader = asyncio.create_task(self._reader_loop())
        except Exception as e:
            self._ws = None
            raise APIConnectionError(f"TTS connect failed: {e}") from e

    def register(self, request_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._queues[request_id] = q
        return q

    def unregister(self, request_id: str) -> None:
        """segment 取消（打断）→ 迟到音频丢弃防线。"""
        self._queues.pop(request_id, None)
        if self._current_rid == request_id:
            self._current_rid = None

    async def send_synthesize(self, *, text: str, call_id: str, biz_type: str,
                              request_id: str) -> None:
        await self.connect()
        try:
            await self._ws.send(json.dumps({
                "type": "synthesize", "text": text, "call_id": call_id,
                "biz_type": biz_type, "request_id": request_id,
                "streaming": True, "protocol_version": 2,
            }))
        except Exception as e:
            # 发送失败走可重试分类（与 STT 插件一致），否则裸异常绕过流级重试
            raise APIConnectionError(f"TTS send failed: {e}") from e

    async def _reader_loop(self) -> None:
        try:
            while self._ws:
                data = await self._ws.recv()
                if isinstance(data, bytes):
                    if self._current_rid and self._current_rid in self._queues:
                        self._queues[self._current_rid].put_nowait(data)
                    # 不在存活集合 → 迟到音频（segment 已取消），丢弃
                else:
                    self._route_text(data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            # 广播连接故障：各 segment 自行抛出，由流级 _main_task 分类重试
            err = APIConnectionError(f"TTS connection lost: {e}")
            for q in self._queues.values():
                q.put_nowait(err)
            self._queues.clear()
            self._current_rid = None
            # 复位死连接，让后续 connect()（如流级重试）重建而非复用已断开的 socket
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None

    def _route_text(self, raw: str) -> None:
        msg = json.loads(raw)
        mtype = msg.get("type")
        rid = msg.get("request_id", "")
        if mtype == "audio_header":
            self._current_rid = rid
        elif mtype == "result":
            self._current_rid = None
            q = self._queues.get(rid)
            if q is not None:
                q.put_nowait(None)  # sentinel：段结束
        elif mtype == "error":
            self._current_rid = None
            q = self._queues.pop(rid, None)
            if q is not None:
                # 服务端引擎错误（GPU 推理）多为瞬态，保持 SDK 默认 retryable；
                # 流级重试受 pushed_duration==0 保护，不会重复已播音频
                q.put_nowait(APIError(msg.get("message", "tts error")))

    async def close(self) -> None:
        if self._reader and not self._reader.done():
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None


class TransvoiceTTS(TTS):
    def __init__(self, *, ws_url: str, biz_type: str, call_id: str) -> None:
        super().__init__(
            capabilities=TTSCapabilities(streaming=True),
            sample_rate=_SAMPLE_RATE, num_channels=1,
        )
        self._ws_url = ws_url
        self._biz_type = biz_type
        self._call_id = call_id
        self._conn: _SharedTtsConnection | None = None

    def _ensure_conn(self) -> _SharedTtsConnection:
        if self._conn is None:
            self._conn = _SharedTtsConnection(ws_url=self._ws_url)
        return self._conn

    def stream(
        self, *, conn_options: APIConnectOptions | None = None, **kwargs
    ) -> TransvoiceSynthesizeStream:
        return TransvoiceSynthesizeStream(
            tts=self, conn_options=conn_options or _SINGLE_ATTEMPT_CONN_OPTIONS,
            conn=self._ensure_conn(), call_id=self._call_id, biz_type=self._biz_type)

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions | None = None, **kwargs
    ) -> TransvoiceChunkedStream:
        return TransvoiceChunkedStream(
            tts=self, conn_options=conn_options or _SINGLE_ATTEMPT_CONN_OPTIONS,
            conn=self._ensure_conn(), text=text,
            call_id=self._call_id, biz_type=self._biz_type)

    async def aclose(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def model(self) -> str:
        return "cosyvoice3"

    @property
    def provider(self) -> str:
        return "transvoice"


class TransvoiceSynthesizeStream(SynthesizeStream):
    def __init__(self, *, tts: TTS, conn_options: APIConnectOptions,
                 conn: _SharedTtsConnection, call_id: str, biz_type: str) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._conn = conn
        self._call_id = call_id
        self._biz_type = biz_type

    def push_text(self, token: str) -> None:
        """覆盖基类：去掉 1.8.3 的"单流多 segment 已废弃"门禁。

        基类在首个 flush 后丢弃后续 push_text（多 segment 改走每段新建流 +
        连接池），但 agent-tts 整句协议恰恰依赖单流内 flush 边界切句（一次
        speech turn = 一条流 = N 个 segment），故恢复送达。其余记账
        （_num_segments/_mtc_text/_input_buffer）必须与基类一致，否则
        _main_task 的 segment 数量校验会误报 mismatch。
        """
        if not token or self._input_ch.closed:
            return

        self._pushed_text += token

        if self._metrics_task is None:
            self._metrics_task = asyncio.create_task(
                self._metrics_monitor_task(self._monitor_aiter), name="TTS._metrics_task"
            )

        if not self._mtc_text:
            self._num_segments += 1

        self._mtc_text += token
        self._input_ch.send_nowait(token)
        self._input_buffer.append(token)

    async def _run(self, output_emitter: AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid(), sample_rate=_SAMPLE_RATE,
            num_channels=1, mime_type="audio/pcm", stream=True,
        )
        buf: list[str] = []
        seg = 0
        async for item in self._input_ch:
            if isinstance(item, self._FlushSentinel):
                text = "".join(buf).strip()
                buf = []
                if text:  # 空哨兵（连续 flush/end_input 内部 flush）直接跳过
                    seg += 1
                    await self._synthesize_segment(output_emitter, f"seg{seg}", text)
            else:
                buf.append(item)
        text = "".join(buf).strip()
        if text:  # 末句未 flush 兜底（调用方只 close 不 flush 的场景）
            seg += 1
            await self._synthesize_segment(output_emitter, f"seg{seg}", text)

    async def _synthesize_segment(self, output_emitter: AudioEmitter,
                                  segment_id: str, text: str) -> None:
        await self._conn.connect()
        q = self._conn.register(segment_id)
        try:
            await self._conn.send_synthesize(
                text=text, call_id=self._call_id, biz_type=self._biz_type,
                request_id=segment_id)
            output_emitter.start_segment(segment_id=segment_id)
            while True:
                item = await q.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                output_emitter.push(item)
            output_emitter.end_segment()
        finally:
            self._conn.unregister(segment_id)  # 迟到音频防线


class TransvoiceChunkedStream(ChunkedStream):
    def __init__(self, *, tts: TTS, conn_options: APIConnectOptions,
                 conn: _SharedTtsConnection, text: str,
                 call_id: str, biz_type: str) -> None:
        super().__init__(tts=tts, input_text=text, conn_options=conn_options)
        self._conn = conn
        self._call_id = call_id
        self._biz_type = biz_type

    async def _run(self, output_emitter: AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid(), sample_rate=_SAMPLE_RATE,
            num_channels=1, mime_type="audio/pcm", stream=False,
        )
        rid = f"chk-{utils.shortuuid()[:8]}"
        await self._conn.connect()
        q = self._conn.register(rid)
        try:
            await self._conn.send_synthesize(
                text=self._input_text, call_id=self._call_id,
                biz_type=self._biz_type, request_id=rid)
            while True:
                item = await q.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                output_emitter.push(item)
            output_emitter.flush()
        finally:
            self._conn.unregister(rid)
