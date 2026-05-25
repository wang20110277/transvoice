"""TTS gRPC client — sends text, receives synthesized audio."""
import logging

import grpc

from clients.tts_grpc import tts_pb2, tts_pb2_grpc

logger = logging.getLogger(__name__)


class TTSGrpcClient:
    """gRPC client for TTS synthesis."""

    def __init__(self, target: str, timeout: float = 120.0):
        self._target = target
        self._timeout = timeout
        self._channel: grpc.aio.Channel | None = None

    async def start(self) -> None:
        self._channel = grpc.aio.insecure_channel(self._target)

    async def close(self) -> None:
        if self._channel:
            await self._channel.close()
            self._channel = None

    async def synthesize_raw(self, text: str, call_id: str, biz_type: str) -> bytes | None:
        """Synthesize text to audio via gRPC. Returns raw WAV bytes."""
        if self._channel is None:
            return None
        try:
            stub = tts_pb2_grpc.TTSServiceStub(self._channel)
            request = tts_pb2.SynthesizeRequest(
                text=text, call_id=call_id, biz_type=biz_type,
            )
            response = await stub.Synthesize(request, timeout=self._timeout)
            return response.audio if response.audio else None
        except Exception as e:
            logger.error("TTS gRPC synthesize failed call_id=%s: %s", call_id, e)
            return None

    async def synthesize(self, text: str, call_id: str, biz_type: str) -> dict | None:
        """Synthesize via gRPC, returns dict compatible with TTSClient."""
        if self._channel is None:
            return None
        try:
            import base64
            stub = tts_pb2_grpc.TTSServiceStub(self._channel)
            request = tts_pb2.SynthesizeRequest(
                text=text, call_id=call_id, biz_type=biz_type,
            )
            response = await stub.Synthesize(request, timeout=self._timeout)
            if not response.audio:
                return None
            return {
                "audio": base64.b64encode(response.audio).decode("ascii"),
                "content_type": response.content_type or "audio/wav",
                "duration_ms": response.duration_ms,
                "minio_key": response.minio_key or None,
            }
        except Exception as e:
            logger.error("TTS gRPC synthesize failed call_id=%s: %s", call_id, e)
            return None
