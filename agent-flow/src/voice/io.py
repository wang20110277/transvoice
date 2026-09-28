"""Telephony IO —— mod_audio_fork WebSocket ↔ livekit AgentSession 音频桥。

上行链复刻原 StreamingCallHandler._process_near_frame 语义：
JitterBuffer 平滑 → WebRTCAPM（AEC，远端参考=下行最近帧）或 denoiser → 增益。
下行链为原 TTSOutputBuffer 语义迁移：30ms 匀速排出 + 静音帧保活 + prebuffer，
并满足 SDK AudioOutput 播放事件硬契约（flush/clear_buffer 必报 playback_finished，
漏报会导致 AgentSession wait_for_playout 永久挂死）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable

from livekit import rtc
from livekit.agents.voice.io import AudioInput, AudioOutput, AudioOutputCapabilities

from ws.audio_processing import WebRTCAPM
from ws.denoise import BaseDenoiser, PassThroughDenoiser
from ws.jitter_buffer import FRAME_BYTES, FRAME_DURATION_MS, SILENCE_FRAME, JitterBuffer

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_BYTES_PER_SAMPLE = 2
_FRAME_DURATION = FRAME_DURATION_MS / 1000.0
# write 后静音填充窗口：覆盖打断 → ASR → LLM → 首句 TTS 全链路延迟；
# 静音帧 RMS=0 不触发 barge-in，超窗停发避免回声路径持续活跃（原 TTSOutputBuffer 语义）
_SILENCE_TIMEOUT = 120.0


def _apply_gain(pcm: bytes, gain: float) -> bytes:
    if gain == 1.0 or len(pcm) < 2:
        return pcm
    import numpy as np
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    samples *= gain
    return np.clip(samples, -32768, 32767).astype(np.int16).tobytes()


class TelephonyAudioInput(AudioInput):
    """WS 帧入队 → 处理链 → 16k mono AudioFrame。

    __anext__ 阻塞即背压（AgentSession forward task 消费）；
    close() 后抛 StopAsyncIteration 触发 session teardown。
    """

    def __init__(
        self,
        *,
        jitter: JitterBuffer,
        apm: WebRTCAPM | None,
        denoiser: BaseDenoiser | None,
        audio_gain: float,
        reverse_ref: Callable[[], bytes],
    ) -> None:
        super().__init__(label="TelephonyIO")
        self._jitter = jitter
        self._apm = apm
        self._denoiser = denoiser or PassThroughDenoiser()
        self._gain = audio_gain
        self._reverse_ref = reverse_ref
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def push_bytes(self, pcm: bytes) -> None:
        self._queue.put_nowait(pcm)

    def close(self) -> None:
        self._queue.put_nowait(None)  # 哨兵

    async def __anext__(self) -> rtc.AudioFrame:
        pcm = await self._queue.get()
        if pcm is None:
            raise StopAsyncIteration
        self._jitter.insert(pcm)
        processed = bytearray()
        while True:
            smooth = self._jitter.drain()
            if not smooth:
                break
            processed.extend(self._process_near(smooth))
        if not processed:
            return await self.__anext__()
        data = _apply_gain(bytes(processed), self._gain)
        return rtc.AudioFrame(
            data=data, sample_rate=_SAMPLE_RATE,
            num_channels=1, samples_per_channel=len(data) // _BYTES_PER_SAMPLE,
        )

    def _process_near(self, frame: bytes) -> bytes:
        if self._apm is not None:
            return self._apm.process(frame, self._reverse_ref())
        return self._denoiser.process(frame)


class TelephonyAudioOutput(AudioOutput):
    """下行 sink：capture_frame 可快于实时 → 内部 30ms 匀速排出 + 静音帧保活。

    原 TTSOutputBuffer 的 paced 循环整体迁移；SDK 契约补充：每个 segment
    （capture_frame…flush / clear_buffer 界定）必须恰好回报一次 playback_finished，
    漏报 → AgentSession.wait_for_playout 永久挂死。
    """

    def __init__(
        self,
        *,
        send_fn: Callable[[bytes], Awaitable[None]],
        prebuffer_frames: int = 0,
        frame_interval: float = _FRAME_DURATION,
    ) -> None:
        super().__init__(
            label="TelephonyIO",
            capabilities=AudioOutputCapabilities(pause=True),
            sample_rate=_SAMPLE_RATE,
        )
        self._send_fn = send_fn
        self._frame_interval = frame_interval
        self._prebuffer_frames = prebuffer_frames
        self._buffer: deque[bytes] = deque()
        self._partial = bytearray()
        self._segment_open = False  # 有 segment 已 capture 且尚未回报 finished
        self._finished = False  # flush 后排空即结束当前段
        self._prebuffering = prebuffer_frames > 0
        self._paused = False
        self._playback_started = False
        self._frames_sent = 0  # 当前段已实际发出的音频帧数（静音帧不计）
        self._last_write_time = 0.0
        self._send_task: asyncio.Task | None = None
        self._cancel = asyncio.Event()
        self._data_ready = asyncio.Event()
        # AEC 远端参考：镜像最近发往 FreeSWITCH 的帧（TTS 帧或静音帧）
        self.recent_reverse: bytes = SILENCE_FRAME

    # ── SDK 契约面 ──

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        if self._send_task is not None and self._send_task.done():
            return  # 发送循环已退出（send 失败/已关闭）：不计段不缓存，防 wait_for_playout 挂死
        await super().capture_frame(frame)  # 基类段计数：首 capture 计 1 个 segment
        self._last_write_time = time.monotonic()
        if not self._segment_open:
            self._segment_open = True
            self._prebuffering = self._prebuffer_frames > 0  # 每段重新预缓冲（原 per-turn 语义）
        self._partial.extend(bytes(frame.data))
        while len(self._partial) >= FRAME_BYTES:
            self._buffer.append(bytes(self._partial[:FRAME_BYTES]))
            self._partial = self._partial[FRAME_BYTES:]
        if self._prebuffering and len(self._buffer) >= self._prebuffer_frames:
            self._prebuffering = False
        self._data_ready.set()

    def flush(self) -> None:
        super().flush()  # 基类复位 capturing 标志
        if self._partial:
            self._buffer.append(bytes(self._partial))  # 残尾不足一帧也发出（原语义）
            self._partial.clear()
        self._prebuffering = False  # 未满阈值但已结束 → 立即播放已累积帧
        self._finished = True
        self._data_ready.set()

    def clear_buffer(self) -> None:
        self._buffer.clear()
        self._partial.clear()
        self._finished = False
        if self._segment_open:
            # 打断必须同步回报：AgentSession 中断路径 clear_buffer 后立即 wait_for_playout
            self.on_playback_finished(
                playback_position=self._frames_sent * _FRAME_DURATION,
                interrupted=True,
            )
            self._close_segment()
        self._data_ready.set()

    def pause(self) -> None:
        # SDK 契约：同步方法（agent_activity 直接 audio_output.pause()，async 覆写会静默失效）
        self._paused = True

    def resume(self) -> None:
        self._paused = False
        self._data_ready.set()

    # ── 生命周期 ──

    def on_attached(self) -> None:
        if self._send_task is None or self._send_task.done():
            self._cancel.clear()
            self._send_task = asyncio.create_task(
                self._send_loop(), name="telephony-audio-output"
            )

    def on_detached(self) -> None:
        self._cancel.set()
        self._data_ready.set()

    async def aclose(self) -> None:
        self.on_detached()
        if self._send_task and not self._send_task.done():
            self._send_task.cancel()
            try:
                await self._send_task
            except asyncio.CancelledError:
                pass
        self._send_task = None
        self._buffer.clear()
        self._partial.clear()
        self._finished = False
        self._close_segment()

    def _close_segment(self) -> None:
        self._segment_open = False
        self._playback_started = False
        self._frames_sent = 0

    # ── 匀速发送循环（原 TTSOutputBuffer._send_loop 语义）──

    async def _send_loop(self) -> None:
        try:
            while not self._cancel.is_set():
                if self._paused:
                    self._data_ready.clear()
                    await self._data_ready.wait()
                    continue
                if self._prebuffering:
                    self._data_ready.clear()
                    await self._data_ready.wait()
                    continue
                if self._buffer:
                    frame = self._buffer.popleft()
                    await self._send_fn(frame)
                    self.recent_reverse = frame  # AEC 远端参考 = 此刻发往线路的帧
                    if not self._segment_open:
                        # send 挂起期间 clear_buffer 已关段：帧物理发出但不归属任何
                        # open segment，不得再触发 started/progressed（否则 finished
                        # 之后出现无终止的 started，且 _playback_started 泄漏压制下一段）
                        continue
                    now = time.time()
                    if not self._playback_started:
                        self._playback_started = True
                        self.on_playback_started(created_at=now)
                    self.on_playback_progressed(
                        started_at=now,
                        offset=self._frames_sent * _FRAME_DURATION,
                        duration=_FRAME_DURATION,
                    )
                    self._frames_sent += 1
                    await asyncio.sleep(self._frame_interval)
                    continue
                if self._finished:
                    # 段排空：正常结束回报（硬契约：与 clear_buffer 打断回报对称）
                    if self._segment_open:
                        self.on_playback_finished(
                            playback_position=self._frames_sent * _FRAME_DURATION,
                            interrupted=False,
                        )
                        self._close_segment()
                    self._finished = False
                    continue
                await self._fill_silence_if_active()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("TelephonyAudioOutput send loop exited: %s", e)

    async def _fill_silence_if_active(self) -> None:
        """缓冲空且段未结束：句间间隙发静音帧保活（120s 窗口）。"""
        elapsed = time.monotonic() - self._last_write_time
        if self._last_write_time > 0 and elapsed < _SILENCE_TIMEOUT:
            self._data_ready.clear()
            try:
                await asyncio.wait_for(self._data_ready.wait(), timeout=self._frame_interval)
            except asyncio.TimeoutError:
                pass
            if not self._buffer and not self._cancel.is_set() and not self._finished:
                await self._send_fn(SILENCE_FRAME)
                self.recent_reverse = SILENCE_FRAME  # AI 沉默 → AEC 参考归零
        else:
            # 回合间：超窗停发静音，避免音频路径持续活跃触发回声误 barge-in
            self._data_ready.clear()
            await self._data_ready.wait()
