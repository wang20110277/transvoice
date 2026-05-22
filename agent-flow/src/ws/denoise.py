"""Audio denoising — configurable pre-VAD noise reduction.

Insertion point: after JitterBuffer.drain(), before VAD and audio_buffer.
All frames are 480 bytes (30ms @ 8kHz 16-bit mono).
"""

import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

SAMPLE_RATE = 8000
FRAME_BYTES = 480  # 30ms @ 8kHz 16-bit mono
SAMPLES_PER_FRAME = FRAME_BYTES // 2  # 240 samples


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
    """RNNoise denoiser with 8kHz→48kHz resampling.

    RNNoise operates at 48kHz with 480-sample (20ms) frames.
    We resample each 240-sample (30ms @ 8kHz) frame up, denoise, then back down.
    """

    def __init__(self) -> None:
        import numpy as np
        from scipy.signal import butter, sosfilt, resample

        self._np = np
        self._resample = resample

        # RNNoise expects 480 samples at 48kHz = 10ms
        # We feed it 240 samples upsampled to 1440 samples at 48kHz (30ms)
        # then split into 3 x 480-sample chunks for RNNoise
        try:
            from rnnoise import RNNoise as _RNNoise
            self._denoiser = _RNNoise()
        except ImportError:
            logger.warning("rnnoise-python not installed, falling back to pass-through")
            self._denoiser = None

    def process(self, frame: bytes) -> bytes:
        if self._denoiser is None:
            return frame

        np = self._np
        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)

        # 8kHz → 48kHz: 240 samples → 1440 samples
        upsampled = self._resample(samples, int(len(samples) * 6))
        float_pcm = upsampled / 32768.0

        # Process in 480-sample chunks (RNNoise frame size at 48kHz)
        denoised_chunks = []
        for i in range(0, len(float_pcm), 480):
            chunk = float_pcm[i : i + 480]
            if len(chunk) < 480:
                chunk = np.pad(chunk, (0, 480 - len(chunk)))
            denoised, _ = self._denoiser.process(chunk)
            denoised_chunks.append(denoised)

        denoised_full = np.concatenate(denoised_chunks)

        # 48kHz → 8kHz: 1440 samples → 240 samples
        downsampled = self._resample(denoised_full, len(samples))
        return np.clip(downsampled * 32768.0, -32768, 32767).astype(np.int16).tobytes()

    def reset(self) -> None:
        pass


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
