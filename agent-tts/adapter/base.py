from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TTSResult:
    audio: bytes
    content_type: str = "audio/wav"
    duration_ms: int = 0


class TTSEngine(ABC):
    @abstractmethod
    async def synthesize(self, text: str, params: dict) -> TTSResult:
        """接收文本，返回合成音频"""

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
