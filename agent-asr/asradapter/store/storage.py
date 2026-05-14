import io
import logging
import os
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


def upload_audio(audio_bytes: bytes, prefix: str = "asr", call_id: str = "") -> str | None:
    client = _client()
    if client is None:
        return None

    date_str = datetime.now().strftime("%Y%m%d")
    object_name = f"{prefix}/{date_str}/{call_id or uuid.uuid4().hex}.wav"

    try:
        _ensure_bucket(client)
        client.put_object(
            MINIO_BUCKET,
            object_name,
            io.BytesIO(audio_bytes),
            length=len(audio_bytes),
            content_type="audio/wav",
        )
        logger.info(f"Uploaded audio to MinIO: {MINIO_BUCKET}/{object_name}")
        return object_name
    except Exception as e:
        logger.error(f"Failed to upload audio to MinIO: {e}")
        return None
