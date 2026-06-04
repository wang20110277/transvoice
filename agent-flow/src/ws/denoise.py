"""Audio denoising — configurable pre-VAD noise reduction.

Insertion point: after JitterBuffer.drain(), before VAD and audio_buffer.
All frames are 960 bytes (30ms @ 16kHz 16-bit mono).
"""

import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_BYTES = 960  # 30ms @ 16kHz 16-bit mono
SAMPLES_PER_FRAME = FRAME_BYTES // 2  # 480 samples


class BaseDenoiser(ABC):
    @abstractmethod
    def process(self, frame: bytes) -> bytes:
        """Process a single PCM frame. Input/output: 16-bit LE PCM bytes."""

    @abstractmethod
    def reset(self) -> None:
        """Reset internal state between turns."""


class PassThroughDenoiser(BaseDenoiser):
    def process(self, frame: bytes) -> bytes:
        return frame

    def reset(self) -> None:
        pass


class HighPassDenoiser(BaseDenoiser):
    """Butterworth high-pass filter removing low-frequency noise.

    Uses stateful IIR filter (scipy.signal.lfilter with zi) for
    continuity across frames — no boundary artifacts.
    """

    def __init__(self, cutoff: float = 200.0, order: int = 4) -> None:
        from scipy.signal import butter, lfilter_zi

        self._b, self._a = butter(order, cutoff, btype="high", fs=SAMPLE_RATE)
        self._zi = lfilter_zi(self._b, self._a) * 0.0

    def process(self, frame: bytes) -> bytes:
        import numpy as np
        from scipy.signal import lfilter

        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float64)
        filtered, self._zi = lfilter(self._b, self._a, samples, zi=self._zi)
        return np.clip(filtered, -32768, 32767).astype(np.int16).tobytes()

    def reset(self) -> None:
        from scipy.signal import lfilter_zi

        self._zi = lfilter_zi(self._b, self._a) * 0.0


class NoisereduceDenoiser(BaseDenoiser):
    """Spectral gating denoiser (noisereduce library).

    Buffers frames into ~300ms chunks for better FFT analysis.
    """

    def __init__(self, prop_decrease: float = 0.8) -> None:
        self._prop_decrease = prop_decrease
        self._buffer: list[bytes] = []
        self._pending: bytes = b""

    def process(self, frame: bytes) -> bytes:
        # Return any pending processed bytes first
        if self._pending and len(self._pending) >= FRAME_BYTES:
            out = self._pending[:FRAME_BYTES]
            self._pending = self._pending[FRAME_BYTES:]
            return out

        self._buffer.append(frame)
        if len(self._buffer) < 10:  # 300ms
            return frame

        self._process_buffer()

        if self._pending and len(self._pending) >= FRAME_BYTES:
            out = self._pending[:FRAME_BYTES]
            self._pending = self._pending[FRAME_BYTES:]
            return out
        return frame

    def _process_buffer(self) -> None:
        import numpy as np
        import noisereduce

        combined = b"".join(self._buffer)
        self._buffer.clear()

        samples = np.frombuffer(combined, dtype=np.int16).astype(np.float64)
        reduced = noisereduce.reduce_noise(
            y=samples, sr=SAMPLE_RATE,
            prop_decrease=self._prop_decrease,
            stationary=True,
        )
        self._pending = np.clip(reduced, -32768, 32767).astype(np.int16).tobytes()

    def reset(self) -> None:
        self._buffer.clear()
        self._pending = b""


class RNNoiseDenoiser(BaseDenoiser):
    """RNNoise denoiser — pyrnnoise with native 16kHz support.

    pyrnnoise handles internal resampling transparently.
    Input/output: 960 bytes (480 samples @ 16kHz 16-bit mono, 30ms frame).
    """

    def __init__(self) -> None:
        try:
            from pyrnnoise import RNNoise as _RNNoise
            self._denoiser = _RNNoise(16000)
            logger.info("Denoiser: RNNoise (pyrnnoise, 16kHz native)")
        except ImportError:
            logger.warning("pyrnnoise not installed, falling back to pass-through")
            self._denoiser = None

    def process(self, frame: bytes) -> bytes:
        if self._denoiser is None:
            return frame

        import numpy as np

        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0

        denoised_samples = []
        for _, denoised in self._denoiser.denoise_chunk(samples):
            denoised_samples.append(np.array(denoised).flatten())

        if not denoised_samples:
            return frame

        result = np.concatenate(denoised_samples)
        # Ensure output matches input length exactly
        if len(result) != len(samples):
            result = result[:len(samples)]

        return np.clip(result * 32768.0, -32768, 32767).astype(np.int16).tobytes()

    def reset(self) -> None:
        # Re-create to clear internal state
        if self._denoiser is not None:
            from pyrnnoise import RNNoise as _RNNoise
            self._denoiser = _RNNoise(16000)


def create_denoiser() -> BaseDenoiser:
    """Factory: create denoiser based on CALLBOT_DENOISE_ENABLED env var."""
    enabled = os.environ.get("CALLBOT_DENOISE_ENABLED", "").lower()

    if enabled in ("", "0", "false", "off", "none"):
        return PassThroughDenoiser()

    if enabled == "highpass":
        cutoff = float(os.environ.get("CALLBOT_DENOISE_HIGHPASS_CUTOFF", "200"))
        logger.info("Denoiser: high-pass filter at %dHz", cutoff)
        return HighPassDenoiser(cutoff=cutoff)

    if enabled == "noisereduce":
        logger.info("Denoiser: spectral gating (noisereduce)")
        return NoisereduceDenoiser()

    if enabled == "rnnoise":
        logger.info("Denoiser: RNNoise (8kHz→48kHz resampling)")
        return RNNoiseDenoiser()

    logger.warning("Unknown denoiser type '%s', using pass-through", enabled)
    return PassThroughDenoiser()
