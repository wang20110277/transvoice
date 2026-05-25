import asyncio
import hashlib
import io
import logging
import os

from ttsadapter.base import TTSEngine, TTSResult

logger = logging.getLogger(__name__)

MODEL_DIR = os.environ.get("MODEL_DIR", "/opt/cosyvoice/models/CosyVoice3-0.5B")
COSYVOICE_MAX_CONCURRENT = int(os.environ.get("COSYVOICE_MAX_CONCURRENT", "5"))

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
        runtime_path = os.environ.get("COSYVOICE_RUNTIME", "/opt/cosyvoice/runtime")
        if runtime_path not in sys.path:
            sys.path.insert(0, runtime_path)
        from cosyvoice.cli.cosyvoice import AutoModel

        logger.info("Loading CosyVoice model from %s", MODEL_DIR)
        self._model = AutoModel(model_dir=MODEL_DIR, fp16=False)
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
                chunks = list(self._model.inference_instruct2(
                    text, instruct_text, prompt_wav, stream=False, speed=profile["speed"],
                ))

                buffer = io.BytesIO()
                for chunk in chunks:
                    sf.write(buffer, chunk["tts_speech"].numpy().flatten(), 22050, format="WAV")
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
