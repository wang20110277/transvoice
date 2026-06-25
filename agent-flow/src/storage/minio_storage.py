"""MinIO 音频归档 — ASR 输入音频和 TTS 输出音频的异步上传。"""
import asyncio
import io
import logging
import uuid
from datetime import datetime, timedelta

from config import settings
from minio import Minio

logger = logging.getLogger(__name__)


def _client() -> Minio | None:
    if not settings.minio_endpoint:
        return None
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def _ensure_bucket(client: Minio):
    if not client.bucket_exists(settings.minio_bucket):
        client.make_bucket(settings.minio_bucket)


def build_object_key(prefix: str = "audio", call_id: str = "", suffix: str = "") -> str | None:
    if not settings.minio_endpoint:
        return None
    date_str = datetime.now().strftime("%Y%m%d")
    name = call_id or uuid.uuid4().hex
    if suffix:
        name = f"{name}_{suffix}"
    return f"{prefix}/{date_str}/{name}.wav"


def upload_audio(audio_bytes: bytes, object_name: str) -> bool:
    """上传音频到 MinIO。成功 True；未配置客户端或上传失败 False（失败仅记日志，不抛——
    由调用方据返回值判定，避免吞异常后误报成功、写入指向空文件的 artifact）。
    """
    client = _client()
    if client is None:
        return False
    try:
        _ensure_bucket(client)
        client.put_object(
            settings.minio_bucket,
            object_name,
            io.BytesIO(audio_bytes),
            length=len(audio_bytes),
            content_type="audio/wav",
        )
        logger.info("Uploaded audio to MinIO: %s/%s", settings.minio_bucket, object_name)
        return True
    except Exception as e:
        logger.error("Failed to upload audio to MinIO: %s", e)
        return False


async def upload_audio_async(audio_bytes: bytes, object_name: str) -> bool:
    return await asyncio.to_thread(upload_audio, audio_bytes, object_name)


async def upload_recording(
    call_id: str, wav_bytes: bytes, biz_type: str, tenant_id: str,
) -> str | None:
    """上传整通录音 wav 到 MinIO。返回 object key；未配置或上传失败返回 None。

    失败必须返回 None（而非 key）：否则调用方误判成功、写入指向空文件的 artifact
    （_archive_recording 与手动归档接口都依赖 None 判定跳过 / 返回 502）。
    """
    if not settings.minio_endpoint:
        return None
    key = build_object_key(prefix="recordings", call_id=call_id)
    if key is None:
        return None
    ok = await upload_audio_async(wav_bytes, key)
    return key if ok else None


def presigned_get_url(object_key: str, expiry: int = 3600) -> str | None:
    """生成 MinIO presigned GET URL（默认 1h）。MinIO 未配置或异常返回 None。"""
    client = _client()
    if client is None:
        return None
    try:
        return client.presigned_get_object(
            settings.minio_bucket, object_key, expires=timedelta(seconds=expiry),
        )
    except Exception as e:
        logger.error("presigned_get_url failed: %s", e)
        return None
