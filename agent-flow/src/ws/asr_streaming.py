"""ASR 流式传输管理 — 封装流生命周期（create→feed→finish/cancel）。

从 handler.py 抽出，把原本散在主循环局部变量里的 asr_stream/speech_started/asr_partial_text
三态收敛到一个对象，降低 handle 主循环的变量纠缠。

职责边界：只管 ASR 流；音频增益（apply_gain + AEC 判断）与 ASR 结果有效性判定
（空文本/单字过滤）留 handler——它们依赖 audio_buffer 和 turn 语义，不属于 ASR。
"""
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from clients.asr_ws_client import ASRWsStream


class AsrStreamingManager:
    """单轮对话的 ASR 流管理器。

    provider 选择(WS)在首帧时确定;之后 feed 持续喂帧。主路径由服务端 FSMN-VAD
    分段后主动推 final 触发 on_final 回调驱动轮次;finalize 保留为批量/后备接口
    (主动收尾取结果)。barge-in 用 reset_server_segment 丢服务端进行中段(连接保持)。
    """

    def __init__(
        self,
        asr_ws_client=None,
        use_streaming_asr: bool = False,
        on_final: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        self._asr_ws_client = asr_ws_client
        self._use_streaming_asr = use_streaming_asr
        self._on_final = on_final  # WS 服务端驱动 final 回调(per-call)
        # 流生命周期三态
        self._stream: "ASRWsStream | None" = None
        self._speech_started = False
        self._partial_text = ""

    @property
    def stream(self) -> "ASRWsStream | None":
        """暴露当前流，供挂断清理时统一 cancel。"""
        return self._stream

    def _provider(self):
        """返回 ASR 流式提供者(WS);未配置返回 None。"""
        if self._asr_ws_client:
            return self._asr_ws_client
        return None

    async def feed(self, frame: bytes, call_id: str) -> None:
        """喂一帧音频；首帧时创建流。无 provider 时直接返回（回退批量 ASR）。"""
        provider = self._provider()
        if not provider:
            return

        if not self._speech_started:
            self._speech_started = True

            def _on_partial(text: str, stability: float) -> None:
                self._partial_text = text
                logger.debug("[%s] ASR partial: %s (stability=%.2f)", call_id, text, stability)

            self._stream = provider.create_stream(
                call_id, streaming=self._use_streaming_asr,
                on_partial=_on_partial if self._use_streaming_asr else None,
                on_final=self._on_final,
            )
            if self._stream:
                await self._stream.start()
                logger.info("[%s] ASR stream created", call_id)

        if self._stream:
            self._stream.send_audio(frame)

    async def finalize(self, call_id: str) -> dict | None:
        """收尾流并返回识别结果（dict 含 text/confidence/is_final）。

        主路径(WS 多 final)不调用此方法——on_final 回调驱动轮次。保留供批量模式
        /测试/后备路径:显式 end 取结果,空结果时用 partial 文本兜底。

        流未启动（无 provider 或未喂帧）时返回 None；流返回空结果但收到过 partial
        时回退用 partial 文本兜底（避免流式尾帧丢失导致整轮丢字）。
        """
        if not self._stream:
            return None
        result = await self._stream.finish()
        self._stream = None
        self._speech_started = False
        if not result and self._partial_text:
            result = {"text": self._partial_text, "confidence": 0.8, "is_final": True}
            logger.info("[%s] ASR partial fallback: %s", call_id, self._partial_text[:50])
        self._partial_text = ""
        return result

    async def reset_server_segment(self, call_id: str) -> None:
        """发 {type:reset} 丢服务端进行中段(barge-in 用,连接保持)。无 stream 时 no-op。"""
        if self._stream is not None and hasattr(self._stream, "send_reset"):
            try:
                self._stream.send_reset()
            except Exception as e:
                logger.warning("[%s] reset_server_segment failed: %s", call_id, e)

    async def cancel(self) -> None:
        """取消并丢弃当前流（barge-in / 误触发清理用）。"""
        if self._stream is not None:
            try:
                await self._stream.cancel()
            except Exception:
                pass
        self._stream = None
        self._speech_started = False
        self._partial_text = ""
