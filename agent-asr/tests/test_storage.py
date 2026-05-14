import pytest
from unittest.mock import patch, MagicMock
from adapter import storage


def test_upload_audio_no_endpoint():
    with patch.dict("os.environ", {}, clear=False):
        # Remove MINIO_ENDPOINT if set
        if "MINIO_ENDPOINT" in __import__("os").environ:
            del __import__("os").environ["MINIO_ENDPOINT"]
    import importlib
    importlib.reload(storage)
    result = storage.upload_audio(b"fake_audio")
    assert result is None


def test_upload_audio_success():
    with patch.dict("os.environ", {"MINIO_ENDPOINT": "localhost:9000", "MINIO_ACCESS_KEY": "key", "MINIO_SECRET_KEY": "secret", "MINIO_BUCKET": "test-bucket"}):
        import importlib
        importlib.reload(storage)

        mock_client = MagicMock()
        mock_client.bucket_exists.return_value = True

        with patch("adapter.storage._client", return_value=mock_client):
            result = storage.upload_audio(b"fake_audio", prefix="asr", call_id="call123")

        assert result is not None
        assert result.startswith("asr/")
        assert "call123" in result
        assert result.endswith(".wav")
        mock_client.put_object.assert_called_once()


def test_upload_audio_minio_error():
    with patch.dict("os.environ", {"MINIO_ENDPOINT": "localhost:9000", "MINIO_ACCESS_KEY": "key", "MINIO_SECRET_KEY": "secret"}):
        import importlib
        importlib.reload(storage)

        mock_client = MagicMock()
        mock_client.bucket_exists.return_value = True
        mock_client.put_object.side_effect = Exception("minio error")

        with patch("adapter.storage._client", return_value=mock_client):
            result = storage.upload_audio(b"fake_audio")

        assert result is None
