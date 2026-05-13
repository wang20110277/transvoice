import time
import json
import logging
from config import settings

logger = logging.getLogger(__name__)

try:
    from ESL import ESLconnection
except ImportError:
    ESLconnection = None


class ESLEventLoop:
    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self.password = password
        self.conn = None
        self._running = False
        self._reconnect_delay = 5

    def connect(self) -> bool:
        if ESLconnection is None:
            logger.error("python-ESL not installed")
            return False
        self.conn = ESLconnection(self.host, self.port, self.password)
        if not self.conn.connected():
            logger.error("ESL 连接失败")
            return False
        self.conn.events("json", "all")
        logger.info("ESL 连接成功")
        return True

    def disconnect(self):
        if self.conn:
            self.conn.disconnect()
            self.conn = None

    def recv_event(self) -> dict | None:
        if not self.conn or not self.conn.connected():
            return None
        event = self.conn.recv_event()
        if not event:
            return None
        headers = event.headers
        return dict(headers) if headers else {}

    def run(self, dispatcher):
        self._running = True
        while self._running:
            if not self.conn or not self.conn.connected():
                logger.warning(f"ESL 断线，{self._reconnect_delay}s 后重连...")
                time.sleep(self._reconnect_delay)
                if not self.connect():
                    continue
            event = self.recv_event()
            if event:
                try:
                    dispatcher.dispatch(event)
                except Exception as e:
                    logger.exception(f"事件处理异常: {e}")

    def stop(self):
        self._running = False
        self.disconnect()
