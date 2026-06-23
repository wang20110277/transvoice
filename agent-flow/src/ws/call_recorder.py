"""通话双声道录音器 — agent-flow 作为音频枢纽自录 caller + AI 两路。

FS record_session 录不到 mod_audio_fork 注入的 TTS（media bug 叠加层，只叠加到播放，
不在 record_session 抓取的原生帧上）。故由 agent-flow 自录：它同时持有
caller 收帧（mod_audio_fork 实时转发的上游）+ AI TTS PCM（自己生成的下游），
挂断时合成立体声 wav：L=caller(upstream) R=AI(downstream)。

开销：每帧一次 bytearray 追加（微秒级），内存 ~4MB/分钟；合成在挂断后一次性完成。
"""
import logging
import os

import numpy as np

from storage.minio_storage import wrap_wav_header

logger = logging.getLogger(__name__)


class CallRecorder:
    """单通话级双声道 PCM 累加器。handle() 内创建一个实例，贯穿通话生命周期。"""

    def __init__(self, sample_rate: int = 16000) -> None:
        self._sample_rate = sample_rate
        self._caller = bytearray()  # upstream（用户）
        self._ai = bytearray()      # downstream（AI TTS）

    def feed_caller(self, pcm: bytes) -> None:
        """喂上游帧（mod_audio_fork 收到的 caller 原始 PCM）。"""
        if pcm:
            self._caller.extend(pcm)

    def feed_ai(self, pcm: bytes) -> None:
        """喂下游帧（audio_callback 的 AI TTS PCM）。"""
        if pcm:
            self._ai.extend(pcm)

    @property
    def has_audio(self) -> bool:
        return bool(self._caller) or bool(self._ai)

    def finalize_stereo_wav(self) -> bytes | None:
        """合成 16-bit 立体声 wav（L=caller R=ai，短的补静音 0）。无音频返回 None。"""
        if not self.has_audio:
            return None
        caller = np.frombuffer(bytes(self._caller), dtype=np.int16)
        ai = np.frombuffer(bytes(self._ai), dtype=np.int16)
        n = max(len(caller), len(ai))
        if len(caller) < n:
            caller = np.pad(caller, (0, n - len(caller)))  # int16 0 = 静音
        if len(ai) < n:
            ai = np.pad(ai, (0, n - len(ai)))
        stereo = np.stack([caller, ai], axis=1)  # (n,2) → tobytes 交错 L,R,L,R
        return wrap_wav_header(
            stereo.tobytes(), sample_rate=self._sample_rate, channels=2, bits=16,
        )

    def write_to(self, path: str) -> bool:
        """合成并写 wav 到 path。返回是否实际写入（有音频才写）。"""
        wav = self.finalize_stereo_wav()
        if wav is None:
            logger.info("CallRecorder: no audio captured, skip writing %s", path)
            return False
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        try:
            with open(path, "wb") as f:
                f.write(wav)
            logger.info(
                "CallRecorder: wrote stereo wav %s (%.1fs caller / %.1fs ai)",
                path,
                len(self._caller) / 2 / self._sample_rate,
                len(self._ai) / 2 / self._sample_rate if self._ai else 0,
            )
            return True
        except OSError as e:
            logger.error("CallRecorder: write %s failed: %s", path, e)
            return False
