"""Telephony IO —— mod_audio_fork WebSocket ↔ livekit AgentSession 音频桥。

上行链复刻原 StreamingCallHandler._process_near_frame 语义：
JitterBuffer 平滑 → WebRTCAPM（AEC，远端参考=下行最近帧）或 denoiser → 增益。
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from livekit import rtc
from livekit.agents.voice.io import AudioInput

from ws.audio_processing import WebRTCAPM
from ws.denoise import BaseDenoiser, PassThroughDenoiser
from ws.jitter_buffer import JitterBuffer

_SAMPLE_RATE = 16000
_BYTES_PER_SAMPLE = 2


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
