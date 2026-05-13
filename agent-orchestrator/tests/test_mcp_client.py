import pytest
from unittest.mock import AsyncMock, patch
from mcp_client import MCPClient, IdentityResult, CreditResult


@pytest.mark.asyncio
async def test_query_user_identity_success():
    client = MCPClient("http://localhost:9090")
    with patch.object(client, "_call_mcp", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = {
            "user_id": "u123",
            "name_masked": "张*",
            "id_last_four": "1234",
            "gender": "male",
        }
        result = await client.query_user_identity("phone_hash_123", "collection")
        assert isinstance(result, IdentityResult)
        assert result.user_id == "u123"
        assert result.verified is True
