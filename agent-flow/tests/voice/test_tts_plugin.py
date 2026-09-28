"""TransvoiceTTS：整句聚合请求 + segment 生命周期 + 迟到音频过滤。

与 brief 的差异（SDK 1.8.3 实测）：
- 结束时 `await tts.aclose()` 清理共享连接后台 reader，避免
  "Task was destroyed but it is pending" 噪音（同 Task 4 STT 插件做法）。
"""
import asyncio
import json

import pytest
import websockets

from voice.tts_plugin import TransvoiceTTS, _SharedTtsConnection


class FakeTtsServer:
    """模拟 agent-tts：audio_header → 二进制块 → result。可注入迟到音频。"""

    def __init__(self):
        self.requests: list[dict] = []
        self.incoming: list = []   # 服务端主动推的（迟到音频场景）
        self.closed = False

    async def send(self, data):
        if isinstance(data, str):
            msg = json.loads(data)
            self.requests.append(msg)
            if msg.get("type") == "synthesize":
                rid = msg["request_id"]
                self.incoming.extend([
                    json.dumps({"type": "audio_header", "request_id": rid}),
                    b"\x01\x02" * 160,  # 一块 22050Hz PCM
                    json.dumps({"type": "result", "request_id": rid,
                                "chunks_sent": 1, "duration_ms": 10}),
                ])

    async def recv(self):
        # 轮询而非一次性 sleep(3600)：第二次 send() 追加的响应也要能唤醒已阻塞的
        # recv（真实 ws recv 语义；brief 原版会睡死导致第二段请求永远无人消费）
        while not self.incoming:
            await asyncio.sleep(0.01)
        item = self.incoming.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_two_sentences_two_requests(monkeypatch):
    import voice.tts_plugin as tp
    fake = FakeTtsServer()
    async def fake_connect(*a, **kw):
        return fake
    monkeypatch.setattr(tp.websockets, "connect", fake_connect)

    tts = TransvoiceTTS(ws_url="ws://fake", biz_type="collection", call_id="c1")
    stream = tts.stream()
    chunks = []

    async def consume():
        async for audio in stream:
            chunks.append(bytes(audio.frame.data))
    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)

    stream.push_text("你好。")
    stream.flush()
    stream.push_text("请问是本人吗？")
    stream.flush()
    stream.end_input()
    await asyncio.wait_for(task, timeout=3.0)

    synth = [r for r in fake.requests if r.get("type") == "synthesize"]
    assert len(synth) == 2
    assert synth[0]["text"] == "你好。"
    assert synth[1]["text"] == "请问是本人吗？"
    assert all(r["biz_type"] == "collection" for r in synth)
    assert len(chunks) == 2  # 每句一块音频
    await tts.aclose()


@pytest.mark.asyncio
async def test_cancelled_segment_audio_dropped(monkeypatch):
    import voice.tts_plugin as tp
    fake = FakeTtsServer()

    async def fake_connect(*a, **kw):
        return fake
    monkeypatch.setattr(tp.websockets, "connect", fake_connect)

    conn = _SharedTtsConnection(ws_url="ws://fake")
    await conn.connect()
    q1 = conn.register("r1")
    # r1 已取消（unregister）后，迟到音频应被丢弃
    conn.unregister("r1")
    fake.incoming.extend([
        json.dumps({"type": "audio_header", "request_id": "r1"}),
        b"\x09\x09" * 100,
        json.dumps({"type": "result", "request_id": "r1"}),
    ])
    await asyncio.sleep(0.1)
    assert q1.empty(), "已取消 request 的迟到音频必须被丢弃"
    await conn.close()
