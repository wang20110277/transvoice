import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from clients.mcp import MCPClient, IdentityResult, CreditResult


@pytest.mark.asyncio
async def test_query_user_identity_success():
    client = MCPClient("http://localhost:9090/mcp/")
    mock_tool = AsyncMock()
    mock_tool.name = "user_identity_query"
    mock_tool.ainvoke.return_value = {
        "user_id": "u123",
        "name_masked": "张*",
        "id_last_four": "1234",
        "gender": "male",
    }
    client._tools = {"user_identity_query": mock_tool}

    result = await client.query_user_identity("phone_hash_123", "collection")
    assert isinstance(result, IdentityResult)
    assert result.user_id == "u123"
    assert result.verified is True
    mock_tool.ainvoke.assert_awaited_once_with({
        "phone_hash": "phone_hash_123",
        "biz_type": "collection",
    })


@pytest.mark.asyncio
async def test_query_credit_profile_success():
    client = MCPClient("http://localhost:9090/mcp/")
    mock_tool = AsyncMock()
    mock_tool.name = "user_credit_query"
    mock_tool.ainvoke.return_value = {
        "credit_qualified": True,
        "risk_level": "low",
        "details": {"score": 750},
    }
    client._tools = {"user_credit_query": mock_tool}

    result = await client.query_credit_profile("u123", "phone_hash_123")
    assert isinstance(result, CreditResult)
    assert result.credit_qualified is True
    assert result.risk_level == "low"
    mock_tool.ainvoke.assert_awaited_once_with({
        "user_id": "u123",
        "phone_hash": "phone_hash_123",
    })


@pytest.mark.asyncio
async def test_call_tool_not_found():
    client = MCPClient("http://localhost:9090/mcp/")
    client._tools = {}
    with pytest.raises(RuntimeError, match="MCP tool not found"):
        await client.query_user_identity("phone_hash", "marketing")


@pytest.mark.asyncio
async def test_health_check_with_tools():
    client = MCPClient("http://localhost:9090/mcp/")
    client._tools = {"user_identity_query": MagicMock()}
    assert await client.health_check() is True


@pytest.mark.asyncio
async def test_health_check_empty():
    client = MCPClient("http://localhost:9090/mcp/")
    client._tools = {}
    assert await client.health_check() is False
