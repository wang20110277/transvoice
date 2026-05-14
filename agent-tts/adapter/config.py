import importlib
from adapter.base import TTSEngine


def load_tts_engine(name: str) -> TTSEngine:
    try:
        module = importlib.import_module(f"adapter.engines.{name}.engine")
        return module.Engine()
    except (ImportError, AttributeError) as e:
        raise ValueError(f"Unknown TTS engine: {name}") from e
