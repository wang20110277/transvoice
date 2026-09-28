"""通话编排管线 — Pre-LLM 阶段 + 流式文本输出。

对外暴露两个函数，由 voice 插件（TransvoiceAgent llm_node）调用：
  - run_pre_llm_phase()  — ASR 文本接入 + 并行扇出（MCP/记忆/RAG）
  - astream_reply_text() — LLM 流式回复文本迭代器（SDK tokenizer 接管分句→TTS）

调用链路：
  main.py::ws_media_fork() → AgentSession → TransvoiceAgent.llm_node
    ├── run_pre_llm_phase()   ← Phase 1: ASR 接入 + MCP/Memory 并行
    └── astream_reply_text()  ← Phase 2: LLM 流式 token → yield（节点 ⑦ 由 SDK TTS 接管）
"""
import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from inspect import isawaitable
from typing import TypedDict

from langchain_core.messages import BaseMessage

from llm.service import get_llm_service
from config import settings
from rag.retriever import retrieve_scripts, build_rag_block, should_retrieve, grade_documents, rewrite_query
from graph.prompt import build_messages
from graph.render import render
from memory.assembler import MemoryAssembler
from memory.chat_history import load_chat_history, save_turn
from clients.mcp import MCPClient
from storage import minio_storage
from storage.persistence_helpers import fire_insert_turn

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 服务单例 — 由 main.py lifespan 通过 set_services() 注入
# ═══════════════════════════════════════════════════════════════════

_assembler: MemoryAssembler | None = None
_mcp_client: MCPClient | None = None


def set_services(assembler: MemoryAssembler, mcp: MCPClient) -> None:
    global _assembler, _mcp_client
    _assembler = assembler
    _mcp_client = mcp
    logger.info("flow services injected: mcp=%s", mcp is not None)


# ═══════════════════════════════════════════════════════════════════
# State 定义
# ═══════════════════════════════════════════════════════════════════

class CallGraphState(TypedDict, total=False):
    call_id: str
    biz_type: str
    user_key: str
    user_input: str
    audio_bytes: bytes | None
    identity: dict | None
    credit_result: dict | None
    memory_block: str
    rag_block: str
    chat_history: list[BaseMessage]
    call_task_vars: dict


# ═══════════════════════════════════════════════════════════════════
# Node 函数 — 被 run_pre_llm_phase 调用
# ═══════════════════════════════════════════════════════════════════

async def _asr_node(state: CallGraphState) -> dict:
    """Node ①: ASR 文本接入（识别由 TransvoiceSTT 逐 call WS 完成，audio_bytes 仅归档）。"""
    call_id = state.get("call_id", "?")
    audio_bytes = state.get("audio_bytes")

    if audio_bytes:
        asr_minio_key = minio_storage.build_object_key(prefix="asr", call_id=call_id)
        if asr_minio_key:
            asyncio.create_task(minio_storage.upload_audio_async(audio_bytes, asr_minio_key))

    return {"user_input": state.get("user_input", "")}


async def _mcp_identity_node(state: CallGraphState) -> dict:
    """Node ②: MCP 用户身份查询。"""
    if _mcp_client is None:
        return {"identity": None}
    call_id = state.get("call_id", "?")
    try:
        result = await _mcp_client.query_user_identity(state["user_key"], state["biz_type"])
        logger.info(
            "[%s] MCP user_identity_query(phone=%s, biz_type=%s) → user_id=%s phone_masked=%s id_card_last_four=%s",
            call_id, state["user_key"], state["biz_type"],
            result.user_id, result.phone_masked, result.id_card_last_four,
        )
        return {"identity": {
            "user_id": result.user_id,
            "phone_masked": result.phone_masked,
            "id_card_last_four": result.id_card_last_four,
        }}
    except Exception as e:
        logger.error("[%s] MCP identity failed: %s", call_id, e)
        return {"identity": None}


async def _credit_query_node(state: CallGraphState) -> dict:
    """Node ③: 征信查询（仅 marketing）。"""
    if _mcp_client is None:
        return {"credit_result": None}
    call_id = state.get("call_id", "?")
    try:
        user_id = state.get("identity", {}).get("user_id", "") if state.get("identity") else ""
        result = await _mcp_client.query_credit_profile(user_id)
        logger.info("[%s] credit: qualified=%s risk=%s", call_id, result.credit_qualified, result.risk_level)
        return {"credit_result": {
            "user_id": result.user_id,
            "credit_qualified": result.credit_qualified,
            "risk_level": result.risk_level,
            "details": result.details,
        }}
    except Exception as e:
        logger.error("[%s] credit query failed: %s", call_id, e)
        return {"credit_result": None}


async def _recall_memory_node(state: CallGraphState) -> dict:
    """Node ④: 记忆召回（Redis 热 + PG 长期）。"""
    if _assembler is None:
        return {"memory_block": ""}
    call_id = state.get("call_id", "?")
    try:
        memory_block = await _assembler.assemble(
            biz_type=state["biz_type"],
            user_key=state["user_key"],
            user_input=state["user_input"],
        )
        logger.info("[%s] memory assembled: %d chars", call_id, len(memory_block))
        return {"memory_block": memory_block}
    except Exception as e:
        logger.error("[%s] memory recall failed: %s", call_id, e)
        return {"memory_block": ""}


async def _rag_retrieve_node(state: CallGraphState) -> dict:
    """Node ⑤: Agentic RAG（自适应检索 + 文档评分 + 查询改写）。"""
    call_id = state.get("call_id", "?")
    try:
        rag_query = state["user_input"]

        need_retrieve = await should_retrieve(rag_query, state["biz_type"])
        if not need_retrieve:
            logger.info("[%s] RAG skipped (greeting/closing)", call_id)
            return {"rag_block": ""}

        for attempt in range(settings.rag_max_retries + 1):
            scripts = await retrieve_scripts(state["biz_type"], rag_query)
            if scripts:
                relevant = await grade_documents(rag_query, scripts)
                if relevant:
                    logger.info("[%s] RAG: %d relevant scripts found (attempt %d)", call_id, len(relevant), attempt + 1)
                    return {"rag_block": build_rag_block(relevant)}

            if attempt < settings.rag_max_retries:
                rag_query = await rewrite_query(rag_query, scripts or [])
                logger.info("[%s] RAG query rewritten (attempt %d): %s", call_id, attempt + 1, rag_query[:50])

        logger.info("[%s] RAG: no relevant scripts after %d attempts", call_id, settings.rag_max_retries + 1)
        return {"rag_block": ""}
    except Exception as e:
        logger.error("[%s] RAG failed: %s", call_id, e)
        return {"rag_block": ""}


# ═══════════════════════════════════════════════════════════════════
# Phase 1: Pre-LLM — ASR + 并行扇出
# ═══════════════════════════════════════════════════════════════════

async def run_pre_llm_phase(
    call_id: str, biz_type: str, user_key: str, audio_bytes: bytes,
    precomputed_asr_result: dict | None = None,
    tenant_id: str = "default",
    scenario: str = "default",
    call_task_vars: dict | None = None,
) -> CallGraphState:
    """Phase 1: ASR 识别 + 并行扇出（MCP 身份 + 记忆召回 + RAG 检索）。

    Args:
        call_id: 通话唯一标识
        biz_type: 业务类型 (customer_service/collection/marketing)
        user_key: 用户标识
        audio_bytes: 用户音频 PCM
        precomputed_asr_result: 已通过 WS 流式获取的 ASR 结果（跳过批量 recognize）
        tenant_id: 租户/业务系统(提示词隔离维度)
        scenario: 话术场景(提示词选择维度)
        call_task_vars: 外呼每号码 render 变量（call_target.vars），由 render() 替换 prompt 占位符；
            呼入/无变量时 {} (flow.py 下游 state.get 已就绪消费)

    Returns:
        组装好的 CallGraphState，供 astream_reply_text 使用
    """
    t0 = time.monotonic()
    logger.info(
        "[%s] tenant=%s biz_type=%s scenario=%s user_key=%s",
        call_id, tenant_id, biz_type, scenario, user_key,
    )

    # ── ASR ──
    state: CallGraphState = {
        "call_id": call_id,
        "tenant_id": tenant_id,
        "biz_type": biz_type,
        "scenario": scenario,
        "user_key": user_key,
        "user_input": "",
        "audio_bytes": audio_bytes,
        "identity": None,
        "credit_result": None,
        "memory_block": "",
        "rag_block": "",
        "chat_history": [],
        "call_task_vars": call_task_vars or {},
    }

    if precomputed_asr_result:
        precomputed_text = precomputed_asr_result.get("text", "")
        if precomputed_text:
            state["user_input"] = precomputed_text
            state["audio_bytes"] = None
        asr_result = await _asr_node(state)
        state.update(asr_result)
        if precomputed_text:
            state["user_input"] = precomputed_text
    else:
        asr_result = await _asr_node(state)
        state.update(asr_result)

    # 加载跨轮对话历史 (plain Redis LIST,无 RediSearch 依赖),供 LLM 区分首轮/后续轮
    state["chat_history"] = await load_chat_history(call_id, biz_type)

    logger.info("[%s] ASR done: user_input=%s", call_id, state.get("user_input", "")[:50])

    # ── 并行扇出: MCP 身份 + 记忆召回 + RAG ──
    # MCP 身份查询已启用（含 mock 模式）；记忆召回 / RAG 仍待修复（RedisSearch /
    # Ollama structured_output 依赖），暂保持禁用。
    identity = await _mcp_identity_node(state)
    state.update(identity)
    if biz_type == "marketing" and _mcp_client:
        state.update(await _credit_query_node(state))

    elapsed = (time.monotonic() - t0) * 1000
    logger.info("[%s] pre-llm phase done in %.0fms", call_id, elapsed)

    return state


# ═══════════════════════════════════════════════════════════════════
# Phase 2: LLM 流式文本输出（livekit llm_node 消费）
# ═══════════════════════════════════════════════════════════════════

async def astream_reply_text(
    state: CallGraphState,
    on_action: Callable[[str], Awaitable[None]] | None = None,
) -> AsyncIterator[str]:
    """LLM 流式回复文本迭代器（livekit llm_node 消费，design.md §3.6）。

    节点 ⑥：prompt 三维加载 + 变量渲染；
    输出改为逐 token yield（SDK tokenizer 接管分句→TTS，节点 ⑦ 拆除）。
    action 经 on_action 回调（end/handoff 由 TransvoiceAgent 在 playout 排空后执行）。
    流末持久化对话历史（Redis save_turn + PG fire_insert_turn）。
    """
    from graph.prompt_config import get_system_prompt

    async def _emit_action(action: str) -> None:
        # 兼容同步回调实现（list.append / 同步方法）：仅 await 可等待返回值
        if on_action:
            result = on_action(action)
            if isawaitable(result):
                await result

    llm = get_llm_service()
    call_id = state.get("call_id", "?")
    biz_type = state["biz_type"]
    tenant_id = state.get("tenant_id", "default")
    scenario = state.get("scenario", "default")

    # ── 构建 Prompt ──
    system_prompt = await get_system_prompt(tenant_id, biz_type, scenario)
    logger.info(
        "[%s] tenant=%s biz_type=%s scenario=%s prompt loaded: %d chars",
        call_id, tenant_id, biz_type, scenario, len(system_prompt),
    )

    # 聚合变量上下文:MCP 身份 ‖ 外呼 call_task.vars(渲染 {占位符})
    vars_context: dict = {}
    identity = state.get("identity")
    if isinstance(identity, dict):
        vars_context.update(identity)
    call_task_vars = state.get("call_task_vars")
    if isinstance(call_task_vars, dict):
        vars_context.update(call_task_vars)
    rendered_prompt = render(system_prompt, vars_context)
    logger.info("[%s] rendered system_prompt (vars=%s):\n%s", call_id, list(vars_context), rendered_prompt)

    messages = build_messages(
        biz_type=biz_type,
        system_prompt=rendered_prompt,
        user_input=state["user_input"],
        memory_block=state.get("memory_block", ""),
        rag_block=state.get("rag_block", ""),
        chat_history=state.get("chat_history", []),
    )

    action_sent = False
    detected_action = "say"
    full_text = ""
    try:
        async for event in llm.astream_action([m.model_dump() for m in messages]):
            if event.action and not action_sent:
                action_sent = True
                detected_action = event.action
                await _emit_action(event.action)

            if event.text_delta:
                full_text += event.text_delta
                yield event.text_delta

            if event.is_complete:
                logger.info("[%s] LLM complete: action=%s text=%s", call_id, detected_action, full_text)
                if not full_text and event.parsed:
                    full_text = event.parsed.get("text", "")
    except asyncio.CancelledError:
        logger.info("[%s] astream_reply_text cancelled", call_id)
        raise
    except Exception as e:
        logger.error("[%s] streaming LLM failed: %s", call_id, e)

    # 兜底: 确保 action 已发送
    if not action_sent:
        await _emit_action("say")

    # 持久化本轮对话 (Redis LIST) + PG call_turn 双写，供下一轮/Console 审查
    if full_text.strip():
        await save_turn(call_id, biz_type, state.get("user_input", ""), full_text)

        _user_key = state.get("user_key", "")
        if state.get("user_input", "").strip():
            fire_insert_turn(
                call_id=call_id, fs_uuid=call_id, biz_type=biz_type,
                user_id=_user_key, user_key=_user_key, role="user",
                text=state.get("user_input", ""),
            )
        fire_insert_turn(
            call_id=call_id, fs_uuid=call_id, biz_type=biz_type,
            user_id=_user_key, user_key=_user_key, role="assistant", text=full_text,
        )
