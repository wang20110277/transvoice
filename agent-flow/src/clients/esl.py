"""Async ESL 客户端 — 通过 FreeSWITCH Event Socket 控制通话生命周期"""
import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ESLEvent:
    """ESL 事件/响应"""
    headers: dict[str, str]
    body: str = ""


class ESLClient:
    """异步 ESL 客户端, 连接 FreeSWITCH mod_event_socket。

    支持:
    - api/bgapi 命令 (uuid_kill, uuid_transfer, uuid_break 等)
    - 事件订阅 (CHANNEL_HANGUP, CHANNEL_ANSWER 等)
    - 自动重连
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8021, password: str = "ClueCon"):
        self._host = host
        self._port = port
        self._password = password
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False
        self._event_handlers: dict[str, list] = {}
        self._event_task: asyncio.Task | None = None

    async def start(self) -> None:
        """连接并认证, 启动事件监听。"""
        await self._connect()
        self._event_task = asyncio.create_task(self._event_loop())
        logger.info("ESL connected to %s:%d", self._host, self._port)

    async def close(self) -> None:
        """关闭连接。"""
        self._connected = False
        if self._event_task:
            self._event_task.cancel()
            self._event_task = None
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
        logger.info("ESL disconnected")

    # ── Call control commands ──

    async def hangup(self, uuid: str, cause: str = "NORMAL_CLEARING") -> str:
        """挂断通话。"""
        return await self.api(f"uuid_kill {uuid} {cause}")

    async def transfer(self, uuid: str, dest: str, dialplan: str = "XML",
                       context: str = "public") -> str:
        """转接通话到指定分机。"""
        return await self.api(f"uuid_transfer {uuid} {dest} {dialplan} {context}")

    async def break_media(self, uuid: str) -> str:
        """中断当前媒体播放。"""
        return await self.api(f"uuid_break {uuid}")

    async def broadcast(self, uuid: str, path: str, leg: str = "both") -> str:
        """向通话广播音频。"""
        return await self.api(f"uuid_broadcast {uuid} {path} {leg}")

    async def set_var(self, uuid: str, var: str, value: str) -> str:
        """设置通道变量。"""
        return await self.api(f"uuid_setvar {uuid} {var} {value}")

    # ── Event subscription ──

    def on_event(self, event_name: str, handler) -> None:
        """注册事件处理器。"""
        self._event_handlers.setdefault(event_name, []).append(handler)

    async def subscribe(self, events: list[str]) -> None:
        """订阅 FreeSWITCH 事件。"""
        events_str = " ".join(events)
        await self._send_command(f"event plain {events_str}\n\n")

    # ── Low-level protocol ──

    async def api(self, command: str) -> str:
        """发送同步 API 命令, 返回结果文本。"""
        if not self._connected:
            return "-ERR not connected"
        resp = await self._send_command(f"api {command}\n\n")
        return resp.body.strip() if resp else "-ERR no response"

    async def bgapi(self, command: str) -> str:
        """发送后台 API 命令, 返回 job UUID。"""
        if not self._connected:
            return "-ERR not connected"
        resp = await self._send_command(f"bgapi {command}\n\n")
        return resp.body.strip() if resp else "-ERR no response"

    async def _connect(self) -> None:
        """建立 TCP 连接并认证。"""
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)

        # 读取认证挑战
        greeting = await self._read_response()
        if not greeting or "auth" not in greeting.headers.get("Content-Type", "").lower():
            raise ConnectionError("ESL: 未收到认证挑战")

        # 认证
        resp = await self._send_command(f"auth {self._password}\n\n")
        if not resp or "+OK" not in resp.headers.get("Reply-Text", ""):
            raise ConnectionError("ESL: 认证失败")

        self._connected = True

    async def _send_command(self, command: str) -> ESLEvent | None:
        """发送命令并读取响应。"""
        if self._writer is None:
            return None
        try:
            self._writer.write(command.encode())
            await self._writer.drain()
            return await self._read_response()
        except Exception as e:
            logger.error("ESL send error: %s", e)
            self._connected = False
            return None

    async def _read_response(self) -> ESLEvent | None:
        """读取一个 ESL 响应/事件。"""
        if self._reader is None:
            return None
        try:
            headers: dict[str, str] = {}
            # 读取 headers (空行结束)
            while True:
                line = await asyncio.wait_for(self._reader.readline(), timeout=10.0)
                line = line.decode().strip()
                if not line:
                    break
                if ": " in line:
                    key, _, val = line.partition(": ")
                    headers[key] = val

            # 读取 body (根据 Content-Length)
            content_length = int(headers.get("Content-Length", "0"))
            body = ""
            if content_length > 0:
                raw = await asyncio.wait_for(
                    self._reader.readexactly(content_length), timeout=10.0
                )
                body = raw.decode(errors="replace")

            return ESLEvent(headers=headers, body=body)
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.error("ESL read error: %s", e)
            self._connected = False
            return None

    async def _event_loop(self) -> None:
        """后台事件监听循环。"""
        while self._connected:
            try:
                event = await self._read_response()
                if event is None:
                    if self._connected:
                        await asyncio.sleep(0.1)
                    continue

                content_type = event.headers.get("Content-Type", "")
                if content_type == "text/event-plain":
                    event_name = event.headers.get("Event-Name", "")
                    handlers = self._event_handlers.get(event_name, [])
                    for handler in handlers:
                        try:
                            if asyncio.iscoroutinefunction(handler):
                                await handler(event)
                            else:
                                handler(event)
                        except Exception as e:
                            logger.error("ESL event handler error: %s", e)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("ESL event loop error: %s", e)
                if self._connected:
                    await asyncio.sleep(1.0)
