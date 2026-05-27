"""ASR gRPC streaming client — sends audio frames incrementally to agent-asr."""
import asyncio
import logging

import grpc

from clients.asr_grpc import asr_pb2, asr_pb2_grpc

logger = logging.getLogger(__name__)


class ASRGrpcClient:
    """gRPC client for ASR streaming recognition."""

    def __init__(self, target: str, timeout: float = 15.0):
        self._target = target
        self._timeout = timeout
        self._channel: grpc.aio.Channel | None = None

    async def start(self) -> None:
        self._channel = grpc.aio.insecure_channel(self._target)

    async def close(self) -> None:
        if self._channel:
            await self._channel.close()
            self._channel = None

    async def recognize(self, audio_bytes: bytes, call_id: str) -> dict | None:
        """Batch fallback: send entire audio as one chunk via gRPC."""
        if self._channel is None:
            return None
        try:
            stub = asr_pb2_grpc.ASRServiceStub(self._channel)

            async def _request_iterator():
                yield asr_pb2.StreamingRecognizeRequest(
                    config=asr_pb2.RecognitionConfig(call_id=call_id)
                )
                yield asr_pb2.StreamingRecognizeRequest(audio_chunk=audio_bytes)

            response = await stub.StreamingRecognize(_request_iterator(), timeout=self._timeout)
            return {
                "text": response.text,
                "confidence": response.confidence,
                "is_final": response.is_final,
                "minio_key": response.minio_key or None,
            }
        except Exception as e:
            logger.error("ASR gRPC recognize failed call_id=%s: %s", call_id, e)
            return None

    def create_stream(self, call_id: str, **kwargs) -> "ASRStream | None":
        """Create a streaming context for incremental audio upload."""
        if self._channel is None:
            return None
        return ASRStream(self._channel, call_id, self._timeout)


class ASRStream:
    """Manages an active gRPC streaming call to the ASR service.

    Usage:
        stream = client.create_stream(call_id)
        await stream.start()
        stream.send_audio(frame_bytes)  # call per frame
        result = await stream.finish()  # on end-of-speech
    """

    def __init__(self, channel: grpc.aio.Channel, call_id: str, timeout: float):
        self._channel = channel
        self._call_id = call_id
        self._timeout = timeout
        self._stub = asr_pb2_grpc.ASRServiceStub(channel)
        self._queue: asyncio.Queue | None = None
        self._rpc = None

    async def start(self) -> None:
        self._queue = asyncio.Queue()
        self._rpc = self._stub.StreamingRecognize(
            self._request_iterator(), timeout=self._timeout,
        )

    async def _request_iterator(self):
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item

    def send_audio(self, chunk: bytes) -> None:
        """Send an audio chunk. Call as frames arrive from JitterBuffer."""
        if self._queue is None:
            return
        if self._queue.empty():
            self._queue.put_nowait(asr_pb2.StreamingRecognizeRequest(
                config=asr_pb2.RecognitionConfig(call_id=self._call_id),
            ))
        self._queue.put_nowait(asr_pb2.StreamingRecognizeRequest(audio_chunk=chunk))

    async def finish(self) -> dict | None:
        """Signal end-of-stream and wait for the final ASR result."""
        if self._queue is not None:
            self._queue.put_nowait(None)
        try:
            if self._rpc is None:
                return None
            response = await self._rpc
            return {
                "text": response.text,
                "confidence": response.confidence,
                "is_final": response.is_final,
                "minio_key": response.minio_key or None,
            }
        except Exception as e:
            logger.error("ASR gRPC stream finish failed call_id=%s: %s", self._call_id, e)
            return None

    async def cancel(self) -> None:
        if self._queue is not None:
            self._queue.put_nowait(None)
        if self._rpc is not None:
            self._rpc.cancel()
