"""WebRTC VAD — 基于 WebRTC 的语音端点检测"""
import logging
import webrtcvad

logger = logging.getLogger(__name__)

# 8kHz 16-bit mono PCM: 30ms frame = 480 bytes, 20ms = 320 bytes, 10ms = 160 bytes
FRAME_DURATION_MS = 30
SAMPLE_RATE = 8000
FRAME_BYTES = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000) * 2  # 480 bytes


class WebRTCVAD:
    """基于 WebRTC VAD 的端点检测。

    处理 FreeSWITCH mod_audio_fork 发来的 PCM 流:
    - 累积音频到固定帧长 (30ms / 480 bytes @ 8kHz)
    - 每帧送入 WebRTC VAD 判断是否为语音
    - 连续 silent_frames 帧非语音时判定为静音结束
    """

    def __init__(
        self,
        aggressiveness: int = 3,
        silence_frames: int = 15,
        min_audio_bytes: int = 3200,
    ) -> None:
        """Args:
            aggressiveness: VAD 灵敏度 0-3 (0=最灵敏, 3=最激进过滤)
            silence_frames: 连续多少帧非语音判定为静音
            min_audio_bytes: 最少累积音频量才允许判定 end-of-speech
        """
        self._vad = webrtcvad.Vad(aggressiveness)
        self._silence_frames = silence_frames
        self._min_audio_bytes = min_audio_bytes
        self._silent_count = 0
        self._speech_detected = False
        self._frame_buffer = bytearray()

    def reset(self) -> None:
        self._silent_count = 0
        self._speech_detected = False
        self._frame_buffer.clear()

    def is_speech(self, frame: bytes) -> bool:
        """判断一个完整帧是否为语音。帧长度必须等于 FRAME_BYTES。"""
        if len(frame) != FRAME_BYTES:
            return False
        try:
            return self._vad.is_speech(frame, SAMPLE_RATE)
        except Exception:
            return False

    def is_end_of_speech(self, chunk: bytes, buffer_len: int) -> bool:
        """处理一个音频块，返回是否到达语音终点。

        chunk 可以是任意长度，内部按 FRAME_BYTES 拆帧处理。
        buffer_len 为总累积音频量，用于最小长度门槛。
        """
        self._frame_buffer.extend(chunk)

        # 按帧处理
        while len(self._frame_buffer) >= FRAME_BYTES:
            frame = bytes(self._frame_buffer[:FRAME_BYTES])
            self._frame_buffer = self._frame_buffer[FRAME_BYTES:]

            if self.is_speech(frame):
                self._silent_count = 0
                self._speech_detected = True
            else:
                self._silent_count += 1

        return (
            self._speech_detected
            and self._silent_count >= self._silence_frames
            and buffer_len >= self._min_audio_bytes
        )


# Backward-compatible alias
SimpleVAD = WebRTCVAD
