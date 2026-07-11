"""FSMN-VAD 流式分段器 — 把连续 PCM 流切成已完成语音段,供 SenseVoice 段级 recognize。

基于 FunASR AutoModel(FSMN-VAD)流式 chunk 推理:feed 增量喂音频,内部累积到
chunk_size 后 generate(is_final=False)取已完成段;force_flush 用 is_final=True
冲刷尾部。FunASR 返回的段时间戳是绝对 ms(跨 chunk 经 cache 累积),故需保留音频
并按绝对 ms 切片,已吐段不重复。

ms→字节:16kHz×16bit mono = 32 字节/ms。
"""
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
BYTES_PER_MS = SAMPLE_RATE * 2 // 1000  # 32 bytes/ms (16-bit mono)

DEFAULT_MODEL_DIR = os.environ.get(
    "FSMN_VAD_MODEL_DIR",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "models", "speech_fsmn_vad_zh-cn-16k-common-pytorch",
    ),
)

# FunASR ≥1.2.7 FSMN-VAD 流式 chunk_size 接受 int(毫秒),非旧版 [0,10,5] list。
# generate() 内部 chunk_stride_samples = chunk_size * fs / 1000;600ms 是流式分段步长。
CHUNK_SIZE = 600
CHUNK_MS = CHUNK_SIZE  # 累积阈值 = chunk 步长(ms)
# _audio 滑动窗口上限。用户长静音时 consumed 停滞、_audio 持续累积;FSMN 段延迟
# (尾部静音确认)远小于此,丢弃超出部分不影响段切片(段 start_ms>=consumed)。
MAX_RETAINED_MS = 30_000  # 30s ≈ 960KB


def load_fsmn_vad_model(model_dir: str = DEFAULT_MODEL_DIR):
    """加载 FSMN-VAD AutoModel(进程级单例,只读权重,可跨连接共享)。

    模型权重在推理期间只读,可被多个 FsmnVadSegmenter 共享;每连接的流状态
    生活在 generate() 的 cache 字典中,由 FsmnVadSegmenter 实例持有。
    """
    from funasr import AutoModel
    return AutoModel(model=model_dir, disable_update=True)


class FsmnVadSegmenter:
    """流式 VAD 分段器。线程不安全 —— 单 WS 连接独占一个实例。

    model 参数为 load_fsmn_vad_model() 返回的共享 AutoModel(只读);每实例自己
    持有 feed_buf/audio/cache 等流状态,故多连接并发安全。
    """

    def __init__(self, model, chunk_size=CHUNK_SIZE):
        self._model = model            # 共享的已加载 AutoModel(只读)
        self._chunk_size = chunk_size
        self._feed_buf = bytearray()    # 累积到 chunk_bytes 才喂模型
        self._audio = bytearray()       # 保留音频供段切片(绝对 ms 索引)
        self._audio_offset_ms = 0       # _audio[0] 对应的绝对 ms
        self._cache: dict[str, Any] = {}
        self._consumed_ms = 0           # 已吐段的最大 end_ms

    def feed(self, pcm: bytes) -> list[bytes]:
        """累积 pcm,每达 chunk 阈值跑一次 generate,返回新完成的语音段 PCM 列表。"""
        self._audio.extend(pcm)
        self._feed_buf.extend(pcm)
        chunk_bytes = CHUNK_MS * BYTES_PER_MS
        segments: list[bytes] = []
        while len(self._feed_buf) >= chunk_bytes:
            chunk = bytes(self._feed_buf[:chunk_bytes])
            del self._feed_buf[:chunk_bytes]
            segments.extend(self._run(chunk, is_final=False))
        return segments

    def force_flush(self) -> list[bytes]:
        """is_final=True 冲刷尾部残余音频,返回最后一段(若 VAD 判定为语音)。"""
        if not self._feed_buf:
            return []
        chunk = bytes(self._feed_buf)
        self._feed_buf.clear()
        return self._run(chunk, is_final=True)

    def reset(self) -> None:
        """重置所有内部状态(新一轮/barge-in 后)。"""
        self._feed_buf.clear()
        self._audio.clear()
        self._cache = {}
        self._audio_offset_ms = 0
        self._consumed_ms = 0

    def _run(self, chunk: bytes, is_final: bool) -> list[bytes]:
        try:
            res = self._model.generate(
                input=chunk,
                is_final=is_final,
                chunk_size=self._chunk_size,
                cache=self._cache,
            )
        except Exception as e:
            logger.error("FSMN-VAD generate failed (is_final=%s): %s", is_final, e)
            raise
        emitted = self._extract_segments(res)
        self._drop_consumed_audio()
        return emitted

    def _extract_segments(self, res) -> list[bytes]:
        if not res or not isinstance(res[0], dict):
            return []
        value = res[0].get("value", [])
        out: list[bytes] = []
        for seg in value:
            start_ms, end_ms = int(seg[0]), int(seg[1])
            if end_ms <= self._consumed_ms:
                # 已吐过;或 end==-1(FSMN 流式 sentinel:段未结束,起始已在先前 chunk 报过,等结束标记)
                continue
            # start==-1(段起始在先前 chunk 已报)时回落到 consumed,从上次吐段点切片
            start_ms = max(start_ms, self._consumed_ms)
            b0 = (start_ms - self._audio_offset_ms) * BYTES_PER_MS
            b1 = (end_ms - self._audio_offset_ms) * BYTES_PER_MS
            if b1 <= b0 or b1 > len(self._audio):
                continue
            out.append(bytes(self._audio[b0:b1]))
            self._consumed_ms = end_ms
        return out

    def _drop_consumed_audio(self) -> None:
        """丢弃已吐段之前的音频;consumed 停滞时按 MAX_RETAINED_MS 截断防无界增长。"""
        drop_ms = min(self._consumed_ms - self._audio_offset_ms,
                      len(self._audio) // BYTES_PER_MS)
        if drop_ms > 0:
            del self._audio[:drop_ms * BYTES_PER_MS]
            self._audio_offset_ms += drop_ms
        retained_ms = len(self._audio) // BYTES_PER_MS
        if retained_ms > MAX_RETAINED_MS:
            excess = retained_ms - MAX_RETAINED_MS
            del self._audio[:excess * BYTES_PER_MS]
            self._audio_offset_ms += excess
            if self._consumed_ms < self._audio_offset_ms:
                self._consumed_ms = self._audio_offset_ms
