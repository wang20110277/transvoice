import pytest
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport
from main import app


@pytest.mark.asyncio
async def test_healthz():
    with patch("main._initialized", True):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_healthz_initializing():
    with patch("main._initialized", False):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200
            assert resp.json()["status"] == "initializing"
