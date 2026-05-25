"""gRPC TTS service — unary synthesis alongside FastAPI HTTP."""
import asyncio
import base64
import logging
import os
from concurrent import futures

import grpc

from ttsadapter.proto import tts_pb2, tts_pb2_grpc
from ttsadapter.base import TTSEngine

logger = logging.getLogger(__name__)

GRPC_PORT = int(os.environ.get("TTS_GRPC_PORT", "50052"))


class TTSGrpcServicer(tts_pb2_grpc.TTSServiceServicer):
    """gRPC TTS 服务实现 — 一元调用语音合成。

    协议: 发送文本 + 业务类型，返回合成音频（WAV）。
    端口: 50052（可通过 TTS_GRPC_PORT 环境变量覆盖）
    """

    def __init__(self, engine: TTSEngine):
        self._engine = engine

    async def Synthesize(self, request, context):
        """文本转语音 — 接收文本，调用引擎合成音频，异步上传 MinIO。"""
        text = request.text
        call_id = request.call_id
        biz_type = request.biz_type or "marketing"
        logger.info("[gRPC] Synthesize call_id=%s text=%r biz_type=%s", call_id, text[:50], biz_type)
        params = {"call_id": call_id, "biz_type": biz_type}

        try:
            result = await self._engine.synthesize(text, params)
        except Exception as e:
            logger.error("gRPC TTS synthesize error call_id=%s: %s", call_id, e)
            return tts_pb2.SynthesizeResponse()

        minio_key = ""
        if result.audio:
            try:
                from ttsadapter.store.storage import build_object_key, upload_audio_async
                key = build_object_key(prefix="tts", call_id=call_id)
                if key:
                    asyncio.create_task(upload_audio_async(result.audio, key))
                    minio_key = key
            except Exception:
                pass

        return tts_pb2.SynthesizeResponse(
            audio=result.audio or b"",
            content_type=result.content_type or "audio/wav",
            duration_ms=result.duration_ms or 0,
            minio_key=minio_key,
        )


async def serve_grpc(engine: TTSEngine, port: int = GRPC_PORT):
    """启动 gRPC 服务（与 FastAPI HTTP 共存）。"""
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    tts_pb2_grpc.add_TTSServiceServicer_to_server(TTSGrpcServicer(engine), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    logger.info("gRPC TTS server started on port %d", port)
    return server
