"""ASRWsStream 多 final + on_final + send_reset 单测。

直接驱动 _receiver_loop(注入假 _ws),不连真实 socket。
"""
import asyncio
import json

import pytest

from clients.asr_ws_client import ASRWsStream


class _FakeWS:
    """recv 依次返回 msg 列表,之后永久阻塞(模拟连接保持)。"""
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []
        self.closed = False

    async def recv(self):
        if self._messages:
            return self._messages.pop(0)
        await asyncio.sleep(10)  # 模拟连接保持,不返回

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_on_final_fires_per_result_and_loops():
    finals = []
    stream = ASRWsStream("ws://x", "c1", streaming=True,
                         on_final=lambda r: finals.append(r["text"]))
    ws = _FakeWS([
        json.dumps({"type": "result", "text": "你好", "confidence": 0.9}),
        json.dumps({"type": "result", "text": "第二句", "confidence": 0.9}),
    ])
    stream._ws = ws
    task = asyncio.create_task(stream._receiver_loop())
    # 给 receiver 时间处理两条
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert finals == ["你好", "第二句"]  # 多 final,不首条 break


@pytest.mark.asyncio
async def test_send_reset_enqueues_reset_message():
    stream = ASRWsStream("ws://x", "c1", streaming=True, on_final=lambda r: None)
    stream._queue = asyncio.Queue()
    stream.send_reset()
    item = stream._queue.get_nowait()
    assert json.loads(item) == {"type": "reset"}


@pytest.mark.asyncio
async def test_no_on_final_keeps_legacy_single_result_break():
    """o_final=None → 旧行为:首 result 后 break(receiver_loop 退出)。"""
    stream = ASRWsStream("ws://x", "c1", streaming=True, on_final=None)
    ws = _FakeWS([json.dumps({"type": "result", "text": "只此一句", "confidence": 0.9})])
    stream._ws = ws
    await stream._receiver_loop()  # 应正常返回(不抛 CancelledError)
    assert stream._result is not None
    assert stream._result["text"] == "只此一句"
