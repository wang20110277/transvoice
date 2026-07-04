"""ASRWebSocketHandler 单测 — 验证多 final 主动推、reset、sample_rate resample、降级。

mock segmenter(返回固定段)+ mock engine(返回固定文本),不依赖真实模型。
"""
import asyncio
import json

import pytest

from asradapter.base import ASRResult
from asradapter.ws_server import ASRWebSocketHandler


class _FakeSegmenter:
    def __init__(self, segments_by_feed):
        self._seq = list(segments_by_feed)
        self._i = 0
        self.resets = 0
        self.flushed = False

    def feed(self, pcm):
        if self._i < len(self._seq):
            segs = self._seq[self._i]
            self._i += 1
            return segs
        return []

    def force_flush(self):
        self.flushed = True
        return []

    def reset(self):
        self.resets += 1


class _FakeEngine:
    def __init__(self, text="你好"):
        self._text = text

    async def recognize(self, audio, params):
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
async def test_segment_drives_proactive_final(monkeypatch):
    seg = _FakeSegmenter([[b"seg-a"], [b"seg-b"]])
    handler = ASRWebSocketHandler(_FakeEngine("hi"), seg)
    ws = _FakeWS([
        _config_msg(),
        b"frame1",  # feed 返回 [seg-a] → recognize → 推一个 final
        b"frame2",  # feed 返回 [seg-b] → 再推一个 final
        json.dumps({"type": "end"}),
    ])
    await asyncio.wait_for(handler.handle(ws), timeout=2.0)
    results = [m for m in ws.sent if m.get("type") == "result"]
    assert len(results) >= 2  # 单连接多 final
    assert all(m["is_final"] for m in results)


@pytest.mark.asyncio
async def test_reset_calls_segmenter_reset():
    seg = _FakeSegmenter([])
    handler = ASRWebSocketHandler(_FakeEngine(), seg)
    ws = _FakeWS([_config_msg(), json.dumps({"type": "reset"}), json.dumps({"type": "end"})])
    await asyncio.wait_for(handler.handle(ws), timeout=2.0)
    assert seg.resets == 1


@pytest.mark.asyncio
async def test_sample_rate_8000_triggers_resample(monkeypatch):
    """declared 8k → 16k resample 后再喂 segmenter。"""
    called_sr = []

    class _Seg(_FakeSegmenter):
        def feed(self, pcm):
            called_sr.append(len(pcm))
            return super().feed(pcm)

    seg = _Seg([])
    handler = ASRWebSocketHandler(_FakeEngine(), seg)
    ws = _FakeWS([_config_msg(sample_rate=8000), b"\x01\x00" * 160, json.dumps({"type": "end"})])
    await asyncio.wait_for(handler.handle(ws), timeout=2.0)
    # 160 samples @ 8k = 160ms;resample 到 16k = 320 samples = 640 bytes
    assert any(n == 640 for n in called_sr)


@pytest.mark.asyncio
async def test_degrade_falls_back_to_end_batch(monkeypatch):
    """segmenter.feed 抛异常 → 标记 degraded → 后续收 end 整段 recognize 兜底。"""

    class _BoomSeg(_FakeSegmenter):
        def feed(self, pcm):
            raise RuntimeError("vad boom")

    seg = _BoomSeg([])
    handler = ASRWebSocketHandler(_FakeEngine("fallback"), seg)
    ws = _FakeWS([_config_msg(), b"audiochunk", json.dumps({"type": "end"})])
    await asyncio.wait_for(handler.handle(ws), timeout=2.0)
    results = [m for m in ws.sent if m.get("type") == "result"]
    assert len(results) == 1
    assert results[0]["text"] == "fallback"
