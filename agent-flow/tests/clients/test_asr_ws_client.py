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

    async def _on_final(r):
        finals.append(r["text"])

    stream = ASRWsStream("ws://x", "c1", streaming=True, on_final=_on_final)
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
    async def _on_final(r):
        return None

    stream = ASRWsStream("ws://x", "c1", streaming=True, on_final=_on_final)
    stream._queue = asyncio.Queue()
    stream.send_reset()
    item = stream._queue.get_nowait()
    assert json.loads(item) == {"type": "reset"}


@pytest.mark.asyncio
async def test_on_final_is_awaited_not_dropped():
    """回归: on_final 为 async 时必须 await,否则协程被丢弃、轮次永不启动。

    Bug:_receiver_loop 旧版同步调用 self._on_final(result_dict),返回的协程 never awaited →
    TurnController.on_final body 永不执行 → WS 默认链路下每个通话都静默挂死。
    用 asyncio.Event 探测 body 是否真的执行过(而非仅创建了协程对象)。
    """
    fired = asyncio.Event()

    async def _cb(result):
        # 只有 body 真的执行才会 set；如果只是创建了协程对象,此行永不运行
        assert result["text"] == "hi"
        fired.set()

    stream = ASRWsStream(
        "ws://x", "c1", streaming=True, on_final=_cb,
    )
    stream._ws = _FakeWS([json.dumps({"type": "result", "text": "hi", "confidence": 0.9})])
    task = asyncio.create_task(stream._receiver_loop())
    # 若 cb 未被 await,fired 永远不 set,wait_for 会超时
    await asyncio.wait_for(fired.wait(), timeout=1.0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert fired.is_set()


@pytest.mark.asyncio
async def test_no_on_final_keeps_legacy_single_result_break():
    """o_final=None → 旧行为:首 result 后 break(receiver_loop 退出)。"""
    stream = ASRWsStream("ws://x", "c1", streaming=True, on_final=None)
    ws = _FakeWS([json.dumps({"type": "result", "text": "只此一句", "confidence": 0.9})])
    stream._ws = ws
    await stream._receiver_loop()  # 应正常返回(不抛 CancelledError)
    assert stream._result is not None
    assert stream._result["text"] == "只此一句"
