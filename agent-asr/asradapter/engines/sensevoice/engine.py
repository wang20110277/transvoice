import asyncio
import logging
import os
import re
import struct
import tempfile
import uuid

from asradapter.base import ASREngine, ASRResult

logger = logging.getLogger(__name__)

MODEL_DIR = os.environ.get("MODEL_DIR", "/opt/sensevoice/models/SenseVoiceSmall")
SENSEVOICE_LANGUAGE = os.environ.get("SENSEVOICE_LANGUAGE", "zh")
SENSEVOICE_MAX_CONCURRENT = int(os.environ.get("SENSEVOICE_MAX_CONCURRENT", "50"))


def _pcm_to_wav(pcm: bytes, sample_rate: int = 16000, channels: int = 1, bits: int = 16) -> bytes:
    """Wrap raw PCM bytes in a WAV header."""
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_size = len(pcm)
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + data_size, b'WAVE',
        b'fmt ', 16, 1, channels, sample_rate, byte_rate, block_align, bits,
        b'data', data_size,
    )
    return header + pcm


class SenseVoiceASREngine(ASREngine):
    def __init__(self):
        self._language = SENSEVOICE_LANGUAGE
        self._semaphore = asyncio.Semaphore(SENSEVOICE_MAX_CONCURRENT)
        self._model = None

    async def load_model(self):
        from funasr import AutoModel
        logger.info("Loading SenseVoice model from %s", MODEL_DIR)
        self._model = AutoModel(model=MODEL_DIR, disable_update=True)
        logger.info("SenseVoice model loaded")

    async def recognize(self, audio_stream: bytes, params: dict) -> ASRResult:
        if self._model is None:
            raise RuntimeError("SenseVoice model not loaded")

        async with self._semaphore:
            tmp_path = os.path.join(tempfile.gettempdir(), f"asr_{uuid.uuid4().hex}.wav")
            try:
                # If input is raw PCM (no RIFF header), wrap it in WAV format
                wav_data = audio_stream
                if len(audio_stream) < 4 or audio_stream[:4] != b'RIFF':
                    sample_rate = params.get("sample_rate", 16000)
                    wav_data = _pcm_to_wav(audio_stream, sample_rate=sample_rate)

                with open(tmp_path, "wb") as f:
                    f.write(wav_data)

                import torch
                with torch.no_grad():
                    result = self._model.generate(
                        input=tmp_path,
                        language=params.get("language", self._language),
                        batch_size_s=300,
                    )
                # MPS cache cleanup to prevent progressive slowdown
                if torch.backends.mps.is_available():
                    torch.mps.empty_cache()
                text = result[0]["text"] if result else ""
                # Strip SenseVoice special tokens: <|zh|>, <|EMO_UNKNOWN|>, <|Speech|>, <|woitn|>, etc.
                text = re.sub(r'<\|[^|]*\|>', '', text).strip()
                return ASRResult(text=text, confidence=0.95, is_final=True)
            except Exception as e:
                logger.error("SenseVoice recognition failed: %s", e)
                raise RuntimeError(f"SenseVoice recognition failed: {e}") from e
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

    async def health_check(self) -> bool:
        return self._model is not None


Engine = SenseVoiceASREngine
