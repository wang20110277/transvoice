"""TransvoiceSTT —— agent-asr WS（无状态整段识别）→ livekit 非流式批量 STT。

插件仅实现 _recognize_impl（每语音段一条连接：config → 分帧音频 → end → 单 result）；
流式能力由 SDK 默认 stt_node 自动包装的 stt.StreamAdapter 提供（silero VAD 切段 →
段级 recognize → SOS/FINAL/EOS 事件），插件不手工合成事件序列。要点（design.md D1/D4/D5）：
- 文本 strip 后 < min_final_len → 返回空 alternatives（StreamAdapter 跳过 FINAL 发射，
  过滤无意义短促噪声段）
- 上游故障一律 APIConnectionError（SDK 默认 retryable）→ STT.recognize 基类
  按 conn_options 重试，防 AgentSession 连续错误计数熔断拆通话
"""
from __future__ import annotations

import contextlib
import json
import logging

import websockets
from livekit import rtc
from livekit.agents import APIConnectionError, utils
from livekit.agents.stt import (
    STT,
    STTCapabilities,
    SpeechData,
    SpeechEvent,
    SpeechEventType,
)

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_MAX_WS_CHUNK_BYTES = 64 * 1024  # 单 binary 帧上限，规避 websockets 接收缓冲限制


class TransvoiceSTT(STT):
    def __init__(self, *, ws_url: str, min_final_len: int = 2) -> None:
        super().__init__(
            capabilities=STTCapabilities(streaming=False, interim_results=False))
        self._ws_url = ws_url
        self._min_final_len = min_final_len

    @property
    def model(self) -> str:
        return "sensevoice"

    @property
    def provider(self) -> str:
        return "transvoice"

    async def _recognize_impl(self, buffer, *, language, conn_options):
        call_id = utils.shortuuid()[:8]
        pcm = bytes(rtc.combine_audio_frames(buffer).data)
        try:
            ws = await websockets.connect(
                self._ws_url, ping_interval=120, ping_timeout=180)
        except Exception as e:
            raise APIConnectionError(f"ASR connect failed: {e}") from e
        try:
            await ws.send(json.dumps({
                "type": "config", "call_id": call_id,
                "language": language or "zh", "sample_rate": _SAMPLE_RATE,
            }))
            for i in range(0, len(pcm), _MAX_WS_CHUNK_BYTES):
                await ws.send(pcm[i:i + _MAX_WS_CHUNK_BYTES])
            await ws.send(json.dumps({"type": "end"}))
            try:
                raw = await ws.recv()
            except websockets.ConnectionClosedOK:
                # 服务端回 result 后才关连接；未回即关属协议异常
                raise APIConnectionError("ASR closed without result")
            msg = json.loads(raw)
            if msg.get("type") == "error":
                raise APIConnectionError(
                    f"ASR server error: {msg.get('message', '')}")
            text = (msg.get("text") or "").strip()
        except APIConnectionError:
            raise
        except Exception as e:
            raise APIConnectionError(f"ASR recognize failed: {e}") from e
        finally:
            with contextlib.suppress(Exception):
                await ws.close()

        if len(text) < self._min_final_len:
            logger.info("ASR final too short ('%s'), drop", text)
            return SpeechEvent(
                type=SpeechEventType.FINAL_TRANSCRIPT, alternatives=[])
        return SpeechEvent(
            type=SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[SpeechData(
                language=language or "zh", text=text,
                confidence=msg.get("confidence", 0.0))],
        )
