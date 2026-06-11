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
SILENCE_FRAME = b'\x00' * FRAME_BYTES


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

    静音填充策略：write() 后 silence_timeout 秒内，buffer 空时发送静音帧
    保持流连续。超时后停止静音，避免回合间持续发送导致回声累积触发误 barge-in。
    """

    # write() 后静音填充持续时长
    # 需覆盖打断 → ASR → LLM → 首句 TTS 的全链路延迟（可达 45s）
    # 静音帧 RMS=0 不会触发 barge-in（阈值 300），可安全使用较长超时
    _SILENCE_TIMEOUT = 120.0

    def __init__(
        self,
        send_fn: "Callable[[bytes], Awaitable[None]]",
        frame_size: int = FRAME_BYTES,
        frame_interval: float = FRAME_DURATION_MS / 1000.0,
        prebuffer_frames: int = 0,
    ) -> None:
        """
        Args:
            send_fn: async 回调，每帧调用一次 (websocket.send_bytes)
            frame_size: 单帧字节数 (默认 960 = 30ms @ 16kHz 16-bit)
            frame_interval: 帧发送间隔秒数 (默认 0.03 = 30ms)
            prebuffer_frames: 预缓冲帧数，累积到阈值后才开始匀速发送
                              0 = 不预缓冲（立即发送），10 = 300ms 延迟换取平滑
        """
        self._send_fn = send_fn
        self._frame_size = frame_size
        self._frame_interval = frame_interval
        self._prebuffer_frames = prebuffer_frames
        self._prebuffering = prebuffer_frames > 0
        self._prebuffer_done = asyncio.Event()
        self._buffer: deque[bytes] = deque()
        self._partial: bytearray = bytearray()
        self._task: asyncio.Task | None = None
        self._cancel = asyncio.Event()
        self._data_ready = asyncio.Event()
        self._finished = False
        self._last_write_time: float = 0.0

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def write(self, pcm: bytes) -> None:
        """写入 PCM 数据（拆帧入队，由发送任务匀速输出）。"""
        if self._task is not None and self._task.done():
            return  # 发送循环已退出，丢弃数据
        self._last_write_time = time.monotonic()
        self._partial.extend(pcm)
        while len(self._partial) >= self._frame_size:
            frame = bytes(self._partial[:self._frame_size])
            self._partial = self._partial[self._frame_size:]
            self._buffer.append(frame)
        # 预缓冲阈值达到 → 唤醒 _send_loop 开始播放
        if self._prebuffering and len(self._buffer) >= self._prebuffer_frames:
            self._prebuffering = False
            self._prebuffer_done.set()
        self._data_ready.set()

    def clear(self) -> None:
        """清空缓冲区，丢弃所有待发送的音频帧。_send_loop 继续运行，后续会自动填充静音帧。"""
        self._buffer.clear()
        self._partial.clear()

    def finish(self) -> None:
        """标记写入完成（残余数据入队，发送任务将在排空后自动停止）。"""
        if self._partial:
            self._buffer.append(bytes(self._partial))
            self._partial.clear()
        self._finished = True
        # 预缓冲未满但已结束 → 立即开始播放已累积的帧
        if self._prebuffering:
            self._prebuffering = False
            self._prebuffer_done.set()
        self._data_ready.set()

    async def start(self) -> None:
        """启动匀速发送任务。"""
        if self._task is not None:
            return
        self._cancel.clear()
        self._task = asyncio.create_task(self._send_loop(), name="tts-output-buffer")

    async def stop(self) -> None:
        """停止发送任务。"""
        self._cancel.set()
        self._prebuffering = False
        self._prebuffer_done.set()
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

        预缓冲阶段: 累积到 prebuffer_frames 后才开始发送。
        无 TTS 数据时发送静音帧，保持音频流连续，防止 FreeSWITCH playout buffer
        在句间间隙耗尽导致可听到的间断。静音帧与 TTS 帧以相同 30ms 间隔发送，
        确保句 N 末尾与句 N+1 开头之间无缝衔接。
        """
        frames_sent = 0
        silence_sent = 0
        try:
            # 预缓冲阶段: 等待累积足够帧数
            if self._prebuffer_frames > 0:
                while self._prebuffering and not self._cancel.is_set():
                    self._prebuffer_done.clear()
                    await self._prebuffer_done.wait()
                    break
                if self._cancel.is_set():
                    return
                buf_count = len(self._buffer)
                logger.info(
                    "TTSOutputBuffer pre-buffered %d frames (%dms), starting playback",
                    buf_count, buf_count * FRAME_DURATION_MS,
                )

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
                    logger.info(
                        "TTSOutputBuffer drained: %d audio frames, %d silence frames",
                        frames_sent, silence_sent,
                    )
                    return
                else:
                    # Buffer 空但 TTS 未结束 — 判断是否应发静音帧
                    elapsed = time.monotonic() - self._last_write_time
                    within_silence_window = (
                        self._last_write_time > 0
                        and elapsed < self._SILENCE_TIMEOUT
                    )
                    if within_silence_window:
                        # 句间间隙：发静音帧保持音频流连续
                        self._data_ready.clear()
                        try:
                            await asyncio.wait_for(
                                self._data_ready.wait(),
                                timeout=self._frame_interval,
                            )
                        except asyncio.TimeoutError:
                            pass
                        if not self._buffer and not self._cancel.is_set() and not self._finished:
                            try:
                                await self._send_fn(SILENCE_FRAME)
                            except Exception as e:
                                logger.error("TTSOutputBuffer silence send error: %s", e)
                                return
                            silence_sent += 1
                    else:
                        # 回合间：无 TTS 数据超过超时阈值，停止静音填充
                        # 避免持续发送导致 FreeSWITCH 音频路径活跃、回声触发误 barge-in
                        self._data_ready.clear()
                        await self._data_ready.wait()
        except asyncio.CancelledError:
            logger.info(
                "TTSOutputBuffer cancelled: %d audio frames, %d silence frames",
                frames_sent, silence_sent,
            )
