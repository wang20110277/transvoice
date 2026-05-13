import re
import logging
from config import settings

logger = logging.getLogger(__name__)

_SAFE_TEXT_RE = re.compile(r'[^\w\s一-鿿，。！？、；：""''（）,.!?;:\-\d]')


class TTSProfileMap:
    _MAP = {
        "customer_service": "tts_customer_service_v1",
        "collection": "tts_collection_v1",
        "marketing": "tts_marketing_v1",
    }

    @classmethod
    def get(cls, biz_type: str) -> str:
        return cls._MAP.get(biz_type, "tts_customer_service_v1")

    @classmethod
    def get_asr(cls) -> str:
        return "asr_default_v1"


class FSActions:
    def __init__(self, conn):
        self.conn = conn

    def play_legal_notice(self, uuid: str) -> bool:
        result = self.conn.api(f"uuid_playback {uuid} {settings.legal_notice_file}")
        return str(result) == "+OK"

    def start_recording(self, uuid: str, rec_path: str) -> bool:
        import os
        os.makedirs(rec_path, exist_ok=True)
        self.conn.api(f"uuid_record {uuid} start {rec_path}/caller.wav 48000 16")
        self.conn.api(f"uuid_record {uuid} start {rec_path}/bot.wav 48000 16")
        return True

    def stop_recording(self, uuid: str):
        self.conn.api(f"uuid_record {uuid} stop all")

    def start_detect_speech(self, uuid: str):
        profile = TTSProfileMap.get_asr()
        self.conn.api(f"uuid_detect_speech {uuid} unimrcp://{profile} builtin:grammar:digits")

    def stop_detect_speech(self, uuid: str):
        self.conn.api(f"uuid_detect_speech {uuid} stop")

    def tts_speak(self, uuid: str, biz_type: str, text: str):
        profile = TTSProfileMap.get(biz_type)
        safe_text = _SAFE_TEXT_RE.sub('', text)[:200]
        self.conn.api(f"uuid_playback {uuid} say:{safe_text}^^{profile}")

    def transfer(self, uuid: str, extension: str = None):
        ext = extension or settings.handoff_extension
        self.conn.api(f"uuid_transfer {uuid} loopback/{ext}")

    def hangup(self, uuid: str, reason: str = "NORMAL_CLEARING"):
        self.conn.api(f"uuid_kill {uuid} {reason}")
