"""TransvoiceSTT —— agent-asr WS（FSMN-VAD 服务端分段多 final）→ livekit STT 事件。

协议：agents 参考映射表 design.md §3.4。要点：
- 每段 result → SOS→FINAL→EOS 三连（服务端无 onset 信号，SOS 合成于 final 时刻）
- 文本 < min_final_len 的段整组丢弃（FINAL 与配对 EOS 一并丢，替代原 TurnController.min_text_len）
- "reset" 协议不映射（SDK 打断体系接管）
- 上游故障一律 APIConnectionError（SDK 默认 retryable）→ RecognizeStream._main_task
  按 conn_options 重试，防 AgentSession 连续错误计数熔断拆通话
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

import websockets
from livekit import rtc
from livekit.agents import APIConnectOptions, APIConnectionError, utils
from livekit.agents.stt import (
    RecognizeStream,
    STT,
    STTCapabilities,
    SpeechData,
    SpeechEvent,
    SpeechEventType,
)

logger = logging.getLogger(__name__)

# AgentSession 建流时总会显式传 conn_options（voice/agent.py stt_node:
# session.conn_options.stt_conn_options，默认 max_retry=3），由基类 _main_task 重试；
# 未显式传入（直接调用/测试）时单次尝试、错误即刻抛出（同 sarvam 插件做法）。
_SINGLE_ATTEMPT_CONN_OPTIONS = APIConnectOptions(max_retry=0)


class TransvoiceSTT(STT):
    def __init__(self, *, ws_url: str, min_final_len: int = 2) -> None:
        super().__init__(capabilities=STTCapabilities(streaming=True, interim_results=False))
        self._ws_url = ws_url
        self._min_final_len = min_final_len

    def stream(
        self, *, language: str = "zh", conn_options: APIConnectOptions | None = None, **kwargs
    ) -> TransvoiceRecognizeStream:
        return TransvoiceRecognizeStream(
            stt=self,
            conn_options=conn_options or _SINGLE_ATTEMPT_CONN_OPTIONS,
            language=language or "zh",
            ws_url=self._ws_url,
            min_final_len=self._min_final_len,
        )

    async def _recognize_impl(self, buffer, *, language, conn_options):
        raise NotImplementedError("batch 模式未启用，仅流式（design.md §3.4）")

    @property
    def model(self) -> str:
        return "sensevoice-fsmn"

    @property
    def provider(self) -> str:
        return "transvoice"


class TransvoiceRecognizeStream(RecognizeStream):
    def __init__(self, *, stt, conn_options, language, ws_url, min_final_len) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=16000)
        self._language = language
        self._ws_url = ws_url
        self._min_final_len = min_final_len

    async def _run(self) -> None:
        call_id = utils.shortuuid()[:8]
        try:
            ws = await websockets.connect(self._ws_url, ping_interval=120, ping_timeout=180)
        except Exception as e:
            raise APIConnectionError(f"ASR connect failed: {e}") from e

        recv_task = asyncio.create_task(self._recv_loop(ws))
        send_task = asyncio.create_task(self._send_loop(ws, call_id))
        try:
            # 任一方向异常都要终结整条流（recv 异常若滞留在 task 里，
            # _run 会永久阻塞在另一方向，错误到不了 _main_task）
            done, _ = await asyncio.wait(
                {recv_task, send_task}, return_when=asyncio.FIRST_EXCEPTION
            )
            for task in done:
                if task.exception() is not None:
                    raise task.exception()
        finally:
            for task in (recv_task, send_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(recv_task, send_task, return_exceptions=True)
            with contextlib.suppress(Exception):
                await ws.close()

    async def _send_loop(self, ws, call_id: str) -> None:
        try:
            await ws.send(json.dumps({
                "type": "config", "call_id": call_id,
                "language": self._language, "streaming": True,
            }))
            async for item in self._input_ch:
                if isinstance(item, rtc.AudioFrame):
                    await ws.send(bytes(item.data))
                else:  # 非 AudioFrame 即 flush 哨兵（_FlushSentinel 是基类嵌套类，非公开名）
                    await ws.send(json.dumps({"type": "end"}))
        except Exception as e:
            raise APIConnectionError(f"ASR upstream send error: {e}") from e

    async def _recv_loop(self, ws) -> None:
        try:
            while True:
                try:
                    raw = await ws.recv()
                except websockets.ConnectionClosedOK:
                    # agent-asr 处理完 {"type":"end"} 后主动关连接，属正常终结
                    return
                msg = json.loads(raw)
                mtype = msg.get("type")
                if mtype == "result":
                    text = (msg.get("text") or "").strip()
                    if len(text) < self._min_final_len:
                        logger.info("ASR final too short ('%s'), drop", text)
                        continue  # FINAL 与配对 EOS 一并丢弃
                    rid = utils.shortuuid()
                    self._event_ch.send_nowait(SpeechEvent(
                        type=SpeechEventType.START_OF_SPEECH, request_id=rid))
                    self._event_ch.send_nowait(SpeechEvent(
                        type=SpeechEventType.FINAL_TRANSCRIPT, request_id=rid,
                        alternatives=[SpeechData(
                            language=self._language, text=text,
                            confidence=msg.get("confidence", 0.0))]))
                    # 铁律：FINAL 之后才能 EOS（SDK EOU 由 EOS 驱动）
                    self._event_ch.send_nowait(SpeechEvent(
                        type=SpeechEventType.END_OF_SPEECH, request_id=rid))
                elif mtype == "error":
                    raise APIConnectionError(
                        f"ASR server error: {msg.get('message', '')}")
        except APIConnectionError:
            raise
        except Exception as e:
            raise APIConnectionError(f"ASR recv error: {e}") from e
