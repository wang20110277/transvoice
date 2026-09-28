"""WebSocket ASR 服务 — 无状态整段识别（切段职责在客户端）。

协议:
    客户端 → 服务端:
        Text JSON: {"type":"config","call_id":"...","language":"zh","sample_rate":16000}
        Binary:    PCM 16-bit mono 音频帧（逐帧重采样到 16kHz 后累积）
        Text JSON: {"type":"end"}  整段识别触发
    服务端 → 客户端:
        Text JSON: {"type":"result","text":"...","confidence":0.95,"is_final":true}
                    ↑ end 后回单条 result 并关闭连接
        Text JSON: {"type":"error","message":"..."}
"""
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from asradapter.base import ASREngine

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000


def _resample_to_16k(pcm: bytes, declared_sr: int) -> bytes:
    """declared_sr → 16kHz 重采样(线性插值,够用;整数倍关系直接重采样)。"""
    if declared_sr == SAMPLE_RATE or declared_sr <= 0:
        return pcm
    # 16-bit mono samples;用 numpy 线性插值(已在依赖链:funasr 依赖 numpy)
    import numpy as np
    n_in = len(pcm) // 2
    if n_in == 0:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n_out = int(round(n_in * SAMPLE_RATE / declared_sr))
    idx = np.linspace(0, n_in - 1, n_out)
    resampled = np.interp(idx, np.arange(n_in), samples)
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


class ASRWebSocketHandler:
    """WS handler — 无状态整段识别：客户端切段（VAD 在客户端），服务端只累积识别。"""

    def __init__(self, engine: ASREngine):
        self._engine = engine

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()
        call_id = ""
        language = "zh"
        declared_sr = SAMPLE_RATE
        audio = bytearray()

        try:
            while True:
                data = await websocket.receive()

                if "text" in data and data["text"]:
                    msg = json.loads(data["text"])
                    msg_type = msg.get("type")

                    if msg_type == "config":
                        call_id = msg.get("call_id", "")
                        language = msg.get("language", "zh")
                        declared_sr = int(msg.get("sample_rate", SAMPLE_RATE))
                        logger.info("[WS-ASR] config call_id=%s sr=%d", call_id, declared_sr)

                    elif msg_type == "end":
                        if not audio:
                            await websocket.send_json({
                                "type": "result", "text": "",
                                "confidence": 0.0, "is_final": True})
                            return
                        await self._recognize_and_push(
                            websocket, bytes(audio), call_id, language)
                        return

                elif "bytes" in data and data["bytes"]:
                    audio.extend(_resample_to_16k(data["bytes"], declared_sr))

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
            logger.error("[WS-ASR] recognize error call_id=%s: %s", call_id, e)
            await websocket.send_json({"type": "error", "message": str(e)})
            return
        await websocket.send_json({
            "type": "result", "text": result.text,
            "confidence": result.confidence, "is_final": True,
        })
