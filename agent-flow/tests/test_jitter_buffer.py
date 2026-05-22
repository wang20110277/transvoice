"""Jitter Buffer & TTS Output Buffer 单元测试"""
import asyncio
import pytest
from unittest.mock import AsyncMock
from ws.jitter_buffer import JitterBuffer, TTSOutputBuffer, FRAME_BYTES


# ── JitterBuffer (receive side) ──

class TestJitterBuffer:
    def test_prefill_before_drain(self):
        jb = JitterBuffer(target_depth=3, max_depth=10)
        # 不足 target_depth，drain 返回空
        jb.insert(b"\x00" * FRAME_BYTES)
        jb.insert(b"\x00" * FRAME_BYTES)
        assert jb.drain() == b""
        assert not jb.is_draining

        # 第 3 帧后开始输出
        jb.insert(b"\x00" * FRAME_BYTES)
        frame = jb.drain()  # 触发 prefill，返回第 1 帧
        assert jb.is_draining
        assert jb.depth == 2  # 已取 1 帧，剩 2 帧
        assert len(frame) == FRAME_BYTES

    def test_overflow_drops_oldest(self):
        jb = JitterBuffer(target_depth=1, max_depth=3)
        # 填满
        for i in range(3):
            jb.insert(bytes([i] * FRAME_BYTES))

        assert jb.depth == 3
        # 再插一帧 → 溢出
        jb.insert(bytes([0xFF] * FRAME_BYTES))
        assert jb.stats.overflows == 1
        assert jb.depth == 3

    def test_underflow_returns_empty(self):
        jb = JitterBuffer(target_depth=1, max_depth=5)
        jb.insert(b"\x00" * FRAME_BYTES)
        jb.drain()  # 取出唯一一帧
        assert jb.drain() == b""
        assert jb.stats.underflows == 1

    def test_drain_all_returns_everything(self):
        jb = JitterBuffer(target_depth=1, max_depth=10)
        jb.insert(b"\xAA" * FRAME_BYTES * 3)
        result = jb.drain_all()
        assert len(result) == FRAME_BYTES * 3
        assert jb.depth == 0

    def test_reset_clears_state(self):
        jb = JitterBuffer(target_depth=2, max_depth=10)
        jb.insert(b"\x00" * FRAME_BYTES * 3)
        jb.drain()  # triggers prefill
        assert jb.is_draining

        jb.reset()
        assert jb.depth == 0
        assert not jb.is_draining

    def test_partial_frame_accumulates(self):
        jb = JitterBuffer(target_depth=1, max_depth=10)
        # 不足一帧的数据
        jb.insert(b"\x00" * 100)
        assert jb.depth == 0
        # 补齐到一帧
        jb.insert(b"\x00" * (FRAME_BYTES - 100))
        assert jb.depth == 1

    def test_stats_tracking(self):
        jb = JitterBuffer(target_depth=1, max_depth=2)
        jb.insert(b"\x00" * FRAME_BYTES)
        assert jb.stats.total_in == 1
        jb.drain()
        assert jb.stats.total_out == 1


# ── TTSOutputBuffer (send side) ──

class TestTTSOutputBuffer:
    @pytest.mark.asyncio
    async def test_steady_frame_delivery(self):
        sent_frames: list[bytes] = []
        send_fn = AsyncMock(side_effect=lambda f: sent_frames.append(f))

        buf = TTSOutputBuffer(send_fn=send_fn, frame_interval=0.01)
        await buf.start()

        # Write 3 frames worth of PCM
        pcm = b"\xAA" * FRAME_BYTES * 3
        buf.write(pcm)
        buf.finish()

        await buf.wait_drained(timeout=2.0)

        assert send_fn.call_count == 3
        for frame in sent_frames:
            assert len(frame) == FRAME_BYTES

    @pytest.mark.asyncio
    async def test_partial_frame_flushed_on_finish(self):
        sent_frames: list[bytes] = []
        send_fn = AsyncMock(side_effect=lambda f: sent_frames.append(f))

        buf = TTSOutputBuffer(send_fn=send_fn, frame_interval=0.01)
        await buf.start()

        buf.write(b"\x00" * 100)  # 不足一帧
        buf.finish()

        await buf.wait_drained(timeout=2.0)

        assert send_fn.call_count == 1
        assert len(sent_frames[0]) == 100

    @pytest.mark.asyncio
    async def test_stop_cancels_delivery(self):
        send_fn = AsyncMock()
        buf = TTSOutputBuffer(send_fn=send_fn, frame_interval=0.01)
        await buf.start()

        buf.write(b"\x00" * FRAME_BYTES * 5)
        # Stop immediately before drain completes
        await asyncio.sleep(0.02)
        await buf.stop()

        # Should have sent fewer than 5 frames
        assert send_fn.call_count < 5
        assert not buf.is_running

    @pytest.mark.asyncio
    async def test_is_running_property(self):
        send_fn = AsyncMock()
        buf = TTSOutputBuffer(send_fn=send_fn, frame_interval=0.01)

        assert not buf.is_running
        await buf.start()
        assert buf.is_running
        await buf.stop()
        assert not buf.is_running
