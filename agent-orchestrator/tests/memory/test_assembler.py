import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from memory.assembler import MemoryAssembler


@pytest.mark.asyncio
async def test_assemble_returns_string():
    with patch("memory.assembler.RedisHotMemory") as mock_redis_cls:
        mock_redis = MagicMock()
        mock_redis.get_all_facts = AsyncMock(return_value={"pref": "周末"})
        mock_redis_cls.return_value = mock_redis

        with patch("memory.assembler.get_recent_facts", return_value=[]):
            assembler = MemoryAssembler()
            result = await assembler.assemble("marketing", "u1:h1")
            assert isinstance(result, str)
            assert "周末" in result
