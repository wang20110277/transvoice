import pytest
from unittest.mock import AsyncMock, MagicMock

from clients.mcp import MCPClient, IdentityResult, CreditResult


def _make_mcp_text_response(data: dict) -> list:
    import json
    return [{"type": "text", "text": json.dumps(data)}]


@pytest.mark.asyncio
async def test_query_user_identity_success():
    client = MCPClient("http://localhost:9090/mcp/")
    mock_tool = AsyncMock()
    mock_tool.name = "user_identity_query"
    mock_tool.ainvoke.return_value = _make_mcp_text_response({
        "user_id": "u123",
        "phone_masked": "138****5678",
        "id_card_last_four": "1234",
    })
    client._tools = {"user_identity_query": mock_tool}

    result = await client.query_user_identity("13800005678", "collection")
    assert isinstance(result, IdentityResult)
    assert result.user_id == "u123"
    assert result.phone_masked == "138****5678"
    assert result.id_card_last_four == "1234"
    mock_tool.ainvoke.assert_awaited_once_with({
        "phone": "13800005678",
        "biz_type": "collection",
    })


@pytest.mark.asyncio
async def test_query_credit_profile_success():
    client = MCPClient("http://localhost:9090/mcp/")
    mock_tool = AsyncMock()
    mock_tool.name = "user_credit_query"
    mock_tool.ainvoke.return_value = _make_mcp_text_response({
        "user_id": "u123",
        "credit_qualified": True,
        "risk_level": "low",
    })
    client._tools = {"user_credit_query": mock_tool}

    result = await client.query_credit_profile("u123")
    assert isinstance(result, CreditResult)
    assert result.credit_qualified is True
    assert result.risk_level == "low"
    mock_tool.ainvoke.assert_awaited_once_with({
        "user_id": "u123",
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
