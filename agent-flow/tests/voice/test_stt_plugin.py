"""TransvoiceSTT：非流式批量 recognize —— 协议交互 + 短文本过滤 + 分帧 + 错误分类。

与 design.md 的差异记录：
- `_SINGLE_ATTEMPT_CONN_OPTIONS` 已随 stream() 路径删除（计划「偏差记录」）；
- 直调 recognize 时传 `APIConnectOptions(max_retry=0)` 保证错误即抛（基类不重试）。
"""
import asyncio
import json

import pytest
import websockets
from livekit import rtc
from livekit.agents import APIConnectionError, APIConnectOptions


class FakeUpstreamWs:
    """模拟 agent-asr ws server：录下发件，按脚本推收件。"""

    def __init__(self, incoming: list):
        self.sent: list = []
        self._incoming = list(incoming)
        self.closed = False

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if not self._incoming:
            await asyncio.sleep(3600)  # 保持打开直到取消
        item = self._incoming.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self):
        self.closed = True


def _buffer(nbytes: int) -> rtc.AudioFrame:
    return rtc.AudioFrame(data=b"\x01\x02" * (nbytes // 2), sample_rate=16000,
                          num_channels=1, samples_per_channel=nbytes // 2)


def _patch(monkeypatch, fake):
    import voice.stt_plugin as sp

    async def fake_connect(*a, **kw):
        return fake

    monkeypatch.setattr(sp.websockets, "connect", fake_connect)
    return sp


_NO_RETRY = {"conn_options": APIConnectOptions(max_retry=0)}


@pytest.mark.asyncio
async def test_recognize_returns_final_event(monkeypatch):
    fake = FakeUpstreamWs([
        json.dumps({"type": "result", "text": "你好请问", "confidence": 0.9})])
    sp = _patch(monkeypatch, fake)

    ev = await sp.TransvoiceSTT(ws_url="ws://fake").recognize(
        _buffer(960), language="zh", **_NO_RETRY)

    assert ev.type == sp.SpeechEventType.FINAL_TRANSCRIPT
    assert ev.alternatives[0].text == "你好请问"
    assert ev.alternatives[0].confidence == 0.9
    # 发帧序列：config → 音频 → end → 关连接
    cfg = json.loads(fake.sent[0])
    assert cfg["type"] == "config" and cfg["sample_rate"] == 16000
    assert fake.sent[1] == b"\x01\x02" * 480
    assert json.loads(fake.sent[-1])["type"] == "end"
    assert fake.closed


@pytest.mark.asyncio
async def test_short_text_returns_empty_alternatives(monkeypatch):
    fake = FakeUpstreamWs([
        json.dumps({"type": "result", "text": "嗯", "confidence": 0.5})])
    sp = _patch(monkeypatch, fake)

    ev = await sp.TransvoiceSTT(ws_url="ws://fake").recognize(
        _buffer(960), language="zh", **_NO_RETRY)

    assert ev.type == sp.SpeechEventType.FINAL_TRANSCRIPT
    assert ev.alternatives == []  # StreamAdapter 据此跳过 FINAL


@pytest.mark.asyncio
async def test_large_audio_chunked_to_64k(monkeypatch):
    fake = FakeUpstreamWs([
        json.dumps({"type": "result", "text": "长段文本", "confidence": 0.9})])
    sp = _patch(monkeypatch, fake)

    total = 200 * 1024
    await sp.TransvoiceSTT(ws_url="ws://fake").recognize(
        _buffer(total), language="zh", **_NO_RETRY)

    audio_frames = fake.sent[1:-1]
    assert all(isinstance(f, bytes) and len(f) <= 64 * 1024 for f in audio_frames)
    assert b"".join(audio_frames) == b"\x01\x02" * (total // 2)


@pytest.mark.asyncio
async def test_error_message_raises_connection_error(monkeypatch):
    fake = FakeUpstreamWs([json.dumps({"type": "error", "message": "boom"})])
    sp = _patch(monkeypatch, fake)

    with pytest.raises(APIConnectionError):
        await sp.TransvoiceSTT(ws_url="ws://fake").recognize(
            _buffer(960), language="zh", **_NO_RETRY)


@pytest.mark.asyncio
async def test_disconnect_raises_connection_error(monkeypatch):
    fake = FakeUpstreamWs([websockets.ConnectionClosed(None, None)])
    sp = _patch(monkeypatch, fake)

    with pytest.raises(APIConnectionError):
        await sp.TransvoiceSTT(ws_url="ws://fake").recognize(
            _buffer(960), language="zh", **_NO_RETRY)
