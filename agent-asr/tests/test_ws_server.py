"""ASRWebSocketHandler 单测 — 无状态整段识别协议：config → binary 累积 → end → 单 result → 终结。

mock engine（返回固定文本），不依赖真实模型。
"""
import asyncio
import json

import pytest

from asradapter.base import ASRResult
from asradapter.ws_server import ASRWebSocketHandler


class _FakeEngine:
    def __init__(self, text="你好"):
        self._text = text
        self.audio: list[bytes] = []

    async def recognize(self, audio, params):
        self.audio.append(audio)
        return ASRResult(text=self._text, confidence=0.95, is_final=True)


class _FakeWS:
    """最小 WebSocket 替身 —— 收消息队列 + 发送记录。"""

    def __init__(self, incoming):
        self._incoming = list(incoming)
        self.sent = []

    async def accept(self):
        pass

    async def receive(self):
        if not self._incoming:
            await asyncio.sleep(0.01)
            raise asyncio.TimeoutError
        item = self._incoming.pop(0)
        if isinstance(item, str):
            return {"text": item}
        return {"bytes": item}

    async def send_json(self, obj):
        self.sent.append(obj)


def _config_msg(**over):
    msg = {"type": "config", "call_id": "c1", "language": "zh", "sample_rate": 16000}
    msg.update(over)
    return json.dumps(msg)


@pytest.mark.asyncio
async def test_frames_accumulate_then_end_single_result():
    engine = _FakeEngine("整段文本")
    handler = ASRWebSocketHandler(engine)
    ws = _FakeWS([_config_msg(), b"frame1", b"frame2", json.dumps({"type": "end"})])
    await asyncio.wait_for(handler.handle(ws), timeout=2.0)
    assert engine.audio == [b"frame1frame2"]  # 拼接后整段识别，恰好一次
    results = [m for m in ws.sent if m.get("type") == "result"]
    assert results == [{"type": "result", "text": "整段文本",
                        "confidence": 0.95, "is_final": True}]


@pytest.mark.asyncio
async def test_sample_rate_8000_triggers_resample():
    engine = _FakeEngine()
    handler = ASRWebSocketHandler(engine)
    ws = _FakeWS([_config_msg(sample_rate=8000),
                  b"\x01\x00" * 160, json.dumps({"type": "end"})])
    await asyncio.wait_for(handler.handle(ws), timeout=2.0)
    # 160 samples @ 8k = 20ms；常量幅度线性插值不变 → 320 samples @ 16k = 640B
    assert engine.audio == [b"\x01\x00" * 320]


@pytest.mark.asyncio
async def test_end_without_audio_returns_empty_result():
    engine = _FakeEngine()
    handler = ASRWebSocketHandler(engine)
    ws = _FakeWS([_config_msg(), json.dumps({"type": "end"})])
    await asyncio.wait_for(handler.handle(ws), timeout=2.0)
    assert engine.audio == []  # 空音频不调 engine
    results = [m for m in ws.sent if m.get("type") == "result"]
    assert results == [{"type": "result", "text": "",
                        "confidence": 0.0, "is_final": True}]
