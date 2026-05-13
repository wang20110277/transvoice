import logging
from fs_esl import ESLEventLoop
from event_handlers import EventDispatcher
from call_state import CallStateManager
from fs_actions import FSActions
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=== 智能外呼 Orchestrator 启动 ===")

    state_mgr = CallStateManager()

    loop = ESLEventLoop(settings.fs_esl_host, settings.fs_esl_port, settings.fs_esl_password)
    if not loop.connect():
        logger.error("初始连接失败，将在循环中重连")

    actions = FSActions(loop.conn)
    dispatcher = EventDispatcher(state_mgr, loop.conn, actions)

    try:
        loop.run(dispatcher)
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭...")
        loop.stop()


if __name__ == "__main__":
    main()
