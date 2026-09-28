"""TransvoiceSTT：协议映射 + 事件序列 + 短 final 过滤 + 错误分类。

与 brief 的差异（SDK 1.8.3 实测）：
- `stt.APIConnectionError` 不存在（livekit.agents.stt 包未 re-export），从 livekit.agents 顶层导入；
- 结束时 `await stream.aclose()` 清理后台任务，避免 "Task was destroyed but it is pending" 噪音。
"""
import asyncio
import json

import pytest
from livekit.agents import APIConnectionError, stt


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


@pytest.mark.asyncio
async def test_multi_final_event_sequence(monkeypatch):
    import voice.stt_plugin as sp
    fake = FakeUpstreamWs([
        json.dumps({"type": "result", "text": "你好请问", "confidence": 0.9}),
        json.dumps({"type": "result", "text": "是张先生吗", "confidence": 0.9}),
    ])

    async def fake_connect(*a, **kw):
        return fake
    monkeypatch.setattr(sp.websockets, "connect", fake_connect)

    plugin = sp.TransvoiceSTT(ws_url="ws://fake")
    stream = plugin.stream(language="zh")
    events = []
    consume = asyncio.create_task(_collect(stream, events, n=6))
    await asyncio.sleep(0.1)
    stream.push_frame(_audio_frame(b"\x01\x02" * 480))
    await asyncio.wait_for(consume, timeout=2.0)
    await stream.aclose()

    types = [e.type for e in events]
    assert types == [
        stt.SpeechEventType.START_OF_SPEECH,
        stt.SpeechEventType.FINAL_TRANSCRIPT,
        stt.SpeechEventType.END_OF_SPEECH,
    ] * 2
    finals = [e for e in events if e.type == stt.SpeechEventType.FINAL_TRANSCRIPT]
    assert finals[0].alternatives[0].text == "你好请问"
    assert finals[1].alternatives[0].text == "是张先生吗"


@pytest.mark.asyncio
async def test_short_final_dropped_with_eos(monkeypatch):
    import voice.stt_plugin as sp
    fake = FakeUpstreamWs([
        json.dumps({"type": "result", "text": "嗯", "confidence": 0.5}),
    ])
    async def fake_connect(*a, **kw):
        return fake
    monkeypatch.setattr(sp.websockets, "connect", fake_connect)

    plugin = sp.TransvoiceSTT(ws_url="ws://fake")
    stream = plugin.stream(language="zh")
    events = []
    consume = asyncio.create_task(_collect(stream, events, n=1, timeout=0.3))
    await asyncio.sleep(0.1)
    stream.push_frame(_audio_frame(b"\x01\x02" * 480))
    await consume  # 应超时收不到任何事件
    await stream.aclose()
    assert events == []


@pytest.mark.asyncio
async def test_upstream_disconnect_raises_connection_error(monkeypatch):
    import websockets
    import voice.stt_plugin as sp
    fake = FakeUpstreamWs([websockets.ConnectionClosed(None, None)])
    async def fake_connect(*a, **kw):
        return fake
    monkeypatch.setattr(sp.websockets, "connect", fake_connect)

    plugin = sp.TransvoiceSTT(ws_url="ws://fake")
    stream = plugin.stream(language="zh")
    stream.push_frame(_audio_frame(b"\x01\x02" * 480))
    with pytest.raises(APIConnectionError):
        await asyncio.wait_for(_drain_stream(stream), timeout=2.0)
    await stream.aclose()


def _audio_frame(pcm: bytes):
    from livekit import rtc
    return rtc.AudioFrame(data=pcm, sample_rate=16000, num_channels=1,
                          samples_per_channel=len(pcm) // 2)


async def _collect(stream, out, n, timeout=2.0):
    async def _t():
        async for ev in stream:
            out.append(ev)
            if len(out) >= n:
                return
    try:
        await asyncio.wait_for(_t(), timeout=timeout)
    except asyncio.TimeoutError:
        pass


async def _drain_stream(stream):
    async for _ in stream:
        pass
