"""TransvoiceAgent —— LangGraph 管线经 llm_node 嵌入 AgentSession（design.md §3.6）。

节点 ①-⑥ 经 run_pre_llm_phase + astream_reply_text 复用；节点 ⑦ 由 SDK
默认 tts_node（TransvoiceTTS）接管。终端动作（end/handoff）必须等当前回复
playout 排空再执行（原 handler `_streaming_fn` 后 `wait_drained` 语义）。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from livekit.agents import Agent, llm

from graph.flow import astream_reply_text, run_pre_llm_phase
from storage.persistence_helpers import fire_insert_event

logger = logging.getLogger(__name__)

# 原 handler tts_buffer.wait_drained(timeout=10.0) 的等价保护
_PLAYOUT_DRAIN_TIMEOUT = 10.0


@dataclass
class CallContext:
    call_id: str
    biz_type: str
    user_key: str
    tenant_id: str
    scenario: str
    call_task_vars: dict = field(default_factory=dict)
    handoff_extension: str = "1001"


async def execute_terminal_action(esl, registry, action: str, call_id: str,
                                  handoff_extension: str) -> None:
    """ESL 终态动作（自原 handler._execute_terminal_action 原样迁移）。"""
    if esl is None:
        logger.warning("[%s] ESL unavailable, cannot execute: %s", call_id, action)
        return
    # 关键事件落 PG（fire-and-forget）先行；biz_type/user_key 从 registry 取，取不到留空
    active = registry.get(call_id) if registry else None
    biz_type = active.biz_type if active else ""
    user_key = active.user_key if active else ""
    if action in ("end", "handoff"):
        fire_insert_event(
            call_id=call_id, fs_uuid=call_id, biz_type=biz_type,
            user_id=user_key, user_key=user_key,
            event_type="hangup_by_bot" if action == "end" else "handoff",
            payload={"extension": handoff_extension} if action == "handoff" else {},
        )
    try:
        if action == "end":
            logger.info("[%s] ESL hangup: %s", call_id, await esl.hangup(call_id))
        elif action == "handoff":
            logger.info("[%s] ESL transfer to %s: %s", call_id, handoff_extension,
                        await esl.transfer(call_id, handoff_extension))
    except Exception as e:
        logger.error("[%s] ESL action '%s' failed: %s", call_id, action, e)


def _resolve_playout_wait_coro(session):
    """从 session 解析"当前回复排空"等待协程。

    生产路径：AgentSession.current_speech → SpeechHandle.wait_for_playout()
    （等整个 assistant turn 含 TTS playout 完成；在 llm_node 内联 await 会与
    speech 完成条件循环等待，必须由 spawn 出去的任务调用）。
    测试路径：_test_session.wait_for_playout()。均不可得时返回 None。
    """
    speech = getattr(session, "current_speech", None)
    if speech is not None:
        return speech.wait_for_playout()
    # 测试替身可能直接挂协程（wait_for_playout=coro）而非可调用
    wait_val = getattr(session, "wait_for_playout", None)
    if wait_val is not None:
        return wait_val() if callable(wait_val) else wait_val
    return None


class TransvoiceAgent(Agent):
    def __init__(self, *, instructions: str, ctx: CallContext, registry, esl) -> None:
        super().__init__(instructions=instructions)
        self._ctx = ctx
        self._registry = registry
        self._esl = esl
        self._executed_actions: list[str] = []
        # 强引用防 GC + done_callback 记错（对齐 _archive_recording/_fire 约定）
        self._terminal_tasks: set[asyncio.Task] = set()

    def _session_or_test(self):
        # Agent.session 在未运行时抛 RuntimeError（非 AttributeError），getattr 默认值接不住
        try:
            return self.session
        except RuntimeError:
            return getattr(self, "_test_session", None)

    async def llm_node(self, chat_ctx: llm.ChatContext, tools, model_settings):
        user_text = ""
        for item in reversed(chat_ctx.items):
            if isinstance(item, llm.ChatMessage) and item.role == "user":
                user_text = item.text_content or ""
                break
        async for token in self._llm_node_impl(user_text):
            yield token

    async def _llm_node_impl(self, user_text: str):
        ctx = self._ctx
        state = await run_pre_llm_phase(
            ctx.call_id, ctx.biz_type, ctx.user_key, b"",
            precomputed_asr_result={"text": user_text} if user_text else None,
            tenant_id=ctx.tenant_id, scenario=ctx.scenario,
            call_task_vars=ctx.call_task_vars,
        )
        if not (state.get("user_input") or "").strip():
            logger.info("[%s] empty user input, skip reply", ctx.call_id)
            return

        pending: list[str] = []

        async def on_action(action: str) -> None:
            pending.append(action)

        async for token in astream_reply_text(state, on_action=on_action):
            yield token

        terminal = next((a for a in pending if a in ("end", "handoff")), None)
        if terminal:
            # spawn 出去等 playout：llm_node 生成器自身是 speech 完成的前置条件，
            # 内联 await current_speech.wait_for_playout() 必死锁
            task = asyncio.create_task(
                self._run_action_after_playout(terminal),
                name=f"terminal-action-{ctx.call_id}",
            )
            self._terminal_tasks.add(task)
            task.add_done_callback(self._terminal_tasks.discard)

    async def _run_action_after_playout(self, terminal: str) -> None:
        ctx = self._ctx
        session = self._session_or_test()
        if session is not None:
            wait_coro = _resolve_playout_wait_coro(session)
            if wait_coro is not None:
                try:
                    await asyncio.wait_for(wait_coro, timeout=_PLAYOUT_DRAIN_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.warning(
                        "[%s] playout drain timeout %.0fs, execute '%s' anyway",
                        ctx.call_id, _PLAYOUT_DRAIN_TIMEOUT, terminal,
                    )
                except Exception as e:
                    logger.warning("[%s] wait playout: %s", ctx.call_id, e)
        self._executed_actions.append(terminal)
        await execute_terminal_action(
            self._esl, self._registry, terminal, ctx.call_id,
            ctx.handoff_extension,
        )

    def on_conversation_item_added(self, ev) -> None:
        """barge-in 落 PG（R2）：SDK 在 speech 被打断时为 assistant 消息置
        interrupted=True 后才发本事件（agent_activity 以
        interrupted=speech_handle.interrupted 落 chat_ctx），无 `own` 字段。"""
        item = getattr(ev, "item", None)
        if (isinstance(item, llm.ChatMessage)
                and item.role == "assistant" and item.interrupted):
            fire_insert_event(
                call_id=self._ctx.call_id, fs_uuid=self._ctx.call_id,
                biz_type=self._ctx.biz_type, user_id=self._ctx.user_key,
                user_key=self._ctx.user_key, event_type="barge_in", payload={},
            )
