"""Telephony IO 测试。

上行：TelephonyAudioInput 处理链（jitter 平滑 → 降噪直通 → 增益 → AudioFrame）。
下行：TelephonyAudioOutput（TTSOutputBuffer 语义迁移 + SDK 播放事件硬契约）。
"""
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


# ── TelephonyAudioOutput ────────────────────────────────────────────────

_SILENCE = b"\x00\x00" * 480


def _frame(pcm: bytes):
    from livekit import rtc
    return rtc.AudioFrame(data=pcm, sample_rate=16000, num_channels=1,
                          samples_per_channel=len(pcm) // 2)


def _make_output(sent: list, **kw):
    from voice.io import TelephonyAudioOutput

    async def send_fn(frame: bytes):
        sent.append(frame)

    out = TelephonyAudioOutput(send_fn=send_fn, frame_interval=0.001, **kw)
    out.on_attached()
    return out


@pytest.mark.asyncio
async def test_output_capture_then_flush_reports_finished():
    sent: list = []
    out = _make_output(sent)
    started, finished = [], []
    out.on("playback_started", lambda e: started.append(e))
    out.on("playback_finished", lambda e: finished.append(e))

    await out.capture_frame(_frame(b"\x01\x02" * 480))  # 30ms 音频
    out.flush()
    await asyncio.sleep(0.05)
    assert started, "首帧实际发送后必须上报 playback_started"
    assert finished and finished[-1].interrupted is False
    assert sent and sent[0] != _SILENCE  # 发送的是音频帧
    await out.aclose()


@pytest.mark.asyncio
async def test_output_clear_buffer_interrupts_and_fills_silence():
    sent: list = []
    out = _make_output(sent)
    finished = []
    out.on("playback_finished", lambda e: finished.append(e))

    await out.capture_frame(_frame(b"\x01\x02" * 480))
    await out.capture_frame(_frame(b"\x03\x04" * 480))
    out.clear_buffer()  # 打断：立即清空 + interrupted=True
    assert finished and finished[-1].interrupted is True
    await asyncio.sleep(0.02)
    # 清空后转为静音帧保活（非音频数据）
    assert _SILENCE in sent
    await out.aclose()


@pytest.mark.asyncio
async def test_output_pause_stops_sending_until_resume():
    sent: list = []
    out = _make_output(sent)
    finished = []
    out.on("playback_finished", lambda e: finished.append(e))

    out.pause()  # SDK 契约：pause/resume 为同步方法
    n = len(sent)
    await out.capture_frame(_frame(b"\x01\x02" * 480))
    await asyncio.sleep(0.02)
    assert len(sent) == n, "pause 期间不应发送"
    out.resume()
    await asyncio.sleep(0.05)
    assert len(sent) > n, "resume 后恢复发送"
    out.flush()
    await asyncio.sleep(0.05)
    # pause-resume 路径同样必报 finished，漏报 → wait_for_playout 挂死
    assert finished and finished[-1].interrupted is False
    await out.aclose()


@pytest.mark.asyncio
async def test_output_recent_reverse_tracks_last_sent():
    sent: list = []
    out = _make_output(sent)
    audio = b"\x05\x06" * 480
    await out.capture_frame(_frame(audio))
    out.flush()
    await asyncio.sleep(0.05)
    assert out.recent_reverse in (audio, _SILENCE)  # 音频或其后的静音帧
    await out.aclose()


@pytest.mark.asyncio
async def test_output_prebuffer_delays_send_until_threshold():
    sent: list = []
    out = _make_output(sent, prebuffer_frames=2)
    await out.capture_frame(_frame(b"\x01\x02" * 480))
    await asyncio.sleep(0.02)
    assert not any(f != _SILENCE for f in sent), "未达 prebuffer 阈值不应发送音频"
    await out.capture_frame(_frame(b"\x03\x04" * 480))
    await asyncio.sleep(0.05)
    audio_frames = [f for f in sent if f != _SILENCE]
    assert audio_frames == [b"\x01\x02" * 480, b"\x03\x04" * 480], "达阈值后按序发出"
    await out.aclose()


@pytest.mark.asyncio
async def test_output_wait_for_playout_never_hangs():
    """SDK 硬契约：flush 正常结束与 clear_buffer 打断两条路径 wait_for_playout 均必须返回。"""
    sent: list = []
    out = _make_output(sent)

    await out.capture_frame(_frame(b"\x01\x02" * 480))
    out.flush()
    ev = await asyncio.wait_for(out.wait_for_playout(), timeout=1.0)
    assert ev.interrupted is False

    await out.capture_frame(_frame(b"\x03\x04" * 480))
    out.clear_buffer()
    ev = await asyncio.wait_for(out.wait_for_playout(), timeout=1.0)
    assert ev.interrupted is True
    await out.aclose()


@pytest.mark.asyncio
async def test_output_clear_during_send_no_spurious_started():
    """send_fn 挂起期间 clear_buffer：不得为已关段的帧触发 started，也不得压制下一段的 started。"""
    from voice.io import TelephonyAudioOutput

    sent: list = []
    started: list = []
    finished: list = []
    first_audio_sent = asyncio.Event()
    block_send = asyncio.Event()

    async def send_fn(frame: bytes) -> None:
        sent.append(frame)
        if frame != _SILENCE and not first_audio_sent.is_set():
            first_audio_sent.set()
            await block_send.wait()  # 首个音频帧发送中挂起，制造 clear_buffer 竞态窗口

    out = TelephonyAudioOutput(send_fn=send_fn, frame_interval=0.001)
    out.on_attached()
    out.on("playback_started", lambda e: started.append(e))
    out.on("playback_finished", lambda e: finished.append(e))

    await out.capture_frame(_frame(b"\x01\x02" * 480))
    await first_audio_sent.wait()  # 循环已弹帧且停在 send_fn 内
    out.clear_buffer()  # 发送期间打断
    assert finished and finished[-1].interrupted is True
    block_send.set()
    await asyncio.sleep(0.05)  # 循环恢复：不得为已关段的帧触发 started
    assert started == [], "段已关后不得触发虚假 playback_started"

    await out.capture_frame(_frame(b"\x03\x04" * 480))  # 下一 segment
    out.flush()
    await asyncio.sleep(0.05)
    assert len(started) == 1, "下一 segment 的 playback_started 恰好上报一次"
    await out.aclose()
