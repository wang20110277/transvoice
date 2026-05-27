from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ASRResult:
    text: str
    confidence: float
    is_final: bool


@dataclass
class StreamingASRResult:
    """流式识别中间或最终结果。"""
    text: str
    confidence: float
    is_final: bool
    is_partial: bool = False
    stability: float = 0.0


class ASRStreamContext(ABC):
    """流式识别会话 — 生命周期: start → send_audio*N → get_partial*N → finish/cancel。"""

    @abstractmethod
    async def start(self) -> None:
        """初始化流式会话。"""

    @abstractmethod
    def send_audio(self, chunk: bytes) -> None:
        """喂入音频帧（非阻塞，内部缓冲）。"""

    @abstractmethod
    async def get_partial(self) -> StreamingASRResult | None:
        """获取最新中间结果（轮询），无新结果返回 None。"""

    @abstractmethod
    async def finish(self) -> ASRResult:
        """结束识别，等待最终结果。"""

    @abstractmethod
    async def cancel(self) -> None:
        """取消会话。"""


class ASREngine(ABC):
    @abstractmethod
    async def recognize(self, audio_stream: bytes, params: dict) -> ASRResult:
        """批量识别：接收完整音频，返回结果。"""

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查。"""

    @property
    def supports_streaming(self) -> bool:
        """是否支持流式识别。非流式引擎返回 False。"""
        return False

    async def start_stream(self, params: dict) -> ASRStreamContext:
        """创建流式识别会话。非流式引擎抛 NotImplementedError。"""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support streaming recognition"
        )
