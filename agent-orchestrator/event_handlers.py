import time
import json
import logging
from datetime import datetime
from call_state import CallState, CallStateManager
from fs_actions import FSActions, TTSProfileMap
from config import settings

logger = logging.getLogger(__name__)

# Phase 3 临时规则引擎
RULES = {
    "marketing": "您好，感谢您的接听，请问有什么可以帮助您的？",
    "customer_service": "您好，请问有什么可以帮您？",
    "collection": "您好，这里有一笔账单需要确认。",
}
DEFAULT_REPLY = "抱歉，我没听清楚，请您再说一遍。"


class EventDispatcher:
    def __init__(self, state_mgr: CallStateManager, conn, actions: FSActions):
        self.state_mgr = state_mgr
        self.conn = conn
        self.actions = actions

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
        state = CallState(
            fs_uuid=fs_uuid,
            status="created",
            start_time=time.time(),
        )
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

        try:
            result = self.actions.play_legal_notice(fs_uuid)
            state.recording_notice_played = result
            if not result:
                logger.error(f"[{fs_uuid}] 录音告知播放失败")
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
            logger.debug(f"[{fs_uuid}] DETECTED_SPEECH: empty text")
            return

        logger.info(f"[{fs_uuid}] 用户发言: {speech_text[:50]}")
        state.silence_count = 0
        state.turn_count += 1

        # LangGraph 流程
        import asyncio
        from graph_flow import create_call_graph

        graph = create_call_graph()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(graph.ainvoke({
            "fs_uuid": state.fs_uuid,
            "biz_type": state.biz_type,
            "user_key": state.user_key,
            "user_input": speech_text,
            "memory_block": "",
            "rag_block": "",
            "llm_action": None,
            "identity_verified": state.identity_verified,
            "turn_count": state.turn_count,
            "handoff_reason": "",
        }))
        action = result.get("llm_action")
        if not action:
            return

        if action.type in ("say", "ask"):
            self.actions.stop_detect_speech(fs_uuid)
            self.actions.tts_speak(fs_uuid, state.biz_type, action.text)
            state.status = "listening"
            self.actions.start_detect_speech(fs_uuid)
        elif action.type == "handoff":
            self.actions.transfer(fs_uuid)
        elif action.type == "end":
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
