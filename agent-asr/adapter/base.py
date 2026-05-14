from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ASRResult:
    text: str
    confidence: float
    is_final: bool


class ASREngine(ABC):
    @abstractmethod
    async def recognize(self, audio_stream: bytes, params: dict) -> ASRResult:
        """接收音频流，返回识别结果"""

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
