import importlib
from ttsadapter.base import TTSEngine


def load_tts_engine(name: str) -> TTSEngine:
    try:
        module = importlib.import_module(f"ttsadapter.engines.{name}.engine")
        return module.Engine()
    except (ImportError, AttributeError) as e:
        raise ValueError(f"Unknown TTS engine: {name}") from e
