"""EdgeTTS engine — Microsoft Edge 在线 TTS（无需 GPU）。"""
import asyncio
import hashlib
import logging
import os
from io import BytesIO

from ttsadapter.base import TTSEngine, TTSResult

logger = logging.getLogger(__name__)

EDGE_TTS_VOICE = os.environ.get("EDGE_TTS_VOICE", "zh-CN-XiaoxiaoNeural")
EDGE_TTS_MAX_CONCURRENT = int(os.environ.get("EDGE_TTS_MAX_CONCURRENT", "10"))

BIZ_TYPE_PROFILES = {
    "customer_service": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+0%", "volume": "+0%"},
    "collection": {"voice": "zh-CN-YunxiNeural", "rate": "-10%", "volume": "+0%"},
    "marketing": {"voice": "zh-CN-XiaoyiNeural", "rate": "+10%", "volume": "+0%"},
}
DEFAULT_PROFILE = BIZ_TYPE_PROFILES["customer_service"]


class EdgeTTSEngine(TTSEngine):
    def __init__(self):
        self._cache_dir = os.environ.get("TTS_CACHE_DIR", "/data/tts_cache")
        self._semaphore = asyncio.Semaphore(EDGE_TTS_MAX_CONCURRENT)

    def _get_profile(self, params: dict) -> dict:
        biz_type = params.get("biz_type", "customer_service")
        return BIZ_TYPE_PROFILES.get(biz_type, DEFAULT_PROFILE)

    def _cache_key(self, text: str, profile: dict) -> str:
        raw = f"{profile['voice']}:{profile.get('rate', '+0%')}:{text}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_path(self, biz_type: str, key: str) -> str:
        return os.path.join(self._cache_dir, biz_type, f"{key}.wav")

    async def synthesize(self, text: str, params: dict) -> TTSResult:
        async with self._semaphore:
            profile = self._get_profile(params)
            biz_type = params.get("biz_type", "customer_service")
            cache_path = self._cache_path(biz_type, self._cache_key(text, profile))

            if os.path.exists(cache_path):
                with open(cache_path, "rb") as f:
                    return TTSResult(audio=f.read())

            import edge_tts
            from pydub import AudioSegment

            communicate = edge_tts.Communicate(
                text,
                voice=profile["voice"],
                rate=profile.get("rate", "+0%"),
                volume=profile.get("volume", "+0%"),
            )

            mp3_buffer = BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    mp3_buffer.write(chunk["data"])

            mp3_buffer.seek(0)

            # MP3 → WAV (22050Hz mono 16-bit，与 CosyVoice 输出一致，下游 agent-flow 统一重采样到 16kHz)
            audio_segment = AudioSegment.from_mp3(mp3_buffer)
            audio_segment = audio_segment.set_frame_rate(22050).set_channels(1).set_sample_width(2)

            wav_buffer = BytesIO()
            audio_segment.export(wav_buffer, format="wav")
            wav_buffer.seek(0)
            audio = wav_buffer.read()

            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "wb") as f:
                f.write(audio)

            return TTSResult(audio=audio)

    async def health_check(self) -> bool:
        try:
            import edge_tts

            communicate = edge_tts.Communicate("测试", voice=EDGE_TTS_VOICE)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    return True
            return False
        except Exception as e:
            logger.warning("EdgeTTS health check failed: %s", e)
            return False


Engine = EdgeTTSEngine
