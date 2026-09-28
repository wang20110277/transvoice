"""Jitter Buffer — 抖动缓冲区，平滑网络音频包时序差异

FreeSWITCH mod_audio_fork 通过 WebSocket 发送音频帧，网络抖动会导致帧间隔不均匀。
Jitter Buffer 累积一定量的帧后以稳定间隔输出，保证 VAD 和 ASR 收到连续均匀的音频流。

参考: 基于WebSocket与软交换构建实时AI语音助手全链路优化 — Jitter Buffer 章节
"""
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
