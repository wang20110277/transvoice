"""WebRTC APM 封装 — HPF + AEC + NS + AGC 一次过（基于 livekit.rtc.AudioProcessingModule）。

替换 denoise.py 的具体降噪器 + 固定 AUDIO_GAIN。near 端每 30ms 帧直接喂入 APM
（livekit 的 AudioFrame 无 10ms 限制，省掉拆帧）；reverse（TTS 远端参考）成对先喂。

调用顺序（livekit in-place 语义）：每帧先 set_stream_delay_ms + process_reverse_stream
（喂远端参考 + 设延迟）再 process_stream（in-place 处理 capture，从 frame.data 取回）。
"""
import logging

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLES_PER_FRAME = 480  # 30ms @ 16kHz 16-bit mono = 960 bytes


class WebRTCAPM:
    """livekit AudioProcessingModule 的帧级封装。

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
        _frame_cls=None,
    ) -> None:
        # _ap_cls=None 时延迟 import livekit；测试注入 fake
        if _ap_cls is None:
            from livekit.rtc import AudioProcessingModule as _AP
            from livekit.rtc import AudioFrame as _AF
            _ap_cls = _AP
            _frame_cls = _AF
        # livekit 用 bool 开关；aec_type>0 / agc_type>0 转 bool（兼容现有 config）
        self._ap = _ap_cls(
            echo_cancellation=aec_type > 0,
            noise_suppression=True,
            high_pass_filter=True,
            auto_gain_control=agc_type > 0,
        )
        self._frame_cls = _frame_cls
        self._system_delay_ms = system_delay_ms
        logger.info("WebRTCAPM(livekit) init: aec=%s ns=True hpf=True agc=%s delay=%dms",
                    aec_type > 0, agc_type > 0, system_delay_ms)

    def _to_frame(self, pcm: bytes):
        return self._frame_cls(pcm, SAMPLE_RATE, CHANNELS, SAMPLES_PER_FRAME)

    def process(self, near_frame: bytes, reverse_frame: bytes | None) -> bytes:
        """处理一个 30ms near 帧，返回去回声+降噪+增益后的等长帧。

        失败时降级返回原始 near_frame（单帧错误不影响通话）。
        """
        try:
            if reverse_frame:
                self._ap.set_stream_delay_ms(self._system_delay_ms)
                self._ap.process_reverse_stream(self._to_frame(reverse_frame))
            near_af = self._to_frame(near_frame)
            self._ap.process_stream(near_af)  # in-place
            return bytes(near_af.data)
        except Exception as e:
            logger.error("WebRTCAPM process failed, passthrough: %s", e)
            return near_frame


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
        logger.warning("livekit 未安装，AEC 关闭（走原 denoise 路径）: %s", e)
        return None
