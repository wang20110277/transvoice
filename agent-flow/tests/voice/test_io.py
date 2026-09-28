"""TelephonyAudioInput 处理链测试：jitter 平滑 → 降噪直通 → 增益 → AudioFrame。"""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_input_yields_16k_mono_frames():
    from livekit import rtc
    from voice.io import TelephonyAudioInput
    from ws.jitter_buffer import JitterBuffer
    from ws.denoise import PassThroughDenoiser

    inp = TelephonyAudioInput(
        jitter=JitterBuffer(target_depth=1, max_depth=10),
        apm=None, denoiser=PassThroughDenoiser(),
        audio_gain=1.0, reverse_ref=lambda: b"",
    )
    # JitterBuffer target_depth=1：首帧即可 drain（960B = 30ms@16k）
    inp.push_bytes(b"\x01\x02" * 480)
    frame = await asyncio.wait_for(inp.__anext__(), timeout=1.0)
    assert isinstance(frame, rtc.AudioFrame)
    assert frame.sample_rate == 16000
    assert frame.num_channels == 1
    # rtc.AudioFrame.data 是 int16 memoryview，len() 计样本数（480），字节数经 bytes() 取
    assert len(bytes(frame.data)) == 960


@pytest.mark.asyncio
async def test_input_close_raises_stop_iteration():
    from voice.io import TelephonyAudioInput
    from ws.jitter_buffer import JitterBuffer
    from ws.denoise import PassThroughDenoiser

    inp = TelephonyAudioInput(
        jitter=JitterBuffer(target_depth=1, max_depth=10),
        apm=None, denoiser=PassThroughDenoiser(),
        audio_gain=1.0, reverse_ref=lambda: b"",
    )
    inp.close()
    with pytest.raises(StopAsyncIteration):
        await inp.__anext__()


@pytest.mark.asyncio
async def test_input_applies_gain():
    import numpy as np
    from voice.io import TelephonyAudioInput
    from ws.jitter_buffer import JitterBuffer
    from ws.denoise import PassThroughDenoiser

    quiet = (np.ones(480, dtype=np.int16) * 100).tobytes()
    inp = TelephonyAudioInput(
        jitter=JitterBuffer(target_depth=1, max_depth=10),
        apm=None, denoiser=PassThroughDenoiser(),
        audio_gain=2.0, reverse_ref=lambda: b"",
    )
    inp.push_bytes(quiet)
    frame = await asyncio.wait_for(inp.__anext__(), timeout=1.0)
    out = np.frombuffer(frame.data, dtype=np.int16)
    assert abs(int(out[0]) - 200) <= 1  # gain 放大
