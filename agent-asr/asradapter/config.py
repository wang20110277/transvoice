import importlib
from asradapter.base import ASREngine


def load_asr_engine(name: str) -> ASREngine:
    """反射加载 engines/{name}/engine.py 中的 Engine 类"""
    try:
        module = importlib.import_module(f"asradapter.engines.{name}.engine")
        return module.Engine()
    except (ImportError, AttributeError) as e:
        raise ValueError(f"Unknown ASR engine: {name}") from e
