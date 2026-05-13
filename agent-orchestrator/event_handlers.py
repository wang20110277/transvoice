"""事件分发器 - 异步 LangGraph 集成 + MCP 身份核验"""
import asyncio
import threading
import time
import logging
from datetime import datetime
from call_state import CallState, CallStateManager
from fs_actions import FSActions
from config import settings

logger = logging.getLogger(__name__)


class EventDispatcher:
    def __init__(self, state_mgr: CallStateManager, conn, actions: FSActions,
                 graph, mcp_client=None):
        self.state_mgr = state_mgr
        self.conn = conn
        self.actions = actions
        self.graph = graph
        self.mcp_client = mcp_client
        # 创建并运行独立 asyncio 事件循环（后台线程）
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._loop_thread.start()

    def dispatch(self, event: dict):
        event_name = event.get("Event-Name", "")
        handler = {
            "CHANNEL_CREATE": self.handle_channel_create,
            "CHANNEL_ANSWER": self.handle_channel_answer,
            "DETECTED_SPEECH": self.handle_detected_speech,
            "CHANNEL_HANGUP": self.handle_channel_hangup,
            "CHANNEL_HANGUP_COMPLETE": self.handle_channel_hangup,
        }.get(event_name)
        if handler:
            handler(event)

    def handle_channel_create(self, event: dict):
        fs_uuid = event["Unique-ID"]
        state = CallState(fs_uuid=fs_uuid, status="created", start_time=time.time())
        self.state_mgr.set(fs_uuid, state)
        logger.info(f"[{fs_uuid}] CHANNEL_CREATE")

    def handle_channel_answer(self, event: dict):
        fs_uuid = event["Unique-ID"]
        state = self.state_mgr.get(fs_uuid)
        if not state:
            logger.error(f"[{fs_uuid}] CHANNEL_ANSWER: state not found")
            return

        state.status = "answered"
        state.answer_time = time.time()
        state.biz_type = event.get("variable_biz_type", "marketing")
        state.task_id = event.get("variable_task_id", "")
        state.core_user_id = event.get("variable_core_user_id", "")
        state.phone_hash = event.get("variable_phone_hash", "")
        state.user_key = f"{state.core_user_id}:{state.phone_hash}" if state.core_user_id else ""

        # MCP 身份核验（异步，5s 超时）
        if self.mcp_client and state.phone_hash:
            try:
                identity = asyncio.run_coroutine_threadsafe(
                    self.mcp_client.query_user_identity(state.phone_hash, state.biz_type),
                    self._loop,
                ).result(timeout=5.0)
                state.identity_verified = identity.verified
            except Exception as e:
                logger.warning(f"[{fs_uuid}] 身份核验失败: {e}")

        try:
            result = self.actions.play_legal_notice(fs_uuid)
            state.recording_notice_played = result
        except Exception as e:
            logger.exception(f"[{fs_uuid}] 录音告知异常: {e}")
            state.recording_notice_played = False

        try:
            date_str = datetime.now().strftime("%Y/%m/%d")
            rec_path = f"/nas/rec/{state.biz_type}/{date_str}/{fs_uuid}"
            self.actions.start_recording(fs_uuid, rec_path)
            state.recording_path = rec_path
        except Exception as e:
            logger.exception(f"[{fs_uuid}] 录音启动失败: {e}")

        try:
            self.actions.start_detect_speech(fs_uuid)
            state.status = "listening"
        except Exception as e:
            logger.exception(f"[{fs_uuid}] detect_speech 启动失败: {e}")

        logger.info(f"[{fs_uuid}] CHANNEL_ANSWER: biz_type={state.biz_type}")

    def handle_detected_speech(self, event: dict):
        fs_uuid = event["Unique-ID"]
        state = self.state_mgr.get(fs_uuid)
        if not state or state.status != "listening":
            return

        speech_text = event.get("speech", "") or ""
        if not speech_text.strip():
            return

        logger.info(f"[{fs_uuid}] 用户发言: {speech_text[:50]}")
        state.silence_count = 0
        state.turn_count += 1

        try:
            future = asyncio.run_coroutine_threadsafe(
                self.graph.ainvoke({
                    "fs_uuid": state.fs_uuid,
                    "biz_type": state.biz_type,
                    "user_key": state.user_key,
                    "user_id": state.core_user_id,
                    "user_input": speech_text,
                    "memory_block": "",
                    "rag_block": "",
                    "llm_action": None,
                    "identity_verified": state.identity_verified,
                    "do_not_call": False,
                    "turn_count": state.turn_count,
                    "turn_history": state.turn_history,
                    "handoff_reason": "",
                }),
                self._loop,
            )
            result = future.result(timeout=settings.llm_timeout_sec * 3)
        except Exception as e:
            logger.error(f"[{fs_uuid}] LangGraph 调用失败: {e}")
            return

        action = result.get("llm_action")
        if not action:
            return

        state.turn_history = result.get("turn_history", state.turn_history)

        if action.action in ("say", "ask"):
            self.actions.stop_detect_speech(fs_uuid)
            self.actions.tts_speak(fs_uuid, state.biz_type, action.text)
            state.status = "listening"
            self.actions.start_detect_speech(fs_uuid)
        elif action.action == "handoff":
            self.actions.transfer(fs_uuid)
        elif action.action == "end":
            self.actions.hangup(fs_uuid)

    def handle_channel_hangup(self, event: dict):
        fs_uuid = event["Unique-ID"]
        state = self.state_mgr.remove(fs_uuid)
        if not state:
            return

        hangup_cause = event.get("Hangup-Cause", "")
        duration = time.time() - state.start_time if state.start_time else 0
        logger.info(f"[{fs_uuid}] HANGUP: cause={hangup_cause}, duration={duration:.1f}s")

        try:
            self.actions.stop_detect_speech(fs_uuid)
        except Exception:
            pass
        try:
            self.actions.stop_recording(fs_uuid)
        except Exception:
            pass
