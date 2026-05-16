"""简易 VAD — 基于 RMS 能量的端点检测"""
import math
import struct


def compute_rms(audio_bytes: bytes, sample_width: int = 2) -> float:
    """计算 PCM 音频的 RMS 能量。"""
    if not audio_bytes or len(audio_bytes) < sample_width:
        return 0.0
    n_samples = len(audio_bytes) // sample_width
    if sample_width == 2:
        fmt = f"<{n_samples}h"
    else:
        fmt = f"<{n_samples}b"
    try:
        samples = struct.unpack(fmt, audio_bytes[: n_samples * sample_width])
    except struct.error:
        return 0.0
    sum_sq = sum(s * s for s in samples)
    return math.sqrt(sum_sq / n_samples) if n_samples else 0.0


class SimpleVAD:
    """基于能量的简单 VAD。

    连续 silent_frames 帧低于阈值时判定为静音结束。
    """

    def __init__(
        self,
        silence_threshold: float = 500.0,
        silence_frames: int = 15,
        min_audio_bytes: int = 3200,
    ) -> None:
        self.silence_threshold = silence_threshold
        self.silence_frames = silence_frames
        self.min_audio_bytes = min_audio_bytes
        self._silent_count = 0
        self._speech_detected = False

    def reset(self) -> None:
        self._silent_count = 0
        self._speech_detected = False

    def is_speech(self, frame: bytes) -> bool:
        """判断当前帧是否为语音。"""
        rms = compute_rms(frame)
        if rms >= self.silence_threshold:
            self._silent_count = 0
            self._speech_detected = True
            return True
        else:
            self._silent_count += 1
            return False

    def is_end_of_speech(self, frame: bytes, buffer_len: int) -> bool:
        """判断是否到达语音终点（静音超时 + 已检测到语音 + 缓冲区足够长）。"""
        self.is_speech(frame)
        return (
            self._speech_detected
            and self._silent_count >= self.silence_frames
            and buffer_len >= self.min_audio_bytes
        )
