"""gRPC ASR service — streaming audio recognition alongside FastAPI HTTP."""
import asyncio
import logging
import os
from concurrent import futures

import grpc

from asradapter.proto import asr_pb2, asr_pb2_grpc
from asradapter.base import ASREngine

logger = logging.getLogger(__name__)

GRPC_PORT = int(os.environ.get("ASR_GRPC_PORT", "50051"))


class ASRGrpcServicer(asr_pb2_grpc.ASRServiceServicer):
    """gRPC ASR 服务实现 — 客户端流式语音识别。

    协议: 客户端逐帧发送音频（首帧为 RecognitionConfig），流关闭后返回识别结果。
    端口: 50051（可通过 ASR_GRPC_PORT 环境变量覆盖）
    """

    def __init__(self, engine: ASREngine):
        self._engine = engine

    async def StreamingRecognize(self, request_iterator, context):
        """接收流式音频帧，累积后批量识别。流关闭时返回最终结果，并异步上传 MinIO。"""
        call_id = ""
        language = "zh"
        audio_chunks: list[bytes] = []

        async for request in request_iterator:
            if request.HasField("config"):
                call_id = request.config.call_id
                language = request.config.language or "zh"
            elif request.audio_chunk:
                audio_chunks.append(request.audio_chunk)

        audio_bytes = b"".join(audio_chunks)
        logger.info("[gRPC] StreamingRecognize call_id=%s size=%d bytes", call_id, len(audio_bytes))
        if not audio_bytes:
            return asr_pb2.StreamingRecognizeResponse(
                text="", confidence=0.0, is_final=True,
            )

        params = {"call_id": call_id, "language": language}
        try:
            result = await self._engine.recognize(audio_bytes, params)
        except Exception as e:
            logger.error("gRPC ASR recognize error call_id=%s: %s", call_id, e)
            return asr_pb2.StreamingRecognizeResponse(
                text="", confidence=0.0, is_final=True,
            )

        return asr_pb2.StreamingRecognizeResponse(
            text=result.text,
            confidence=result.confidence,
            is_final=True,
        )


async def serve_grpc(engine: ASREngine, port: int = GRPC_PORT):
    """启动 gRPC 服务（与 FastAPI HTTP 共存）。"""
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    asr_pb2_grpc.add_ASRServiceServicer_to_server(ASRGrpcServicer(engine), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    logger.info("gRPC ASR server started on port %d", port)
    return server
