import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
from main import app, SpeechRequest


def _mock_graph_result():
    from llm.service import LLMAction
    action = LLMAction(action="say", text="您好，有什么可以帮您？")
    return {
        "call_id": "test-call-123",
        "llm_action": action,
        "tts_audio": "dGVzdA==",
        "tts_minio_key": "tts/20260514/test.wav",
    }


@pytest.mark.asyncio
async def test_healthz():
    with patch("main._initialized", True):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_call_speech():
    with patch("main._graph") as mock_graph, \
         patch("main._initialized", True):
        mock_graph.ainvoke = AsyncMock(return_value=_mock_graph_result())

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/call/speech", json={
                "call_id": "test-call-123",
                "biz_type": "customer_service",
                "user_key": "user_abc",
                "text": "我想咨询一下",
                "minio_key": "asr/20260514/test.wav",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["action"] == "say"
            assert data["text"] == "您好，有什么可以帮您？"
            assert data["tts_audio"] == "dGVzdA=="
