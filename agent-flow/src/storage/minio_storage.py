"""MinIO 音频归档 — ASR 输入音频和 TTS 输出音频的异步上传。"""
import asyncio
import io
import logging
import os
import uuid
from datetime import datetime, timedelta

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


async def upload_recording(
    call_id: str, wav_bytes: bytes, biz_type: str, tenant_id: str,
) -> str | None:
    """上传整通录音 wav 到 MinIO。返回 object key；MinIO 未配置返回 None。"""
    if not MINIO_ENDPOINT:
        return None
    key = build_object_key(prefix="recordings", call_id=call_id)
    if key is None:
        return None
    await upload_audio_async(wav_bytes, key)
    return key


def presigned_get_url(object_key: str, expiry: int = 3600) -> str | None:
    """生成 MinIO presigned GET URL（默认 1h）。MinIO 未配置或异常返回 None。"""
    client = _client()
    if client is None:
        return None
    try:
        return client.presigned_get_object(
            MINIO_BUCKET, object_key, expires=timedelta(seconds=expiry),
        )
    except Exception as e:
        logger.error("presigned_get_url failed: %s", e)
        return None
