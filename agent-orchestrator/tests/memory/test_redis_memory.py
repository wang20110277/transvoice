import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from memory.redis_memory import RedisHotMemory


@pytest.fixture
def memory():
    store = {}

    mock_client = AsyncMock()

    async def fake_hset(key, field, value):
        store.setdefault(key, {})[field] = value

    async def fake_hget(key, field):
        return store.get(key, {}).get(field)

    async def fake_hgetall(key):
        return store.get(key, {})

    mock_client.hset.side_effect = fake_hset
    mock_client.hget.side_effect = fake_hget
    mock_client.hgetall.side_effect = fake_hgetall
    mock_client.expire.return_value = True

    with patch("memory.redis_memory.aioredis.from_url", return_value=mock_client):
        yield RedisHotMemory("redis://localhost:6379/0")


@pytest.mark.asyncio
async def test_set_and_get(memory):
    await memory.set_fact("customer_service", "user1:h1", "pref_contact_time", "周末上午")
    fact = await memory.get_fact("customer_service", "user1:h1", "pref_contact_time")
    assert fact == "周末上午"


@pytest.mark.asyncio
async def test_get_all_facts(memory):
    await memory.set_fact("marketing", "u1:h1", "do_not_call", "true")
    facts = await memory.get_all_facts("marketing", "u1:h1")
    assert isinstance(facts, dict)
    assert facts.get("do_not_call") == "true"


@pytest.mark.asyncio
async def test_set_do_not_call(memory):
    await memory.set_do_not_call("marketing", "u1:h1")
    fact = await memory.get_fact("marketing", "u1:h1", "do_not_call")
    assert fact == "true"
