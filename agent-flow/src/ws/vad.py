"""VAD 语音端点检测 — 可插拔引擎（WebRTC / Silero）"""
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from config import Settings

logger = logging.getLogger(__name__)

# 16kHz 16-bit mono PCM: 30ms frame = 960 bytes
FRAME_DURATION_MS = 30
SAMPLE_RATE = 16000
FRAME_BYTES = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000) * 2  # 960 bytes


class BaseVAD(ABC):
    """VAD 引擎抽象基类。"""

    @property
    @abstractmethod
    def speech_detected(self) -> bool:
        """VAD 是否已确认检测到语音（连续 N 帧语音后置 True，reset 后置 False）。"""

    @abstractmethod
    def is_speech(self, frame: bytes) -> bool:
        """判断单个完整帧是否为语音。"""

    @abstractmethod
    def is_end_of_speech(self, chunk: bytes, buffer_len: int) -> bool:
        """处理音频块，返回是否到达语音终点。

        chunk 可以是任意长度，内部按 FRAME_BYTES 拆帧处理。
        buffer_len 为总累积音频量，用于最小长度门槛。
        """

    @abstractmethod
    def reset(self) -> None:
        """重置内部状态（新一轮对话开始时调用）。"""


class WebRTCVAD(BaseVAD):
    """基于 WebRTC VAD 的端点检测。

    处理 FreeSWITCH mod_audio_fork 发来的 PCM 流:
    - 累积音频到固定帧长 (30ms / 960 bytes @ 16kHz)
    - 每帧送入 WebRTC VAD 判断是否为语音
    - 连续 silent_frames 帧非语音时判定为静音结束
    """

    # 最少连续语音帧 — 过滤 < 90ms 的短噪声（呼吸声、环境音）
    _MIN_SPEECH_FRAMES = 3

    def __init__(
        self,
        aggressiveness: int = 3,
        silence_frames: int = 15,
        min_audio_bytes: int = 3200,
        rms_threshold: float = 0.0,
        noise_floor_init: float = 0.0,
        snr_factor: float = 0.0,
        noise_adapt_rate: float = 0.1,
    ) -> None:
        import webrtcvad

        self._vad = webrtcvad.Vad(aggressiveness)
        self._silence_frames = silence_frames
        self._min_audio_bytes = min_audio_bytes
        self._rms_threshold = rms_threshold
        # 自适应噪声底噪：门限随环境底噪浮动，解决固定 RMS 门限在嘈杂环境失效
        # （底噪 RMS 本身就 > 300，门限形同虚设）的问题。详见 _has_speech_energy。
        self._noise_floor = noise_floor_init
        self._snr_factor = snr_factor
        self._noise_adapt_rate = noise_adapt_rate
        self._silent_count = 0
        self._speech_count = 0
        self._speech_detected = False
        self._frame_buffer = bytearray()

    @property
    def speech_detected(self) -> bool:
        return self._speech_detected

    def reset(self) -> None:
        self._silent_count = 0
        self._speech_count = 0
        self._speech_detected = False
        self._frame_buffer.clear()
        # noise_floor 不重置：一通通话内环境底噪相对稳定，跨轮次复用估计更准；
        # 跨通话由 create_vad() 重建实例自然归零（避免每轮 warm-up 误判开头帧）

    def is_speech(self, frame: bytes) -> bool:
        if len(frame) != FRAME_BYTES:
            return False
        try:
            return self._vad.is_speech(frame, SAMPLE_RATE)
        except Exception:
            return False

    def _has_speech_energy(self, frame: bytes) -> bool:
        """能量门控：区分语音与噪声。

        两种模式：
        - 自适应 SNR（snr_factor > 0）：门限 = noise_floor * snr_factor。非语音帧用
          EMA 更新 noise_floor，使门限随环境底噪自适应——安静时低（人声轻松通过），
          嘈杂时抬高（要求明显超出底噪才判语音），避免固定门限在强噪声下失效。
          rms_threshold 作为门限下限，保留对极低能量帧（纯静音/SIP 底噪）的过滤。
        - 固定门限（snr_factor <= 0）：rms_threshold <= 0 时不做能量过滤（纯 WebRTC），
          否则帧 RMS 须 > rms_threshold。向后兼容旧行为。
        """
        if self._rms_threshold <= 0 and self._snr_factor <= 0:
            return True
        _f32 = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(_f32**2)))
        if self._snr_factor > 0:
            # SNR 门限不低于 rms_threshold（默认 300），保留底噪过滤兜底
            threshold = max(self._noise_floor * self._snr_factor, self._rms_threshold)
            return rms > threshold
        return rms > self._rms_threshold

    def _update_noise_floor(self, frame: bytes) -> None:
        """非语音帧用 EMA 平滑更新噪声底噪估计。

        仅在 is_end_of_speech 判定为非语音（WebRTC + 能量都不认为是语音）的帧上调用，
        避免语音帧拉高底噪。双向 EMA：环境变安静时门限随之下降，变嘈杂时上升。
        """
        if self._snr_factor <= 0:
            return
        _f32 = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(_f32**2)))
        a = self._noise_adapt_rate
        self._noise_floor = self._noise_floor * (1 - a) + rms * a

    def is_end_of_speech(self, chunk: bytes, buffer_len: int) -> bool:
        self._frame_buffer.extend(chunk)

        while len(self._frame_buffer) >= FRAME_BYTES:
            frame = bytes(self._frame_buffer[:FRAME_BYTES])
            self._frame_buffer = self._frame_buffer[FRAME_BYTES:]

            webrtc_speech = self.is_speech(frame)
            energy_speech = self._has_speech_energy(frame)
            if webrtc_speech and energy_speech:
                self._silent_count = 0
                self._speech_count += 1
                if self._speech_count >= self._MIN_SPEECH_FRAMES:
                    self._speech_detected = True
            else:
                self._silent_count += 1
                if self._speech_count < self._MIN_SPEECH_FRAMES:
                    self._speech_count = 0
                # 两个判定都为否 → 干净噪声帧，用于估计底噪
                if not webrtc_speech and not energy_speech:
                    self._update_noise_floor(frame)

        return (
            self._speech_detected
            and self._silent_count >= self._silence_frames
            and buffer_len >= self._min_audio_bytes
        )


class SileroVAD(BaseVAD):
    """基于 Silero VAD 的端点检测（神经网络，精度高于 WebRTC）。

    VADIterator 内部追踪语音起止状态机：
    - speech 事件 → 标记检测到语音
    - silence 事件 → 超过 min_silence_duration_ms 静音后触发，判定终点
    """

    # Silero VAD 要求最小 512 samples (32ms @ 16kHz)，大于标准帧 480 samples (30ms)
    _SILERO_MIN_SAMPLES = 512
    _SILERO_MIN_BYTES = _SILERO_MIN_SAMPLES * 2  # 1024 bytes

    def __init__(
        self,
        threshold: float = 0.5,
        min_silence_duration_ms: int = 200,
        min_audio_bytes: int = 3200,
    ) -> None:
        from silero_vad import load_silero_vad, VADIterator

        self._model = load_silero_vad()
        self._threshold = threshold
        self._min_audio_bytes = min_audio_bytes
        self._vad_iterator = VADIterator(
            self._model,
            threshold=threshold,
            sampling_rate=SAMPLE_RATE,
            min_silence_duration_ms=min_silence_duration_ms,
        )
        self._speech_detected = False
        self._silence_detected = False
        self._frame_buffer = bytearray()
        logger.info("SileroVAD initialized: threshold=%.2f silence_ms=%d", threshold, min_silence_duration_ms)

    @property
    def speech_detected(self) -> bool:
        return self._speech_detected

    @staticmethod
    def _int2float(data: bytes) -> np.ndarray:
        return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0

    def is_speech(self, frame: bytes) -> bool:
        """逐帧语音检测 — 直接调用模型获取概率，不经过 VADIterator。"""
        if len(frame) < FRAME_BYTES:
            return False
        try:
            import torch
            # 取前 _SILERO_MIN_SAMPLES 个采样点（>= 512），不足则补零
            audio_float32 = self._int2float(frame[:self._SILERO_MIN_BYTES])
            if len(audio_float32) < self._SILERO_MIN_SAMPLES:
                audio_float32 = np.pad(audio_float32, (0, self._SILERO_MIN_SAMPLES - len(audio_float32)))
            prob = self._model(torch.from_numpy(audio_float32), SAMPLE_RATE).item()
            return prob >= self._threshold
        except Exception:
            return False

    def is_end_of_speech(self, chunk: bytes, buffer_len: int) -> bool:
        """通过 VADIterator 状态机检测语音终点。"""
        import torch

        self._frame_buffer.extend(chunk)

        while len(self._frame_buffer) >= self._SILERO_MIN_BYTES:
            frame = bytes(self._frame_buffer[:self._SILERO_MIN_BYTES])
            self._frame_buffer = self._frame_buffer[self._SILERO_MIN_BYTES:]

            audio_float32 = self._int2float(frame)
            x = torch.from_numpy(audio_float32)
            # 调试：输出原始概率
            prob = self._model(x, SAMPLE_RATE).item()
            self._prob_count = getattr(self, '_prob_count', 0) + 1
            if self._prob_count <= 5 or (self._prob_count % 50 == 0):
                rms = float(np.sqrt(np.mean(np.frombuffer(frame, dtype=np.int16).astype(np.float32)**2)))
                logger.info("SileroVAD debug: prob=%.3f rms=%.0f chunk=%d (#%d)", prob, rms, len(frame), self._prob_count)
            result = self._vad_iterator(x)

            if result is not None:
                logger.debug("SileroVAD event: %s", result)
                if "speech" in result:
                    self._speech_detected = True
                    self._silence_detected = False
                    logger.info("SileroVAD: speech detected")
                elif "silence" in result:
                    self._silence_detected = True
                    logger.info("SileroVAD: silence detected (end of speech)")

        return (
            self._speech_detected
            and self._silence_detected
            and buffer_len >= self._min_audio_bytes
        )

    def reset(self) -> None:
        self._speech_detected = False
        self._silence_detected = False
        self._frame_buffer.clear()
        self._vad_iterator.reset_states()


# Backward-compatible alias
SimpleVAD = WebRTCVAD


def create_vad(settings: "Settings") -> BaseVAD:
    """工厂：根据 settings.vad_type 创建 VAD 实例。

    每次调用返回新实例，用于每通电话独立的 VAD 状态。
    """
    vad_type = settings.vad_type.lower()

    if vad_type == "silero":
        return SileroVAD(
            threshold=settings.vad_silero_threshold,
            min_silence_duration_ms=settings.vad_silero_min_silence_ms,
            min_audio_bytes=settings.vad_min_audio_bytes,
        )

    if vad_type == "webrtc":
        return WebRTCVAD(
            aggressiveness=settings.vad_aggressiveness,
            silence_frames=settings.vad_silence_frames,
            min_audio_bytes=settings.vad_min_audio_bytes,
            rms_threshold=settings.vad_rms_threshold,
            noise_floor_init=settings.vad_noise_floor_init,
            snr_factor=settings.vad_snr_factor,
            noise_adapt_rate=settings.vad_noise_adapt_rate,
        )

    logger.warning("Unknown VAD type '%s', falling back to webrtc", vad_type)
    return WebRTCVAD(
        aggressiveness=settings.vad_aggressiveness,
        silence_frames=settings.vad_silence_frames,
        min_audio_bytes=settings.vad_min_audio_bytes,
        rms_threshold=settings.vad_rms_threshold,
        noise_floor_init=settings.vad_noise_floor_init,
        snr_factor=settings.vad_snr_factor,
        noise_adapt_rate=settings.vad_noise_adapt_rate,
    )
