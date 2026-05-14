import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from graph_flow import CallGraphState, create_call_graph


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
        "llm_action": None,
        "tts_minio_key": None,
        "tts_audio": None,
        "turn_count": 0,
        "turn_history": [],
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_route_credit_query_only_marketing():
    from graph_flow import should_query_credit
    assert should_query_credit(_make_state(biz_type="marketing")) == "credit_query"
    assert should_query_credit(_make_state(biz_type="customer_service")) == "recall_memory"
    assert should_query_credit(_make_state(biz_type="collection")) == "recall_memory"


@pytest.mark.asyncio
async def test_receive_asr_node():
    from graph_flow import receive_asr_node
    state = _make_state()
    with patch("graph_flow.load_context", new=AsyncMock(return_value={"turn_history": [{"role": "user", "text": "hi"}], "turn_count": 1, "identity": None})):
        result = await receive_asr_node(state)
    assert result["user_input"] == "我想咨询一下"
    assert result["asr_minio_key"] == "asr/20260514/test.wav"
    assert result["turn_count"] == 1
    assert len(result["turn_history"]) == 1


@pytest.mark.asyncio
async def test_mcp_identity_node():
    from graph_flow import mcp_identity_node
    mock_mcp = MagicMock()
    mock_mcp.query_user_identity = AsyncMock(return_value=MagicMock(
        user_id="u1", name_masked="张*", gender="male", verified=True
    ))
    with patch("graph_flow._mcp_client", mock_mcp):
        result = await mcp_identity_node(_make_state())
    assert result["identity"] is not None
    assert result["identity"]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_mcp_identity_failure_non_blocking():
    from graph_flow import mcp_identity_node
    mock_mcp = MagicMock()
    mock_mcp.query_user_identity = AsyncMock(side_effect=Exception("timeout"))
    with patch("graph_flow._mcp_client", mock_mcp):
        result = await mcp_identity_node(_make_state())
    assert result["identity"] is None


@pytest.mark.asyncio
async def test_credit_query_node():
    from graph_flow import credit_query_node
    mock_mcp = MagicMock()
    mock_mcp.query_credit_profile = AsyncMock(return_value=MagicMock(
        user_id="u1", credit_qualified=True, risk_level="low", details={}
    ))
    with patch("graph_flow._mcp_client", mock_mcp):
        result = await credit_query_node(_make_state(identity={"user_id": "u1"}))
    assert result["credit_result"]["credit_qualified"] is True


@pytest.mark.asyncio
async def test_tts_synthesize_node_success():
    from graph_flow import tts_synthesize_node
    mock_tts = MagicMock()
    mock_tts.synthesize = AsyncMock(return_value={
        "audio": "dGVzdA==", "minio_key": "tts/20260514/test.wav", "content_type": "audio/wav"
    })
    mock_action = MagicMock()
    mock_action.text = "您好，有什么可以帮您？"
    with patch("graph_flow._tts_client", mock_tts), \
         patch("graph_flow.save_context", new=AsyncMock()):
        result = await tts_synthesize_node(_make_state(llm_action=mock_action, turn_count=0))
    assert result["tts_audio"] == "dGVzdA=="
    assert result["tts_minio_key"] == "tts/20260514/test.wav"
    assert result["turn_count"] == 1


@pytest.mark.asyncio
async def test_tts_synthesize_node_failure_graceful():
    from graph_flow import tts_synthesize_node
    mock_tts = MagicMock()
    mock_tts.synthesize = AsyncMock(return_value=None)
    mock_action = MagicMock()
    mock_action.text = "您好"
    with patch("graph_flow._tts_client", mock_tts), \
         patch("graph_flow.save_context", new=AsyncMock()):
        result = await tts_synthesize_node(_make_state(llm_action=mock_action))
    assert result["tts_audio"] is None
    assert result["tts_minio_key"] is None
    assert result["turn_count"] == 1


@pytest.mark.asyncio
async def test_create_call_graph_compiles():
    graph = create_call_graph()
    assert graph is not None
    assert hasattr(graph, "ainvoke")
