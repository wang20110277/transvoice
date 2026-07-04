"""AsrStreamingManager on_final 通透 + reset_server_segment 单测。"""
import asyncio

import pytest

from ws.asr_streaming import AsrStreamingManager


class _FakeStream:
    def __init__(self):
        self.reset_calls = 0
        self.audio = []

    async def start(self): pass

    def send_audio(self, chunk): self.audio.append(chunk)

    def send_reset(self): self.reset_calls += 1

    async def finish(self): return {"text": "x", "confidence": 0.9, "is_final": True}

    async def cancel(self): pass


class _FakeClient:
    def __init__(self):
        self.last_on_final = None

    def create_stream(self, call_id, streaming=False, on_partial=None, on_final=None):
        self.last_on_final = on_final
        return _FakeStream()


@pytest.mark.asyncio
async def test_feed_threads_on_final_to_stream():
    client = _FakeClient()

    async def _on_final(r):
        return None

    mgr = AsrStreamingManager(asr_ws_client=client, use_ws_streaming=True, on_final=_on_final)
    await mgr.feed(b"\x00" * 960, "c1")
    assert client.last_on_final is not None


@pytest.mark.asyncio
async def test_reset_server_segment_sends_reset():
    client = _FakeClient()

    async def _on_final(r):
        return None

    mgr = AsrStreamingManager(asr_ws_client=client, use_ws_streaming=True, on_final=_on_final)
    await mgr.feed(b"\x00" * 960, "c1")  # 建 stream
    await mgr.reset_server_segment("c1")
    assert mgr._stream.reset_calls == 1


@pytest.mark.asyncio
async def test_reset_no_provider_is_noop():
    mgr = AsrStreamingManager()  # 无 provider
    await mgr.reset_server_segment("c1")  # 不抛
