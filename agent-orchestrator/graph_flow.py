"""LangGraph 1.2 通话状态图 - 完整集成 LLM + RAG + 记忆 + 合规"""
import logging
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from llm.service import LLMAction, FALLBACK_ACTION_TEXT
from llm.service import get_llm_service
from rag.retriever import retrieve_scripts, build_rag_block
from prompt_builder import build_messages
from compliance import compliance_check
from memory.assembler import MemoryAssembler

logger = logging.getLogger(__name__)

_assembler: MemoryAssembler | None = None


def set_memory_assembler(assembler: MemoryAssembler) -> None:
    global _assembler
    _assembler = assembler


class CallGraphState(TypedDict, total=False):
    fs_uuid: str
    biz_type: str
    user_key: str
    user_id: str
    user_input: str
    memory_block: str
    rag_block: str
    llm_action: Optional[LLMAction]
    identity_verified: bool
    do_not_call: bool
    turn_count: int
    turn_history: list[dict]
    handoff_reason: str


async def recall_memory_node(state: CallGraphState) -> dict:
    try:
        if _assembler is None:
            return {"memory_block": ""}
        memory_block = await _assembler.assemble(
            biz_type=state["biz_type"],
            user_key=state["user_key"],
            user_input=state["user_input"],
        )
        return {"memory_block": memory_block}
    except Exception as e:
        logger.error(f"[{state.get('fs_uuid', '?')}] 记忆召回失败: {e}")
        return {"memory_block": ""}


async def rag_retrieve_node(state: CallGraphState) -> dict:
    try:
        scripts = await retrieve_scripts(
            biz_type=state["biz_type"],
            user_input=state["user_input"],
        )
        return {"rag_block": build_rag_block(scripts)}
    except Exception as e:
        logger.error(f"[{state.get('fs_uuid', '?')}] RAG 检索失败: {e}")
        return {"rag_block": ""}


async def llm_decide_node(state: CallGraphState) -> dict:
    llm = get_llm_service()

    import yaml
    import os
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
        logger.error(f"[{state['fs_uuid']}] LLM 调用失败: {e}")
        action = LLMAction(action="say", text=FALLBACK_ACTION_TEXT)

    return {"llm_action": action}


async def compliance_check_node(state: CallGraphState) -> dict:
    action = state.get("llm_action")
    if not action:
        return {"llm_action": action}
    checked = compliance_check(
        action=action,
        biz_type=state["biz_type"],
        identity_verified=state.get("identity_verified", False),
        do_not_call=state.get("do_not_call", False),
    )
    return {"llm_action": checked}


async def execute_action_node(state: CallGraphState) -> dict:
    action = state.get("llm_action")
    turn_history = list(state.get("turn_history", []))

    if action:
        turn_history.append({"role": "assistant", "text": action.text})

    if action and action.action in ("end", "handoff"):
        return {
            "handoff_reason": action.intent if action.action == "handoff" else "",
            "turn_history": turn_history,
        }
    return {"turn_history": turn_history}


async def finalize_node(state: CallGraphState) -> dict:
    logger.info(f"[{state['fs_uuid']}] finalize: action={state.get('llm_action')}")
    return {}


def route_after_llm(state: CallGraphState) -> str:
    if state.get("biz_type") == "collection" and not state.get("identity_verified"):
        return "compliance_check"
    return "execute_action"


def route_after_execute(state: CallGraphState) -> str:
    action = state.get("llm_action")
    if action and action.action in ("end", "handoff"):
        return "finalize"
    return END


def create_call_graph():
    graph = StateGraph(CallGraphState)

    graph.add_node("recall_memory", recall_memory_node)
    graph.add_node("rag_retrieve", rag_retrieve_node)
    graph.add_node("llm_decide", llm_decide_node)
    graph.add_node("compliance_check", compliance_check_node)
    graph.add_node("execute_action", execute_action_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("recall_memory")
    graph.add_edge("recall_memory", "rag_retrieve")
    graph.add_edge("rag_retrieve", "llm_decide")
    graph.add_conditional_edges("llm_decide", route_after_llm, {
        "compliance_check": "compliance_check",
        "execute_action": "execute_action",
    })
    graph.add_edge("compliance_check", "execute_action")
    graph.add_conditional_edges("execute_action", route_after_execute, {
        "finalize": "finalize",
        END: END,
    })

    return graph.compile()
