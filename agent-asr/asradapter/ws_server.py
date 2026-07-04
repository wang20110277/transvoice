"""WebSocket ASR 服务 — FSMN-VAD 流式分段 + 段级 recognize + 主动多次推 final。

协议:
    客户端 → 服务端:
        Text JSON: {"type":"config","call_id":"...","language":"zh","sample_rate":16000}
        Binary:    PCM 16-bit 16kHz mono 音频帧(全量喂)
        Text JSON: {"type":"end"}    (兜底:force_flush 冲刷尾部 / 降级整段识别)
        Text JSON: {"type":"reset"}  (barge-in:丢服务端进行中段)
    服务端 → 客户端:
        Text JSON: {"type":"result","text":"...","confidence":0.95,"is_final":true}
                    ↑ VAD 切段识别完主动推,单连接可多次(每语音段一个)
        Text JSON: {"type":"error","message":"..."}
"""
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from asradapter.base import ASREngine
from asradapter.vad_segmenter import FsmnVadSegmenter, SAMPLE_RATE as VAD_SAMPLE_RATE

logger = logging.getLogger(__name__)


def _resample_to_16k(pcm: bytes, declared_sr: int) -> bytes:
    """declared_sr → 16kHz 重采样(线性插值,够用;整数倍关系直接重采样)。"""
    if declared_sr == VAD_SAMPLE_RATE or declared_sr <= 0:
        return pcm
    # 16-bit mono samples;用 numpy 线性插值(已在依赖链:funasr 依赖 numpy)
    import numpy as np
    n_in = len(pcm) // 2
    if n_in == 0:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n_out = int(round(n_in * VAD_SAMPLE_RATE / declared_sr))
    idx = np.linspace(0, n_in - 1, n_out)
    resampled = np.interp(idx, np.arange(n_in), samples)
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


class ASRWebSocketHandler:
    """WS handler — FSMN-VAD 分段层 + 段级 batch recognize。

    服务端 VAD 自检端点:每收帧喂 segmenter,得已完成段即 recognize 并主动推 final
    (不等 end)。segmenter 推理异常时降级:后续收 end 走整段 batch recognize 保可用。
    """

    def __init__(self, engine: ASREngine, segmenter: FsmnVadSegmenter):
        self._engine = engine
        self._segmenter = segmenter

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()
        call_id = ""
        language = "zh"
        declared_sr = VAD_SAMPLE_RATE
        degraded = False
        pending_audio: list[bytes] = []  # 降级路径累积用

        try:
            while True:
                data = await websocket.receive()

                if "text" in data and data["text"]:
                    msg = json.loads(data["text"])
                    msg_type = msg.get("type")

                    if msg_type == "config":
                        call_id = msg.get("call_id", "")
                        language = msg.get("language", "zh")
                        declared_sr = int(msg.get("sample_rate", VAD_SAMPLE_RATE))
                        logger.info("[WS-ASR] config call_id=%s sr=%d", call_id, declared_sr)

                    elif msg_type == "reset":
                        self._segmenter.reset()
                        pending_audio.clear()

                    elif msg_type == "end":
                        if degraded:
                            await self._batch_recognize(
                                websocket, b"".join(pending_audio), call_id, language)
                        else:
                            for seg in self._segmenter.force_flush():
                                await self._recognize_and_push(
                                    websocket, seg, call_id, language)
                        return

                elif "bytes" in data and data["bytes"]:
                    pcm16k = _resample_to_16k(data["bytes"], declared_sr)
                    pending_audio.append(pcm16k)
                    try:
                        segments = self._segmenter.feed(pcm16k)
                    except Exception as e:
                        logger.warning("[WS-ASR] VAD degraded call_id=%s: %s — fallback to end-batch", call_id, e)
                        degraded = True
                        segments = []
                    for seg in segments:
                        await self._recognize_and_push(websocket, seg, call_id, language)

        except WebSocketDisconnect:
            logger.info("[WS-ASR] client disconnected call_id=%s", call_id)
        except Exception as e:
            logger.error("[WS-ASR] error call_id=%s: %s", call_id, e)
            try:
                await websocket.send_json({"type": "error", "message": str(e)})
            except Exception:
                pass

    async def _recognize_and_push(
        self, websocket: WebSocket, audio: bytes, call_id: str, language: str,
    ) -> None:
        params = {"call_id": call_id, "language": language}
        try:
            result = await self._engine.recognize(audio, params)
        except Exception as e:
            logger.error("[WS-ASR] segment recognize error call_id=%s: %s", call_id, e)
            await websocket.send_json({"type": "error", "message": str(e)})
            return
        await websocket.send_json({
            "type": "result", "text": result.text,
            "confidence": result.confidence, "is_final": True,
        })

    async def _batch_recognize(
        self, websocket: WebSocket, audio_bytes: bytes, call_id: str, language: str,
    ) -> None:
        if not audio_bytes:
            await websocket.send_json({
                "type": "result", "text": "", "confidence": 0.0, "is_final": True})
            return
        await self._recognize_and_push(websocket, audio_bytes, call_id, language)
