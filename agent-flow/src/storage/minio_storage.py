"""MinIO 音频归档 — ASR 输入音频和 TTS 输出音频的异步上传。"""
import asyncio
import io
import logging
import os
import struct
import uuid
from datetime import datetime

from minio import Minio

logger = logging.getLogger(__name__)

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "audio-archive")
MINIO_SECURE = os.environ.get("MINIO_SECURE", "false").lower() == "true"


def _client() -> Minio | None:
    if not MINIO_ENDPOINT:
        return None
    return Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE)


def _ensure_bucket(client: Minio):
    if not client.bucket_exists(MINIO_BUCKET):
        client.make_bucket(MINIO_BUCKET)


def build_object_key(prefix: str = "audio", call_id: str = "", suffix: str = "") -> str | None:
    if not MINIO_ENDPOINT:
        return None
    date_str = datetime.now().strftime("%Y%m%d")
    name = call_id or uuid.uuid4().hex
    if suffix:
        name = f"{name}_{suffix}"
    return f"{prefix}/{date_str}/{name}.wav"


def wrap_wav_header(pcm: bytes, sample_rate: int = 8000, channels: int = 1, bits: int = 16) -> bytes:
    """为原始 PCM 数据添加 44 字节 WAV 头。"""
    data_size = len(pcm)
    fmt_chunk = struct.pack('<4sIHHIIHH',
        b'fmt ', 16, 1, channels, sample_rate,
        sample_rate * channels * bits // 8,
        channels * bits // 8, bits)
    data_chunk = struct.pack('<4sI', b'data', data_size)
    riff_header = struct.pack('<4sI', b'RIFF', 36 + data_size)
    return riff_header + b'WAVE' + fmt_chunk + data_chunk + pcm


def upload_audio(audio_bytes: bytes, object_name: str) -> None:
    client = _client()
    if client is None:
        return
    try:
        _ensure_bucket(client)
        client.put_object(
            MINIO_BUCKET,
            object_name,
            io.BytesIO(audio_bytes),
            length=len(audio_bytes),
            content_type="audio/wav",
        )
        logger.info("Uploaded audio to MinIO: %s/%s", MINIO_BUCKET, object_name)
    except Exception as e:
        logger.error("Failed to upload audio to MinIO: %s", e)


async def upload_audio_async(audio_bytes: bytes, object_name: str) -> None:
    await asyncio.to_thread(upload_audio, audio_bytes, object_name)


async def save_turn_audio(
    upstream_pcm: bytes,
    downstream_pcm: bytes,
    call_id: str,
    turn: int,
    upstream_sr: int = 8000,
    downstream_sr: int = 8000,
) -> None:
    """保存一轮对话的上行（用户）和下行（AI）音频到 MinIO。

    upstream_pcm: 用户音频原始 PCM int16 (8kHz)
    downstream_pcm: AI 回复音频原始 PCM int16 (downstream_sr Hz)
    """
    if not MINIO_ENDPOINT:
        return
    suffix = f"t{turn}"

    if upstream_pcm:
        wav = wrap_wav_header(upstream_pcm, sample_rate=upstream_sr)
        key = build_object_key(prefix="upstream", call_id=call_id, suffix=suffix)
        if key:
            asyncio.create_task(upload_audio_async(wav, key))

    if downstream_pcm:
        wav = wrap_wav_header(downstream_pcm, sample_rate=downstream_sr)
        key = build_object_key(prefix="downstream", call_id=call_id, suffix=suffix)
        if key:
            asyncio.create_task(upload_audio_async(wav, key))
