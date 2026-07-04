"""RMS + SNR 自适应门禁 — barge-in 低延迟语音检测。

从 WebRTCVAD._has_speech_energy + _update_noise_floor 抽出(去 WebRTC 频谱判定):
- is_speech(frame):RMS + SNR 自适应门限判语音;返回 False(非语音)时内部 EMA 更新底噪
- reset():清语音状态但保留底噪(一通通话内环境底噪稳定,跨轮复用)

门限两种模式:
- 自适应 SNR(snr_factor > 0):门限 = max(noise_floor * snr_factor, threshold)
- 固定(snr_factor <= 0):门限 = threshold(threshold<=0 时不过滤)
"""
import numpy as np

# 16kHz 16-bit mono PCM: 30ms frame = 960 bytes
SAMPLE_RATE = 16000
FRAME_DURATION_MS = 30
FRAME_BYTES = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000) * 2  # 960


class RMSGate:
    """RMS + SNR 自适应语音门禁。线程不安全 —— 单通话独占。"""

    def __init__(
        self,
        threshold: float = 300.0,
        snr_factor: float = 3.0,
        noise_floor_init: float = 300.0,
        noise_adapt_rate: float = 0.1,
    ) -> None:
        self._threshold = threshold
        self._snr_factor = snr_factor
        self._noise_floor = noise_floor_init
        self._noise_adapt_rate = noise_adapt_rate

    def is_speech(self, frame: bytes) -> bool:
        """判单帧是否语音。非语音帧用 EMA 更新底噪(双向,随环境浮动)。"""
        if self._threshold <= 0 and self._snr_factor <= 0:
            return True
        _f32 = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(_f32 ** 2)))
        if self._snr_factor > 0:
            threshold = max(self._noise_floor * self._snr_factor, self._threshold)
            is_speech = rms > threshold
        else:
            is_speech = rms > self._threshold
        if not is_speech:
            self._update_noise_floor(rms)
        return is_speech

    def _update_noise_floor(self, rms: float) -> None:
        """非语音帧 EMA 平滑更新底噪(仅 snr_factor>0 时有意义)。"""
        if self._snr_factor <= 0:
            return
        a = self._noise_adapt_rate
        self._noise_floor = self._noise_floor * (1 - a) + rms * a

    def reset(self) -> None:
        """重置一轮状态;底噪不重置(跨轮复用,跨通话由新实例归零)。"""
        # 本类无语音累积状态(底噪跨轮复用),故 no-op;方法保留供 handler
        # _reset_audio_state 统一调用生命周期接口。
        return
