"""WebRTC APM 封装 — HPF + AEC + NS + AGC 一次过。

替换 denoise.py 的具体降噪器 + 固定 AUDIO_GAIN。near 端每 30ms 帧拆成 3×10ms
子帧喂入 AudioProcessingModule；reverse（TTS 远端参考）成对先喂，启用回声消除。

调用顺序铁律（源码 audio_processing_module.cpp 确认）：每对子帧必须先
process_reverse_stream（内部 set_stream_delay_ms）再 process_stream。
"""
import logging

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
# 10ms @ 16kHz 16-bit mono = 160 samples = 320 bytes；pipeline 30ms = 3×320
SUBFRAME_BYTES = 320


class WebRTCAPM:
    """AudioProcessingModule 的帧级封装。

    near: 麦克风回采（含 TTS 回声 + 语音 + 噪声）
    reverse: 正在播放的 TTS 帧（AEC 远端参考）；AI 沉默时为静音帧
    """

    def __init__(
        self,
        aec_type: int,
        ns_level: int,
        agc_type: int,
        system_delay_ms: int,
        _ap_cls=None,
    ) -> None:
        # _ap_cls=None 时延迟 import 真实库；测试注入 fake
        if _ap_cls is None:
            from webrtc_audio_processing import AudioProcessingModule as _AP
            _ap_cls = _AP
        self._ap = _ap_cls(aec_type=aec_type, enable_ns=True,
                           agc_type=agc_type, enable_vad=False)
        self._ap.set_stream_format(SAMPLE_RATE, CHANNELS, SAMPLE_RATE, CHANNELS)
        self._ap.set_reverse_stream_format(SAMPLE_RATE, CHANNELS)
        self._ap.set_ns_level(ns_level)
        self._system_delay_ms = system_delay_ms
        logger.info("WebRTCAPM init: aec_type=%d ns=%d agc=%d delay=%dms",
                    aec_type, ns_level, agc_type, system_delay_ms)

    def process(self, near_frame: bytes, reverse_frame: bytes | None) -> bytes:
        """处理一个 30ms near 帧，返回去回声+降噪+增益后的等长帧。

        失败时降级返回原始 near_frame（单帧错误不影响通话）。
        """
        try:
            if reverse_frame:
                self._ap.set_system_delay(self._system_delay_ms)
                for i in range(0, len(reverse_frame), SUBFRAME_BYTES):
                    sub = reverse_frame[i:i + SUBFRAME_BYTES]
                    if len(sub) == SUBFRAME_BYTES:
                        self._ap.process_reverse_stream(sub)
            out = bytearray()
            for i in range(0, len(near_frame), SUBFRAME_BYTES):
                sub = near_frame[i:i + SUBFRAME_BYTES]
                if len(sub) == SUBFRAME_BYTES:
                    out.extend(self._ap.process_stream(sub))
                else:
                    out.extend(sub)  # 尾部不足 10ms 透传
            return bytes(out)
        except Exception as e:
            logger.error("WebRTCAPM process failed, passthrough: %s", e)
            return near_frame

    def has_echo(self) -> bool:
        try:
            return self._ap.has_echo()
        except Exception:
            return False


def create_audio_processing(settings) -> "WebRTCAPM | None":
    """工厂：CALLBOT_AEC_ENABLED=true 时创建 WebRTCAPM，否则/库缺失返回 None。"""
    if not settings.aec_enabled:
        return None
    try:
        return WebRTCAPM(
            aec_type=settings.aec_type,
            ns_level=settings.aec_ns_level,
            agc_type=settings.aec_agc_type,
            system_delay_ms=settings.aec_system_delay_ms,
        )
    except ImportError as e:
        logger.warning("webrtc_audio_processing 未安装，AEC 关闭（走原 denoise 路径）: %s", e)
        return None
