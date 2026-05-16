"""LangGraph 1.2 通话状态图 - 7 节点管线"""
import logging
import yaml
import os
from typing import TypedDict
from langgraph.graph import StateGraph, END

from langchain_core.messages import BaseMessage

from llm.service import LLMAction, FALLBACK_ACTION_TEXT, get_llm_service
from config import settings
from rag.retriever import retrieve_scripts, build_rag_block, should_retrieve, grade_documents, rewrite_query
from graph.prompt import build_messages
from memory.assembler import MemoryAssembler
from memory.chat_history import get_chat_history, save_turn
from clients.mcp import MCPClient
from clients.tts import TTSClient

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
    rag_retry_count: int
    rag_query: str
    llm_action: LLMAction | None
    tts_minio_key: str | None
    tts_audio: str | None
    chat_history: list[BaseMessage]


# ── Node ①: receive_asr ──

async def receive_asr_node(state: CallGraphState) -> dict:
    try:
        history = get_chat_history(state["call_id"], state["biz_type"])
        chat_history = list(await history.aget_messages())
    except Exception as e:
        logger.warning(f"[{state.get('call_id', '?')}] 对话历史加载失败: {e}")
        chat_history = []
    return {
        "user_input": state["user_input"],
        "asr_minio_key": state.get("asr_minio_key"),
        "chat_history": chat_history,
    }


# ── Node ②: mcp_identity ──

async def mcp_identity_node(state: CallGraphState) -> dict:
    if _mcp_client is None:
        return {"identity": None}
    try:
        result = await _mcp_client.query_user_identity(state["user_key"], state["biz_type"])
        return {"identity": {
            "user_id": result.user_id,
            "phone_masked": result.phone_masked,
            "id_card_last_four": result.id_card_last_four,
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
        result = await _mcp_client.query_credit_profile(user_id)
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
        rag_query = state["user_input"]

        # Adaptive: check if retrieval is needed
        need_retrieve = await should_retrieve(rag_query, state["biz_type"])
        if not need_retrieve:
            return {"rag_block": ""}

        # Corrective loop: retrieve -> grade -> rewrite -> retry
        for attempt in range(settings.rag_max_retries + 1):
            scripts = await retrieve_scripts(state["biz_type"], rag_query)

            if scripts:
                relevant = await grade_documents(rag_query, scripts)
                if relevant:
                    return {"rag_block": build_rag_block(relevant)}

            # Rewrite query for next attempt
            if attempt < settings.rag_max_retries:
                rag_query = await rewrite_query(rag_query, scripts or [])

        return {"rag_block": ""}
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
        chat_history=state.get("chat_history", []),
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

    tts_result = None
    try:
        if _tts_client is not None and action.text:
            tts_result = await _tts_client.synthesize(action.text, state["call_id"], state["biz_type"])
    except Exception as e:
        logger.error(f"[{state.get('call_id', '?')}] TTS 合成异常: {e}")
        tts_result = None

    try:
        history = get_chat_history(state["call_id"], state["biz_type"])
        await save_turn(history, state["user_input"], action.text)
    except Exception as e:
        logger.warning(f"[{state.get('call_id', '?')}] 对话历史保存失败: {e}")

    return {
        "tts_audio": tts_result.get("audio") if tts_result else None,
        "tts_minio_key": tts_result.get("minio_key") if tts_result else None,
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
