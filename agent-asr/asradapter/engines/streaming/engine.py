"""流式 ASR 引擎 — FunASR paraformer-zh-streaming，边听边转。"""
import asyncio
import logging
import os
import struct
import tempfile
import uuid

import numpy as np

from asradapter.base import (
    ASREngine,
    ASRResult,
    ASRStreamContext,
    StreamingASRResult,
)

logger = logging.getLogger(__name__)

MODEL_DIR = os.environ.get(
    "STREAMING_ASR_MODEL_DIR",
    "/opt/streaming-asr/models/paraformer-zh-streaming",
)

# chunk_size = [0, 10, 5] → 600ms per chunk
# chunk_size[1] * 960 samples = 9600 samples = 600ms @ 16kHz
CHUNK_SIZE = [0, 10, 5]
CHUNK_STRIDE = CHUNK_SIZE[1] * 960  # samples per chunk

ENCODER_CHUNK_LOOK_BACK = int(os.environ.get("STREAMING_ASR_ENCODER_LOOK_BACK", "4"))
DECODER_CHUNK_LOOK_BACK = int(os.environ.get("STREAMING_ASR_DECODER_LOOK_BACK", "1"))
MAX_CONCURRENT = int(os.environ.get("STREAMING_ASR_MAX_CONCURRENT", "50"))
SAMPLE_RATE = 16000


class FunASRStreamContext(ASRStreamContext):
    """FunASR 流式识别会话 — 使用 cache 跨 chunk 状态保持，边听边转。"""

    def __init__(self, model, language: str, semaphore: asyncio.Semaphore):
        self._model = model
        self._language = language
        self._semaphore = semaphore
        self._cache: dict = {}
        self._sample_buffer: np.ndarray = np.array([], dtype=np.float64)
        self._chunk_index = 0
        self._partial_text = ""
        self._started = False
        self._loop = asyncio.get_event_loop()

    async def start(self) -> None:
        self._started = True
        self._cache = {}
        self._sample_buffer = np.array([], dtype=np.float64)
        self._chunk_index = 0
        self._partial_text = ""
        logger.debug("[StreamingASR] session started")

    def send_audio(self, chunk: bytes) -> None:
        if not self._started:
            return
        # PCM int16 bytes → numpy float64
        samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float64) / 32768.0
        self._sample_buffer = np.concatenate([self._sample_buffer, samples])

    async def get_partial(self) -> StreamingASRResult | None:
        if len(self._sample_buffer) < CHUNK_STRIDE:
            return None

        speech_chunk = self._sample_buffer[:CHUNK_STRIDE]
        self._sample_buffer = self._sample_buffer[CHUNK_STRIDE:]
        self._chunk_index += 1

        try:
            text = await self._loop.run_in_executor(
                None, self._inference_chunk, speech_chunk, False,
            )
        except Exception as e:
            logger.warning("[StreamingASR] partial inference failed: %s", e)
            return None

        if text:
            self._partial_text = text
            return StreamingASRResult(
                text=text,
                confidence=0.9,
                is_final=False,
                is_partial=True,
                stability=min(0.3 + self._chunk_index * 0.1, 0.9),
            )
        return None

    async def finish(self) -> ASRResult:
        text = self._partial_text
        if len(self._sample_buffer) > 0:
            speech_chunk = self._sample_buffer
            self._sample_buffer = np.array([], dtype=np.float64)
            try:
                final_text = await self._loop.run_in_executor(
                    None, self._inference_chunk, speech_chunk, True,
                )
                if final_text:
                    text = final_text
            except Exception as e:
                logger.warning("[StreamingASR] final inference failed: %s", e)
        elif self._cache:
            # Flush with empty + is_final to get last tokens
            try:
                final_text = await self._loop.run_in_executor(
                    None, self._inference_chunk, np.array([], dtype=np.float64), True,
                )
                if final_text:
                    text = final_text
            except Exception:
                pass

        self._cache = {}
        return ASRResult(text=text, confidence=0.95, is_final=True)

    async def cancel(self) -> None:
        self._sample_buffer = np.array([], dtype=np.float64)
        self._cache = {}

    def _inference_chunk(self, speech_chunk: np.ndarray, is_final: bool) -> str:
        result = self._model.generate(
            input=speech_chunk,
            cache=self._cache,
            is_final=is_final,
            chunk_size=CHUNK_SIZE,
            encoder_chunk_look_back=ENCODER_CHUNK_LOOK_BACK,
            decoder_chunk_look_back=DECODER_CHUNK_LOOK_BACK,
        )
        if result and result[0]:
            return result[0].get("text", "")
        return ""


class StreamingASREngine(ASREngine):
    """FunASR paraformer-zh-streaming 引擎 — 支持流式 + 批量识别。"""

    def __init__(self):
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        self._model = None

    async def load_model(self):
        from funasr import AutoModel

        logger.info("Loading streaming ASR model from %s", MODEL_DIR)
        self._model = AutoModel(model=MODEL_DIR, disable_update=True)
        logger.info("Streaming ASR model loaded")

    @property
    def supports_streaming(self) -> bool:
        return True

    async def start_stream(self, params: dict) -> ASRStreamContext:
        if self._model is None:
            raise RuntimeError("Streaming ASR model not loaded")
        language = params.get("language", "zh")
        return FunASRStreamContext(self._model, language, self._semaphore)

    async def recognize(self, audio_stream: bytes, params: dict) -> ASRResult:
        """批量识别: 用流式模型 chunk-by-chunk 模拟离线识别。"""
        if self._model is None:
            raise RuntimeError("Streaming ASR model not loaded")

        async with self._semaphore:
            try:
                samples = np.frombuffer(audio_stream, dtype=np.int16).astype(np.float64) / 32768.0
                cache = {}
                full_text = ""

                total_chunks = int(len(samples) // CHUNK_STRIDE)
                for i in range(total_chunks):
                    chunk = samples[i * CHUNK_STRIDE:(i + 1) * CHUNK_STRIDE]
                    is_final = (i == total_chunks - 1) and (len(samples) % CHUNK_STRIDE == 0)
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        None,
                        lambda c=chunk, f=is_final: self._model.generate(
                            input=c, cache=cache, is_final=f,
                            chunk_size=CHUNK_SIZE,
                            encoder_chunk_look_back=ENCODER_CHUNK_LOOK_BACK,
                            decoder_chunk_look_back=DECODER_CHUNK_LOOK_BACK,
                        ),
                    )
                    if result and result[0]:
                        full_text += result[0].get("text", "")

                # Process remaining samples
                remaining = samples[total_chunks * CHUNK_STRIDE:]
                if len(remaining) > 0:
                    result = await loop.run_in_executor(
                        None,
                        lambda: self._model.generate(
                            input=remaining, cache=cache, is_final=True,
                            chunk_size=CHUNK_SIZE,
                            encoder_chunk_look_back=ENCODER_CHUNK_LOOK_BACK,
                            decoder_chunk_look_back=DECODER_CHUNK_LOOK_BACK,
                        ),
                    )
                    if result and result[0]:
                        full_text += result[0].get("text", "")

                return ASRResult(text=full_text, confidence=0.95, is_final=True)
            except Exception as e:
                logger.error("Streaming ASR recognition failed: %s", e)
                raise RuntimeError(f"Streaming ASR recognition failed: {e}") from e

    async def health_check(self) -> bool:
        return self._model is not None


Engine = StreamingASREngine
