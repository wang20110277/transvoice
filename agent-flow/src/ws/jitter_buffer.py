"""Jitter Buffer — 抖动缓冲区，平滑网络音频包时序差异

FreeSWITCH mod_audio_fork 通过 WebSocket 发送音频帧，网络抖动会导致帧间隔不均匀。
Jitter Buffer 累积一定量的帧后以稳定间隔输出，保证 VAD 和 ASR 收到连续均匀的音频流。

参考: 基于WebSocket与软交换构建实时AI语音助手全链路优化 — Jitter Buffer 章节
"""
import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 16kHz 16-bit mono: 30ms frame = 960 bytes
FRAME_DURATION_MS = 30
SAMPLE_RATE = 16000
FRAME_BYTES = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000) * 2  # 960 bytes


@dataclass
class _TimedFrame:
    data: bytes
    recv_time: float  # monotonic timestamp


@dataclass
class JitterBufferStats:
    """抖动缓冲区统计"""
    total_in: int = 0
    total_out: int = 0
    overflows: int = 0
    underflows: int = 0
    current_depth: int = 0


class JitterBuffer:
    """基于 deque 的抖动缓冲区。

    工作模式:
    1. insert(): 收到 WebSocket 音频帧时调用，带时间戳入队
    2. drain(): 按需取出累积的音频（给 VAD/ASR）
    3. flush(): 取出所有剩余数据

    策略:
    - 缓冲到 target_depth 帧后才开始输出（初始预填充）
    - 如果缓冲超过 max_depth 帧，丢弃最旧的帧（溢出）
    - 如果缓冲为空，返回空 bytes（下溢，由调用方处理）
    """

    def __init__(
        self,
        frame_size: int = FRAME_BYTES,
        target_depth: int = 3,
        max_depth: int = 10,
    ) -> None:
        """
        Args:
            frame_size: 单帧字节数 (默认 480 = 30ms @ 8kHz 16-bit)
            target_depth: 预填充帧数，累积到此深度后开始输出
                         3帧 = 90ms 缓冲延迟，适合大多数网络环境
            max_depth: 最大缓冲帧数，超出时丢弃旧帧
        """
        self._frame_size = frame_size
        self._target_depth = target_depth
        self._max_depth = max_depth
        self._buffer: deque[_TimedFrame] = deque(maxlen=max_depth)
        self._prefilled = False
        self._partial: bytearray = bytearray()
        self._stats = JitterBufferStats()

    @property
    def stats(self) -> JitterBufferStats:
        return self._stats

    @property
    def depth(self) -> int:
        """当前缓冲帧数。"""
        return len(self._buffer)

    def insert(self, data: bytes) -> None:
        """插入音频数据（可以是任意长度，内部按帧拆分）。"""
        now = time.monotonic()
        self._partial.extend(data)
        self._stats.total_in += 1

        # 拆帧入队
        while len(self._partial) >= self._frame_size:
            frame = bytes(self._partial[:self._frame_size])
            self._partial = self._partial[self._frame_size:]

            if len(self._buffer) >= self._max_depth:
                self._buffer.popleft()
                self._stats.overflows += 1

            self._buffer.append(_TimedFrame(data=frame, recv_time=now))

        self._stats.current_depth = len(self._buffer)

    def drain(self) -> bytes:
        """取出一帧音频。

        预填充阶段: 累积到 target_depth 帧前返回空
        正常阶段: 每次取一帧
        下溢: 缓冲空时返回空
        """
        if not self._prefilled:
            if len(self._buffer) < self._target_depth:
                return b""
            self._prefilled = True
            logger.debug("jitter buffer prefilled, depth=%d", len(self._buffer))

        if not self._buffer:
            self._stats.underflows += 1
            return b""

        frame = self._buffer.popleft()
        self._stats.total_out += 1
        self._stats.current_depth = len(self._buffer)
        return frame.data

    def drain_all(self) -> bytes:
        """取出所有缓冲帧 + 残余数据（用于 end-of-speech 时一次性交给 ASR）。"""
        parts = [f.data for f in self._buffer]
        parts.append(bytes(self._partial))
        self._buffer.clear()
        self._partial.clear()
        self._prefilled = False
        self._stats.current_depth = 0

        result = b"".join(parts)
        if result:
            self._stats.total_out += len(result) // max(self._frame_size, 1)
        return result

    def reset(self) -> None:
        """清空缓冲区。"""
        self._buffer.clear()
        self._partial.clear()
        self._prefilled = False
        self._stats.current_depth = 0

    @property
    def is_draining(self) -> bool:
        """是否已过预填充阶段，正在正常输出。"""
        return self._prefilled


class TTSOutputBuffer:
    """TTS 输出侧 Jitter Buffer — 将 TTS 突发音频按固定帧率匀速发送给 FreeSWITCH。

    TTS 按句合成，每句完成后产生一整段 PCM。如果不做缓冲直接发送：
    - 句 N 的全部 PCM 瞬间发完 → FreeSWITCH 播放
    - 等待句 N+1 合成 → FreeSWITCH 播放间隙静音
    - 句 N+1 完成 → 再次突发全部 PCM

    TTSOutputBuffer 将 PCM 拆帧后以 30ms 间隔匀速输出，消除突发-静默交替，
    同时保持句子间音频的连续性（前句末尾帧和后句首帧间无额外间隔）。
    """

    def __init__(
        self,
        send_fn: "Callable[[bytes], Awaitable[None]]",
        frame_size: int = FRAME_BYTES,
        frame_interval: float = FRAME_DURATION_MS / 1000.0,
    ) -> None:
        """
        Args:
            send_fn: async 回调，每帧调用一次 (websocket.send_bytes)
            frame_size: 单帧字节数 (默认 480 = 30ms @ 8kHz 16-bit)
            frame_interval: 帧发送间隔秒数 (默认 0.03 = 30ms)
        """
        self._send_fn = send_fn
        self._frame_size = frame_size
        self._frame_interval = frame_interval
        self._buffer: deque[bytes] = deque()
        self._partial: bytearray = bytearray()
        self._task: asyncio.Task | None = None
        self._cancel = asyncio.Event()
        self._data_ready = asyncio.Event()
        self._finished = False

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def write(self, pcm: bytes) -> None:
        """写入 PCM 数据（拆帧入队，由发送任务匀速输出）。"""
        if self._task is not None and self._task.done():
            return  # 发送循环已退出，丢弃数据
        self._partial.extend(pcm)
        while len(self._partial) >= self._frame_size:
            frame = bytes(self._partial[:self._frame_size])
            self._partial = self._partial[self._frame_size:]
            self._buffer.append(frame)
        self._data_ready.set()

    def finish(self) -> None:
        """标记写入完成（残余数据入队，发送任务将在排空后自动停止）。"""
        if self._partial:
            self._buffer.append(bytes(self._partial))
            self._partial.clear()
            self._data_ready.set()
        self._finished = True

    async def start(self) -> None:
        """启动匀速发送任务。"""
        if self._task is not None:
            return
        self._cancel.clear()
        self._task = asyncio.create_task(self._send_loop(), name="tts-output-buffer")

    async def stop(self) -> None:
        """停止发送任务。"""
        self._cancel.set()
        self._data_ready.set()  # 唤醒等待
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._buffer.clear()
        self._partial.clear()
        self._finished = False

    async def wait_drained(self, timeout: float = 5.0) -> None:
        """等待所有缓冲帧发送完毕。"""
        if self._task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("TTSOutputBuffer drain timeout, forcing stop")
            await self.stop()

    async def _send_loop(self) -> None:
        """匀速发送循环：以 frame_interval 间隔逐帧调用 send_fn。

        无数据时等待 _data_ready 事件唤醒，不发静音帧。
        FreeSWITCH mod_audio_fork 的 dub_speech_frame 在 playout buffer 空时
        自动向通话方发送静音，无需 agent-flow 侧填充。
        """
        frames_sent = 0
        try:
            while not self._cancel.is_set():
                if self._buffer:
                    frame = self._buffer.popleft()
                    try:
                        await self._send_fn(frame)
                    except Exception as e:
                        logger.error(
                            "TTSOutputBuffer send error (type=%s, repr=%r, frames_sent=%d): %s",
                            type(e).__name__, e, frames_sent, e,
                        )
                        return
                    frames_sent += 1
                    await asyncio.sleep(self._frame_interval)
                elif self._finished:
                    logger.info("TTSOutputBuffer drained: %d audio frames sent", frames_sent)
                    return
                else:
                    # 等待数据写入，stop() 也会 set 此事件以唤醒退出
                    self._data_ready.clear()
                    await self._data_ready.wait()
        except asyncio.CancelledError:
            logger.info("TTSOutputBuffer cancelled: %d audio frames sent", frames_sent)
