import asyncio
import hashlib
import io
import logging
import os
from collections.abc import AsyncIterator

import numpy as np

from ttsadapter.base import TTSEngine, TTSChunk, TTSResult

logger = logging.getLogger(__name__)

MODEL_DIR = os.environ.get("MODEL_DIR", "/opt/cosyvoice/models/CosyVoice3-0.5B")
COSYVOICE_MAX_CONCURRENT = int(os.environ.get("COSYVOICE_MAX_CONCURRENT", "5"))
# cpu | mps | auto (mps if available, else cpu)
COSYVOICE_DEVICE = os.environ.get("COSYVOICE_DEVICE", "auto")

VOICES_DIR = os.environ.get("VOICES_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "voices"))

BIZ_TYPE_PROFILES = {
      "customer_service": {"voice": "default_female.wav", "instruct": "You are a helpful assistant. 请用温柔的客服语气说话。<|endofprompt|>", "speed": 1.0},
      "collection": {"voice": "default_female.wav", "instruct": "You are a helpful assistant. 请用严肃的催收语气说话。<|endofprompt|>", "speed": 0.9},
      "marketing": {"voice": "default_female.wav", "instruct": "You are a helpful assistant. 请用活泼的营销语气说话。<|endofprompt|>", "speed": 1.1},
}

DEFAULT_PROFILE = BIZ_TYPE_PROFILES["customer_service"]


class CosyVoiceTTSEngine(TTSEngine):
    def __init__(self):
        self._cache_dir = os.environ.get("TTS_CACHE_DIR", "/data/tts_cache")
        self._semaphore = asyncio.Semaphore(COSYVOICE_MAX_CONCURRENT)
        self._model = None

    async def load_model(self):
        import sys

        import torch

        runtime_path = os.environ.get("COSYVOICE_RUNTIME", "/opt/cosyvoice/runtime")
        if runtime_path not in sys.path:
            sys.path.insert(0, runtime_path)
        from cosyvoice.cli.cosyvoice import AutoModel

        logger.info("Loading CosyVoice model from %s", MODEL_DIR)
        self._model = AutoModel(model_dir=MODEL_DIR, fp16=False)

        # Device placement: cpu | mps | auto
        use_mps = (
            COSYVOICE_DEVICE == "mps"
            or (COSYVOICE_DEVICE == "auto"
                and not torch.cuda.is_available()
                and hasattr(torch.backends, "mps")
                and torch.backends.mps.is_available())
        )
        if use_mps:
            mps = torch.device("mps")
            self._model.model.device = mps
            self._model.model.llm.to(mps)
            self._model.model.flow.to(mps)
            self._model.model.hift.to(mps)
            # np.hamming returns float64 → MPS only supports float32
            m = self._model.model
            if hasattr(m, 'mel_window') and not isinstance(m.mel_window, torch.Tensor):
                m.mel_window = torch.tensor(m.mel_window, dtype=torch.float32)
            if hasattr(m, 'speech_window') and not isinstance(m.speech_window, torch.Tensor):
                m.speech_window = torch.tensor(m.speech_window, dtype=torch.float32)
            logger.info("CosyVoice model moved to MPS device")
        else:
            device = self._model.model.device
            logger.info("CosyVoice model on %s", device)

        logger.info("CosyVoice model loaded")

    def _get_profile(self, params: dict) -> dict:
        biz_type = params.get("biz_type", "customer_service")
        return BIZ_TYPE_PROFILES.get(biz_type, DEFAULT_PROFILE)

    def _cache_key(self, text: str, profile: dict) -> str:
        raw = f"{profile['voice']}:{text}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_path(self, biz_type: str, key: str) -> str:
        return os.path.join(self._cache_dir, biz_type, f"{key}.wav")

    def _voice_path(self, profile: dict) -> str:
        return os.path.join(VOICES_DIR, profile["voice"])

    @property
    def supports_streaming(self) -> bool:
        return True

    async def synthesize_stream(self, text: str, params: dict) -> AsyncIterator[TTSChunk]:
        """流式合成: 调用 CosyVoice stream=True，逐块 yield PCM int16 音频。"""
        if self._model is None:
            raise RuntimeError("CosyVoice model not loaded")

        async with self._semaphore:
            profile = self._get_profile(params)
            prompt_wav = self._voice_path(profile)
            instruct_text = profile.get("instruct", "")
            loop = asyncio.get_event_loop()
            stream_gen = self._model.inference_instruct2(
                text, instruct_text, prompt_wav, stream=True, speed=profile["speed"],
            )

            chunk_index = 0
            for chunk in await loop.run_in_executor(None, list, stream_gen):
                tensor = chunk["tts_speech"]
                # MPS doesn't support float64 — force float32 before numpy
                pcm_float = tensor.cpu().float().numpy().flatten()
                pcm_int16 = (pcm_float * 32767).clip(-32768, 32767).astype(np.int16)
                chunk_index += 1
                yield TTSChunk(
                    audio=pcm_int16.tobytes(),
                    is_final=False,
                    duration_ms=len(pcm_int16) * 1000 // 22050,
                )

            if chunk_index == 0:
                yield TTSChunk(audio=b"", is_final=True, duration_ms=0)
            else:
                yield TTSChunk(audio=b"", is_final=True, duration_ms=0)

    async def synthesize(self, text: str, params: dict) -> TTSResult:
        if self._model is None:
            raise RuntimeError("CosyVoice model not loaded")

        async with self._semaphore:
            profile = self._get_profile(params)
            biz_type = params.get("biz_type", "customer_service")
            cache_path = self._cache_path(biz_type, self._cache_key(text, profile))

            if os.path.exists(cache_path):
                with open(cache_path, "rb") as f:
                    return TTSResult(audio=f.read())

            try:
                import soundfile as sf

                prompt_wav = self._voice_path(profile)
                instruct_text = profile.get("instruct", "")

                # GPU 推理放线程池，避免阻塞事件循环
                # 不阻塞才能并发处理多个合成请求 + 响应 WebSocket ping/pong
                loop = asyncio.get_event_loop()
                chunks = await loop.run_in_executor(
                    None,
                    lambda: list(self._model.inference_instruct2(
                        text, instruct_text, prompt_wav, stream=False,
                        speed=profile["speed"],
                    )),
                )

                buffer = io.BytesIO()
                for chunk in chunks:
                    sf.write(buffer, chunk["tts_speech"].cpu().float().numpy().flatten(), 22050, format="WAV")
                    break

                buffer.seek(0)
                audio = buffer.read()

                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, "wb") as f:
                    f.write(audio)

                return TTSResult(audio=audio)
            except Exception as e:
                logger.error("CosyVoice synthesis failed: %s", e)
                raise RuntimeError(f"CosyVoice synthesis failed: {e}") from e

    async def health_check(self) -> bool:
        return self._model is not None


Engine = CosyVoiceTTSEngine
