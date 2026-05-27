from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class TTSResult:
    audio: bytes
    content_type: str = "audio/wav"
    duration_ms: int = 0


@dataclass
class TTSChunk:
    """流式合成单个音频块。"""
    audio: bytes
    is_final: bool = False
    duration_ms: int = 0


class TTSEngine(ABC):
    @abstractmethod
    async def synthesize(self, text: str, params: dict) -> TTSResult:
        """批量合成：接收文本，返回完整音频。"""

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查。"""

    @property
    def supports_streaming(self) -> bool:
        """是否支持流式合成。非流式引擎返回 False。"""
        return False

    async def synthesize_stream(self, text: str, params: dict) -> AsyncIterator[TTSChunk]:
        """流式合成：接收文本，逐块返回音频。非流式引擎退化为单块返回。"""
        result = await self.synthesize(text, params)
        yield TTSChunk(audio=result.audio, is_final=True, duration_ms=result.duration_ms)
