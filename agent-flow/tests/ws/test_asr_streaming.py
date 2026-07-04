"""AsrStreamingManager 单元测试 — 封装 ASR 流生命周期（feed→finalize/cancel）。

注入 fake provider/stream，验证首帧建流、多帧喂送、partial fallback、cancel 丢弃。
不依赖真实 WS 客户端。
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(_SRC))

from ws.asr_streaming import AsrStreamingManager  # noqa: E402


class _FakeStream:
    def __init__(self):
        self.started = False
        self.sent = []
        self.finish_result = {"text": "你好", "confidence": 0.9, "is_final": True}
        self.cancelled = False

    async def start(self): self.started = True
    def send_audio(self, chunk): self.sent.append(chunk)
    async def finish(self): return self.finish_result
    async def cancel(self): self.cancelled = True


class _PartialStream(_FakeStream):
    """finish 返回 None，模拟尾帧丢失，触发 partial fallback。"""
    def __init__(self):
        super().__init__()
        self.finish_result = None


class _FakeProvider:
    def __init__(self, stream):
        self._stream = stream
        self.created_with = None

    def create_stream(self, call_id, streaming=False, on_partial=None, on_final=None):
        self.created_with = dict(call_id=call_id, streaming=streaming, has_partial=on_partial is not None)
        return self._stream


import pytest


@pytest.mark.asyncio
async def test_no_provider_is_noop():
    """无 provider(WS 没配)时 feed/finalize 静默返回。"""
    mgr = AsrStreamingManager()  # 全部默认 None/False
    await mgr.feed(b"\x00" * 960, "call1")
    assert mgr.stream is None
    assert await mgr.finalize("call1") is None


@pytest.mark.asyncio
async def test_creates_stream_on_first_feed_only():
    """首帧创建并 start 流；后续帧只 send_audio，不重复建流。"""
    stream = _FakeStream()
    prov = _FakeProvider(stream)
    mgr = AsrStreamingManager(asr_ws_client=prov, use_ws_streaming=True)

    await mgr.feed(b"\x01" * 960, "call1")
    assert stream.started is True
    assert prov.created_with["call_id"] == "call1"

    await mgr.feed(b"\x02" * 960, "call1")
    assert mgr.stream is stream  # 同一实例未重建
    assert stream.sent == [b"\x01" * 960, b"\x02" * 960]


@pytest.mark.asyncio
async def test_finalize_returns_result_and_resets():
    """finalize 收尾取结果，重置流状态供下一轮复用。"""
    stream = _FakeStream()
    mgr = AsrStreamingManager(asr_ws_client=_FakeProvider(stream), use_ws_streaming=True)
    await mgr.feed(b"\x01" * 960, "call1")

    result = await mgr.finalize("call1")
    assert result == {"text": "你好", "confidence": 0.9, "is_final": True}
    assert mgr.stream is None  # 已重置


@pytest.mark.asyncio
async def test_finalize_partial_fallback_when_finish_empty():
    """finish 返回空但收到过 partial 时，回退 partial 文本兜底（避免整轮丢字）。"""
    stream = _PartialStream()

    class _StreamingProvider(_FakeProvider):
        def create_stream(self, call_id, streaming=False, on_partial=None, on_final=None):
            self._on_partial = on_partial
            return super().create_stream(call_id, streaming, on_partial)

    sp = _StreamingProvider(stream)
    mgr = AsrStreamingManager(
        asr_ws_client=sp, use_ws_streaming=True, use_streaming_asr=True,
    )
    await mgr.feed(b"\x01" * 960, "call1")
    sp._on_partial("尾字丢失", 0.5)  # 模拟 WS partial 推送

    result = await mgr.finalize("call1")
    assert result == {"text": "尾字丢失", "confidence": 0.8, "is_final": True}


@pytest.mark.asyncio
async def test_cancel_discards_stream():
    """cancel 取消并丢弃流，状态归零（barge-in 清理用）。"""
    stream = _FakeStream()
    mgr = AsrStreamingManager(asr_ws_client=_FakeProvider(stream), use_ws_streaming=True)
    await mgr.feed(b"\x01" * 960, "call1")

    await mgr.cancel()
    assert stream.cancelled is True
    assert mgr.stream is None


@pytest.mark.asyncio
async def test_finalize_without_feed_returns_none():
    """未喂过帧（流未启动）时 finalize 返回 None。"""
    mgr = AsrStreamingManager(asr_ws_client=_FakeProvider(_FakeStream()), use_ws_streaming=True)
    assert await mgr.finalize("call1") is None
