# Orchestrator LangGraph Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor agent-orchestrator from ESL-event-driven to a FastAPI HTTP service with a clean 7-node LangGraph pipeline, deleting all deprecated/unused modules.

**Architecture:** FastAPI receives ASR results via `POST /call/speech`, runs a 7-node LangGraph (receive_asr → mcp_identity → [credit_query] → recall_memory → rag_retrieve → llm_decide → tts_synthesize), and returns TTS audio. Redis replaces in-memory CallStateManager for conversation context. No direct FreeSWITCH connection.

**Tech Stack:** FastAPI, LangGraph 1.2, LangChain 1.2, Redis, SQLAlchemy 2.0 async, pgvector, httpx, pydantic-settings

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| **DELETE** | `fs_esl.py` | ESL connection — no FreeSWITCH |
| **DELETE** | `fs_actions.py` | ESL commands — no FreeSWITCH |
| **DELETE** | `event_handlers.py` | ESL event dispatch — no event loop |
| **DELETE** | `call_state.py` | In-memory state — replaced by Redis |
| **DELETE** | `compliance.py` | Compliance check — removed per spec |
| **DELETE** | `llm_base.py` | Deprecated |
| **DELETE** | `rag_retriever.py` | Deprecated |
| **DELETE** | `memory/pg_facts.py` | Deprecated |
| **DELETE** | `memory/pg_vector.py` | Deprecated |
| **DELETE** | `storage/db_pg.py` | Deprecated |
| **DELETE** | `llm_engines/` directory | Deprecated |
| **DELETE** | `tests/test_llm_base.py` | Tests deprecated code |
| **DELETE** | `tests/test_rag_retriever.py` | Tests deprecated code |
| **DELETE** | `tests/test_prompt_builder.py` | Broken (wrong import) |
| **DELETE** | `tests/test_event_handlers.py` | ESL removed |
| **DELETE** | `tests/test_fs_actions.py` | ESL removed |
| **REWRITE** | `main.py` | FastAPI HTTP service + lifespan |
| **REWRITE** | `graph_flow.py` | 7-node LangGraph pipeline |
| **MODIFY** | `config.py` | Remove ESL config, add TTS adapter URL |
| **CREATE** | `tts_client.py` | TTS adapter HTTP client |
| **CREATE** | `tests/test_tts_client.py` | TTS client tests |
| **CREATE** | `tests/test_main.py` | FastAPI endpoint tests |
| **MODIFY** | `requirements.txt` | Remove python-ESL |
| **KEEP** | `mcp_client.py` | No changes |
| **KEEP** | `prompt_builder.py` | No changes |
| **KEEP** | `llm/service.py` | No changes |
| **KEEP** | `memory/assembler.py` | No changes |
| **KEEP** | `memory/store.py` | No changes |
| **KEEP** | `memory/redis_memory.py` | No changes |
| **KEEP** | `rag/retriever.py` | No changes |
| **KEEP** | `database.py` | No changes |
| **KEEP** | `db/models.py` | No changes |
| **KEEP** | `storage/repository.py` | No changes |

---

### Task 1: Delete deprecated and unused files

**Files:**
- Delete: `fs_esl.py`, `fs_actions.py`, `event_handlers.py`, `call_state.py`, `compliance.py`, `llm_base.py`, `rag_retriever.py`
- Delete: `memory/pg_facts.py`, `memory/pg_vector.py`, `storage/db_pg.py`
- Delete: `llm_engines/` directory (entire)
- Delete: `tests/test_llm_base.py`, `tests/test_rag_retriever.py`, `tests/test_prompt_builder.py`, `tests/test_event_handlers.py`, `tests/test_fs_actions.py`

- [ ] **Step 1: Delete files and verify imports break**

Run:
```bash
cd /Users/lindaw/Documents/aiphone/agent-orchestrator && \
rm -f fs_esl.py fs_actions.py event_handlers.py call_state.py compliance.py llm_base.py rag_retriever.py && \
rm -f memory/pg_facts.py memory/pg_vector.py storage/db_pg.py && \
rm -rf llm_engines/ && \
rm -f tests/test_llm_base.py tests/test_rag_retriever.py tests/test_prompt_builder.py tests/test_event_handlers.py tests/test_fs_actions.py
```

- [ ] **Step 2: Verify the remaining test files still reference existing modules**

Run:
```bash
cd /Users/lindaw/Documents/aiphone/agent-orchestrator && python -c "import config; import graph_flow; print('remaining imports OK')"
```
Expected: ImportError for graph_flow (it imports from `compliance` which was deleted) — this is expected, will be fixed in Task 3.

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "chore(orchestrator): 删除废弃模块 — ESL/compliance/旧LLM/旧RAG"
```

---

### Task 2: Create tts_client.py — TTS adapter HTTP client

**Files:**
- Create: `agent-orchestrator/tts_client.py`
- Create: `agent-orchestrator/tests/test_tts_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tts_client.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from tts_client import TTSClient


@pytest.mark.asyncio
async def test_synthesize_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "audio": "dGVzdA==",
        "minio_key": "tts/20260514/call123.wav",
        "content_type": "audio/wav",
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("tts_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        client = TTSClient(base_url="http://tts:8081")
        result = await client.synthesize("你好", "call123", "marketing")

    assert result["audio"] == "dGVzdA=="
    assert result["minio_key"] == "tts/20260514/call123.wav"


@pytest.mark.asyncio
async def test_synthesize_failure_returns_none():
    with patch("tts_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        client = TTSClient(base_url="http://tts:8081")
        result = await client.synthesize("你好", "call123", "marketing")

    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lindaw/Documents/aiphone/agent-orchestrator && python -m pytest tests/test_tts_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tts_client'`

- [ ] **Step 3: Write minimal implementation**

Create `tts_client.py`:

```python
"""TTS adapter HTTP client — 调用 TTS adapter 的 /tts/synthesize_json 端点"""
import logging
import httpx

logger = logging.getLogger(__name__)


class TTSClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def synthesize(self, text: str, call_id: str, biz_type: str) -> dict | None:
        """调用 TTS adapter 合成语音，返回 {audio, minio_key, content_type} 或 None"""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/tts/synthesize_json",
                    data={"text": text, "params": f'{{"call_id":"{call_id}","biz_type":"{biz_type}"}}'},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"TTS 合成失败 call_id={call_id}: {e}")
            return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/lindaw/Documents/aiphone/agent-orchestrator && python -m pytest tests/test_tts_client.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/lindaw/Documents/aiphone/agent-orchestrator && git add tts_client.py tests/test_tts_client.py && git commit -m "feat(orchestrator): 新增 TTS adapter HTTP client"
```

---

### Task 3: Rewrite graph_flow.py — 7-node LangGraph pipeline

**Files:**
- Rewrite: `agent-orchestrator/graph_flow.py`
- Create: `agent-orchestrator/tests/test_graph_flow.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_graph_flow.py`:

```python
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
    with patch("graph_flow.load_context", new=AsyncMock(return_value={"turn_history": [{"role": "user", "text": "hi"}], "turn_count": 1, "identity": None})) as mock_load:
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
         patch("graph_flow.save_context", new=AsyncMock()) as mock_save:
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lindaw/Documents/aiphone/agent-orchestrator && python -m pytest tests/test_graph_flow.py -v`
Expected: FAIL — imports broken (graph_flow still imports `compliance`)

- [ ] **Step 3: Rewrite graph_flow.py with 7-node pipeline**

Rewrite `graph_flow.py`:

```python
"""LangGraph 1.2 通话状态图 - 7 节点管线"""
import logging
import yaml
import os
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from pydantic import BaseModel

from llm.service import LLMAction, FALLBACK_ACTION_TEXT, get_llm_service
from rag.retriever import retrieve_scripts, build_rag_block
from prompt_builder import build_messages
from memory.assembler import MemoryAssembler
from mcp_client import MCPClient
from tts_client import TTSClient

logger = logging.getLogger(__name__)

# Module-level service instances — set by main.py lifespan
_assembler: MemoryAssembler | None = None
_mcp_client: MCPClient | None = None
_tts_client: TTSClient | None = None


def set_services(assembler: MemoryAssembler, mcp: MCPClient, tts: TTSClient) -> None:
    global _assembler, _mcp_client, _tts_client
    _assembler = assembler
    _mcp_client = mcp
    _tts_client = tts


class CallGraphState(TypedDict, total=False):
    call_id: str
    biz_type: str
    user_key: str
    user_input: str
    asr_minio_key: str | None
    identity: dict | None
    credit_result: dict | None
    memory_block: str
    rag_block: str
    llm_action: LLMAction | None
    tts_minio_key: str | None
    tts_audio: str | None
    turn_count: int
    turn_history: list[dict]


# ── Redis context helpers ──

async def load_context(call_id: str) -> dict:
    import redis.asyncio as aioredis
    from config import settings
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        raw = await r.get(f"cb:ctx:{call_id}")
        if raw:
            import json
            return json.loads(raw)
    except Exception as e:
        logger.warning(f"Redis context load fail for {call_id}: {e}")
    finally:
        await r.aclose()
    return {"turn_history": [], "turn_count": 0, "identity": None}


async def save_context(call_id: str, state: CallGraphState) -> None:
    import json
    import redis.asyncio as aioredis
    from config import settings
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        ctx = {
            "turn_history": state.get("turn_history", []),
            "turn_count": state.get("turn_count", 0),
            "identity": state.get("identity"),
        }
        await r.set(f"cb:ctx:{call_id}", json.dumps(ctx, ensure_ascii=False), ex=3600)
    except Exception as e:
        logger.warning(f"Redis context save fail for {call_id}: {e}")
    finally:
        await r.aclose()


# ── Node ①: receive_asr ──

async def receive_asr_node(state: CallGraphState) -> dict:
    ctx = await load_context(state["call_id"])
    return {
        "user_input": state["user_input"],
        "asr_minio_key": state.get("asr_minio_key"),
        "turn_history": ctx.get("turn_history", []),
        "turn_count": ctx.get("turn_count", 0),
        "identity": ctx.get("identity"),
    }


# ── Node ②: mcp_identity ──

async def mcp_identity_node(state: CallGraphState) -> dict:
    if _mcp_client is None:
        return {"identity": None}
    try:
        result = await _mcp_client.query_user_identity(state["user_key"], state["biz_type"])
        return {"identity": {
            "user_id": result.user_id,
            "name_masked": result.name_masked,
            "gender": result.gender,
            "verified": result.verified,
        }}
    except Exception as e:
        logger.error(f"[{state.get('call_id', '?')}] MCP 身份查询失败: {e}")
        return {"identity": None}


# ── Node ③: credit_query (conditional) ──

async def credit_query_node(state: CallGraphState) -> dict:
    if _mcp_client is None:
        return {"credit_result": None}
    try:
        user_id = state.get("identity", {}).get("user_id", "") if state.get("identity") else ""
        result = await _mcp_client.query_credit_profile(user_id, state["user_key"])
        return {"credit_result": {
            "user_id": result.user_id,
            "credit_qualified": result.credit_qualified,
            "risk_level": result.risk_level,
            "details": result.details,
        }}
    except Exception as e:
        logger.error(f"[{state.get('call_id', '?')}] 征信查询失败: {e}")
        return {"credit_result": None}


# ── Node ④: recall_memory ──

async def recall_memory_node(state: CallGraphState) -> dict:
    if _assembler is None:
        return {"memory_block": ""}
    try:
        memory_block = await _assembler.assemble(
            biz_type=state["biz_type"],
            user_key=state["user_key"],
            user_input=state["user_input"],
        )
        return {"memory_block": memory_block}
    except Exception as e:
        logger.error(f"[{state.get('call_id', '?')}] 记忆召回失败: {e}")
        return {"memory_block": ""}


# ── Node ⑤: rag_retrieve ──

async def rag_retrieve_node(state: CallGraphState) -> dict:
    try:
        scripts = await retrieve_scripts(
            biz_type=state["biz_type"],
            user_input=state["user_input"],
        )
        return {"rag_block": build_rag_block(scripts)}
    except Exception as e:
        logger.error(f"[{state.get('call_id', '?')}] RAG 检索失败: {e}")
        return {"rag_block": ""}


# ── Node ⑥: llm_decide ──

async def llm_decide_node(state: CallGraphState) -> dict:
    llm = get_llm_service()
    biz_type = state["biz_type"]

    prompt_path = os.path.join(os.path.dirname(__file__), "prompts", f"{biz_type}.yaml")
    system_prompt = ""
    if os.path.exists(prompt_path):
        with open(prompt_path) as f:
            data = yaml.safe_load(f)
            system_prompt = data.get("system_prompt", data.get("template", ""))

    messages = build_messages(
        biz_type=biz_type,
        system_prompt=system_prompt,
        user_input=state["user_input"],
        memory_block=state.get("memory_block", ""),
        rag_block=state.get("rag_block", ""),
        turn_history=state.get("turn_history", []),
    )

    try:
        action = await llm.chat_for_action([m.model_dump() for m in messages])
    except Exception as e:
        logger.error(f"[{state.get('call_id', '?')}] LLM 调用失败: {e}")
        action = LLMAction(action="say", text=FALLBACK_ACTION_TEXT)

    return {"llm_action": action}


# ── Node ⑦: tts_synthesize ──

async def tts_synthesize_node(state: CallGraphState) -> dict:
    action = state.get("llm_action")
    if not action:
        return {}

    # TTS synthesis
    tts_result = None
    if _tts_client is not None and action.text:
        tts_result = await _tts_client.synthesize(action.text, state["call_id"], state["biz_type"])

    # Update turn history
    turn_history = list(state.get("turn_history", []))
    turn_history.append({"role": "user", "text": state["user_input"]})
    turn_history.append({"role": "assistant", "text": action.text})
    turn_count = state.get("turn_count", 0) + 1

    # Save context to Redis
    new_state = {**state, "turn_history": turn_history, "turn_count": turn_count}
    await save_context(state["call_id"], new_state)

    return {
        "tts_audio": tts_result.get("audio") if tts_result else None,
        "tts_minio_key": tts_result.get("minio_key") if tts_result else None,
        "turn_history": turn_history,
        "turn_count": turn_count,
    }


# ── Conditional routing ──

def should_query_credit(state: CallGraphState) -> str:
    if state.get("biz_type") == "marketing":
        return "credit_query"
    return "recall_memory"


# ── Graph builder ──

def create_call_graph():
    graph = StateGraph(CallGraphState)

    graph.add_node("receive_asr", receive_asr_node)
    graph.add_node("mcp_identity", mcp_identity_node)
    graph.add_node("credit_query", credit_query_node)
    graph.add_node("recall_memory", recall_memory_node)
    graph.add_node("rag_retrieve", rag_retrieve_node)
    graph.add_node("llm_decide", llm_decide_node)
    graph.add_node("tts_synthesize", tts_synthesize_node)

    graph.set_entry_point("receive_asr")
    graph.add_edge("receive_asr", "mcp_identity")
    graph.add_conditional_edges("mcp_identity", should_query_credit, {
        "credit_query": "credit_query",
        "recall_memory": "recall_memory",
    })
    graph.add_edge("credit_query", "recall_memory")
    graph.add_edge("recall_memory", "rag_retrieve")
    graph.add_edge("rag_retrieve", "llm_decide")
    graph.add_edge("llm_decide", "tts_synthesize")
    graph.add_edge("tts_synthesize", END)

    return graph.compile()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/lindaw/Documents/aiphone/agent-orchestrator && python -m pytest tests/test_graph_flow.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/lindaw/Documents/aiphone/agent-orchestrator && git add graph_flow.py tests/test_graph_flow.py && git commit -m "refactor(orchestrator): 重写 LangGraph 7 节点管线"
```

---

### Task 4: Update config.py — remove ESL, add TTS adapter URL

**Files:**
- Modify: `agent-orchestrator/config.py`

- [ ] **Step 1: Update config.py**

Replace the full contents of `config.py`:

```python
"""应用配置 - pydantic-settings，环境变量覆盖"""
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """智能外呼系统配置"""

    # PostgreSQL (asyncpg)
    pg_dsn: str = "postgresql+asyncpg://postgres@127.0.0.1:5432/callbot"
    pg_pool_size: int = 10
    pg_max_overflow: int = 20

    # Redis
    redis_url: str = "redis://127.0.0.1:6379/0"

    # MinIO
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "admin"
    minio_secret_key: str = "changeme123"

    # TTS adapter
    tts_adapter_url: str = "http://127.0.0.1:8081"

    # 业务
    biz_types: list[str] = Field(
        default=["customer_service", "collection", "marketing"]
    )

    # 超时
    llm_timeout_sec: float = 3.0

    # LLM
    llm_engine: str = "qwen"
    llm_base_url: str = "http://127.0.0.1:8080/v1"
    llm_model: str = "qwen3.5-9b"
    llm_embedding_model: str = "text-embedding-v3"

    # MCP
    mcp_server_url: str = "http://127.0.0.1:9090"

    model_config = {"env_prefix": "CALLBOT_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
```

Changes:
- Removed: `fs_esl_host`, `fs_esl_port`, `fs_esl_password` (ESL)
- Removed: `handoff_extension`, `legal_notice_file`, `silence_timeout_sec`, `max_silence_count` (ESL/compliance)
- Added: `tts_adapter_url`

- [ ] **Step 2: Run existing config test to verify it passes**

Run: `cd /Users/lindaw/Documents/aiphone/agent-orchestrator && python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
cd /Users/lindaw/Documents/aiphone/agent-orchestrator && git add config.py && git commit -m "refactor(orchestrator): 移除 ESL 配置，新增 TTS adapter URL"
```

---

### Task 5: Rewrite main.py — FastAPI HTTP service

**Files:**
- Rewrite: `agent-orchestrator/main.py`
- Create: `agent-orchestrator/tests/test_main.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_main.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lindaw/Documents/aiphone/agent-orchestrator && python -m pytest tests/test_main.py -v`
Expected: FAIL — main.py still has old code

- [ ] **Step 3: Rewrite main.py**

Rewrite `main.py`:

```python
"""Agent Orchestrator — FastAPI HTTP 服务"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel

from config import settings
from graph_flow import create_call_graph, set_services, CallGraphState
from memory.assembler import MemoryAssembler
from mcp_client import MCPClient
from tts_client import TTSClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

_graph = None
_initialized = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _graph, _initialized

    assembler = MemoryAssembler()
    mcp = MCPClient(settings.mcp_server_url)
    tts = TTSClient(settings.tts_adapter_url)
    set_services(assembler, mcp, tts)

    _graph = create_call_graph()
    _initialized = True
    logger.info("=== Agent Orchestrator 启动 ===")

    yield

    _initialized = False
    logger.info("=== Agent Orchestrator 关闭 ===")


app = FastAPI(title="Agent Orchestrator", lifespan=lifespan)


class SpeechRequest(BaseModel):
    call_id: str
    biz_type: str
    user_key: str
    text: str
    minio_key: str | None = None


@app.get("/healthz")
async def healthz():
    return {"status": "ok" if _initialized else "initializing"}


@app.post("/call/speech")
async def handle_speech(request: SpeechRequest):
    initial_state: CallGraphState = {
        "call_id": request.call_id,
        "biz_type": request.biz_type,
        "user_key": request.user_key,
        "user_input": request.text,
        "asr_minio_key": request.minio_key,
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

    result = await _graph.ainvoke(initial_state)

    action = result.get("llm_action")
    return {
        "action": action.action if action else "say",
        "text": action.text if action else "",
        "tts_minio_key": result.get("tts_minio_key"),
        "tts_audio": result.get("tts_audio"),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/lindaw/Documents/aiphone/agent-orchestrator && python -m pytest tests/test_main.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/lindaw/Documents/aiphone/agent-orchestrator && git add main.py tests/test_main.py && git commit -m "refactor(orchestrator): 重写 main.py 为 FastAPI HTTP 服务"
```

---

### Task 6: Update requirements.txt — remove python-ESL

**Files:**
- Modify: `agent-orchestrator/requirements.txt`

- [ ] **Step 1: Remove python-ESL from requirements**

Remove the line `python-ESL>=0.1` and the `# FreeSWITCH` comment from `requirements.txt`.

The file should become:

```
# Web framework
fastapi>=0.110
uvicorn>=0.29

# Database
sqlalchemy[asyncio]>=2.0.50
asyncpg>=0.29
alembic>=1.13
pgvector>=0.3

# AI/LLM - LangChain 1.2
langchain>=1.2,<2.0
langchain-core>=1.2,<2.0
langchain-openai>=1.2,<2.0
langgraph>=1.2,<2.0

# Redis
redis>=5.0

# Config
pydantic>=2.0
pydantic-settings>=2.0

# Utilities
httpx>=0.27
pyyaml>=6.0
prometheus-client>=0.20

# Testing
pytest>=8.0
pytest-asyncio>=0.23
```

- [ ] **Step 2: Commit**

```bash
cd /Users/lindaw/Documents/aiphone/agent-orchestrator && git add requirements.txt && git commit -m "chore(orchestrator): 移除 python-ESL 依赖"
```

---

### Task 7: Run full test suite and fix any remaining issues

**Files:**
- Possibly fix: any remaining test files

- [ ] **Step 1: Run all tests**

Run: `cd /Users/lindaw/Documents/aiphone/agent-orchestrator && python -m pytest tests/ -v --tb=short`
Expected: All tests pass. Remaining test files: `test_config.py`, `test_mcp_client.py`, `test_graph_flow.py`, `test_tts_client.py`, `test_main.py`, `tests/memory/` directory.

- [ ] **Step 2: Fix any import errors in remaining test files**

If `tests/test_mcp_client.py` or `tests/test_config.py` import deleted modules, fix the imports. Key files to check:
- `tests/test_mcp_client.py` — should only import from `mcp_client`, should be fine
- `tests/test_config.py` — should only import from `config`, should be fine
- `tests/test_call_state.py` — DELETE (call_state.py was deleted)
- `tests/test_compliance.py` — DELETE (compliance.py was deleted)

If `tests/test_call_state.py` or `tests/test_compliance.py` still exist, delete them:
```bash
rm -f tests/test_call_state.py tests/test_compliance.py
```

- [ ] **Step 3: Re-run full suite**

Run: `cd /Users/lindaw/Documents/aiphone/agent-orchestrator && python -m pytest tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 4: Commit any fixes**

```bash
cd /Users/lindaw/Documents/aiphone/agent-orchestrator && git add -A && git commit -m "chore(orchestrator): 清理残留测试文件"
```

---

### Task 8: Final integration verification

- [ ] **Step 1: Verify all Python files compile without import errors**

Run:
```bash
cd /Users/lindaw/Documents/aiphone/agent-orchestrator && python -c "
import main
import graph_flow
import config
import tts_client
import mcp_client
import prompt_builder
import llm.service
import memory.assembler
import memory.store
import memory.redis_memory
import rag.retriever
import database
import storage.repository
print('All imports OK')
"
```
Expected: `All imports OK`

- [ ] **Step 2: Verify deleted files are gone**

Run:
```bash
cd /Users/lindaw/Documents/aiphone/agent-orchestrator && \
for f in fs_esl.py fs_actions.py event_handlers.py call_state.py compliance.py llm_base.py rag_retriever.py; do \
  test -f "$f" && echo "ERROR: $f still exists" || true; \
done && \
test -d llm_engines && echo "ERROR: llm_engines/ still exists" || true && \
echo "Deletion verified"
```
Expected: `Deletion verified` (no ERROR lines)

- [ ] **Step 3: Run full test suite one final time**

Run: `cd /Users/lindaw/Documents/aiphone/agent-orchestrator && python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 4: Verify graph node count is 7**

Run:
```bash
cd /Users/lindaw/Documents/aiphone/agent-orchestrator && python -c "
from graph_flow import create_call_graph
g = create_call_graph()
print(f'Graph nodes: {list(g.nodes.keys())}')
assert len(g.nodes) == 7, f'Expected 7 nodes, got {len(g.nodes)}'
print('7-node pipeline verified')
"
```
Expected: `7-node pipeline verified`
