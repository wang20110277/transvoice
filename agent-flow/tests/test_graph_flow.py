import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import HumanMessage, AIMessage
from graph.flow import CallGraphState, create_call_graph


def _make_state(**overrides) -> dict:
    base = {
        "call_id": "test-call-123",
        "biz_type": "customer_service",
        "user_key": "user_abc",
        "user_input": "我想咨询一下",
        "asr_minio_key": "asr/20260514/test.wav",
        "identity": None,
        "credit_result": None,
        "memory_block": "",
        "rag_block": "",
        "rag_retry_count": 0,
        "rag_query": "",
        "llm_action": None,
        "tts_minio_key": None,
        "tts_audio": None,
        "chat_history": [],
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_route_credit_query_only_marketing():
    from graph.flow import should_query_credit
    assert should_query_credit(_make_state(biz_type="marketing")) == "credit_query"
    assert should_query_credit(_make_state(biz_type="customer_service")) == "llm_decide"
    assert should_query_credit(_make_state(biz_type="collection")) == "llm_decide"


@pytest.mark.asyncio
async def test_receive_asr_node():
    from graph.flow import receive_asr_node
    mock_history = MagicMock()
    mock_history.aget_messages = AsyncMock(return_value=[HumanMessage(content="hi")])
    with patch("graph.flow.get_chat_history", return_value=mock_history):
        result = await receive_asr_node(_make_state())
    assert result["user_input"] == "我想咨询一下"
    assert result["asr_minio_key"] == "asr/20260514/test.wav"
    assert len(result["chat_history"]) == 1


@pytest.mark.asyncio
async def test_receive_asr_node_history_load_failure():
    from graph.flow import receive_asr_node
    with patch("graph.flow.get_chat_history", side_effect=Exception("redis down")):
        result = await receive_asr_node(_make_state())
    assert result["chat_history"] == []


@pytest.mark.asyncio
async def test_mcp_identity_node():
    from graph.flow import mcp_identity_node
    mock_mcp = MagicMock()
    mock_mcp.query_user_identity = AsyncMock(return_value=MagicMock(
        user_id="u1", name_masked="张*", gender="male", verified=True
    ))
    with patch("graph.flow._mcp_client", mock_mcp):
        result = await mcp_identity_node(_make_state())
    assert result["identity"] is not None
    assert result["identity"]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_mcp_identity_failure_non_blocking():
    from graph.flow import mcp_identity_node
    mock_mcp = MagicMock()
    mock_mcp.query_user_identity = AsyncMock(side_effect=Exception("timeout"))
    with patch("graph.flow._mcp_client", mock_mcp):
        result = await mcp_identity_node(_make_state())
    assert result["identity"] is None


@pytest.mark.asyncio
async def test_credit_query_node():
    from graph.flow import credit_query_node
    mock_mcp = MagicMock()
    mock_mcp.query_credit_profile = AsyncMock(return_value=MagicMock(
        user_id="u1", credit_qualified=True, risk_level="low", details={}
    ))
    with patch("graph.flow._mcp_client", mock_mcp):
        result = await credit_query_node(_make_state(identity={"user_id": "u1"}))
    assert result["credit_result"]["credit_qualified"] is True


@pytest.mark.asyncio
async def test_tts_synthesize_node_success():
    from graph.flow import tts_synthesize_node
    mock_tts = MagicMock()
    mock_tts.synthesize = AsyncMock(return_value={
        "audio": "dGVzdA==", "minio_key": "tts/20260514/test.wav", "content_type": "audio/wav"
    })
    mock_history = MagicMock()
    mock_action = MagicMock()
    mock_action.text = "您好，有什么可以帮您？"
    with patch("graph.flow._tts_client", mock_tts), \
         patch("graph.flow.get_chat_history", return_value=mock_history), \
         patch("graph.flow.save_turn", new=AsyncMock()):
        result = await tts_synthesize_node(_make_state(llm_action=mock_action))
    assert result["tts_audio"] == "dGVzdA=="
    assert result["tts_minio_key"] == "tts/20260514/test.wav"


@pytest.mark.asyncio
async def test_tts_synthesize_node_failure_graceful():
    from graph.flow import tts_synthesize_node
    mock_tts = MagicMock()
    mock_tts.synthesize = AsyncMock(return_value=None)
    mock_action = MagicMock()
    mock_action.text = "您好"
    with patch("graph.flow._tts_client", mock_tts), \
         patch("graph.flow.get_chat_history", side_effect=Exception("redis down")):
        result = await tts_synthesize_node(_make_state(llm_action=mock_action))
    assert result["tts_audio"] is None
    assert result["tts_minio_key"] is None


@pytest.mark.asyncio
async def test_create_call_graph_compiles():
    graph = create_call_graph()
    assert graph is not None
    assert hasattr(graph, "ainvoke")
