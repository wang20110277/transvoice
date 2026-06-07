"""Async ESL 客户端 — 通过 FreeSWITCH Event Socket 控制通话生命周期"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ESLEvent:
    """ESL 事件/响应"""
    headers: Dict[str, str]
    body: str = ""


class ESLClient:
    """异步 ESL 客户端, 连接 FreeSWITCH mod_event_socket。

    支持:
    - api/bgapi 命令 (uuid_kill, uuid_transfer, uuid_break 等)
    - 事件订阅 (CHANNEL_HANGUP, CHANNEL_ANSWER 等)
    - 自动重连 (断线后指数退避重连, 重订阅事件)
    - 心跳检测 (定期 api status, 发现僵死连接主动断开重连)
    """

    RECONNECT_BASE_DELAY = 1.0
    RECONNECT_MAX_DELAY = 30.0
    HEARTBEAT_INTERVAL = 30.0

    def __init__(self, host: str = "127.0.0.1", port: int = 8021, password: str = "ClueCon"):
        self._host = host
        self._port = port
        self._password = password
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._closing = False
        self._event_handlers: Dict[str, list] = {}
        self._subscribed_events: List[str] = []
        self._event_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._reconnect_delay = self.RECONNECT_BASE_DELAY
        self._io_lock = asyncio.Lock()

    async def start(self) -> None:
        """连接并认证, 启动事件监听。初始连接失败时启动后台重连。"""
        try:
            await self._connect()
            self._event_task = asyncio.create_task(self._event_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            logger.info("ESL connected to %s:%d", self._host, self._port)
        except Exception:
            logger.warning("ESL initial connection to %s:%d failed, starting background reconnect",
                           self._host, self._port)
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def close(self) -> None:
        """关闭连接, 停止重连。"""
        self._closing = True
        self._connected = False
        for task in (self._event_task, self._reconnect_task, self._heartbeat_task):
            if task:
                task.cancel()
        self._event_task = self._reconnect_task = self._heartbeat_task = None
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

    # ── Audio Fork (uuid_audio_fork) ──

    async def audio_fork_start(
        self, uuid: str, ws_url: str, mode: str = "mono", sample_rate: int = 16000,
        *,
        bidirectional_enabled: bool = True,
        bidirectional_stream: bool = True,
        bidirectional_sample_rate: int = 16000,
    ) -> str:
        """启动音频旁路 — FreeSWITCH 将作为 WebSocket 客户端连接到 ws_url，双向收发音频。

        通过同一 WebSocket 连接:
            FreeSWITCH → agent-flow: 用户上行音频 (PCM 16-bit, sample_rate Hz)
            agent-flow → FreeSWITCH: TTS 下行音频 (PCM 16-bit, bidirectional_sample_rate Hz)

        Args:
            uuid: 通话 UUID
            ws_url: WebSocket 接收地址，如 ws://127.0.0.1:8000/media/{uuid}
            mode: mono (混合双向) / both (分别发送)
            sample_rate: 上行采样率 (默认 16000)
            bidirectional_enabled: 启用双向音频 (默认 True)
            bidirectional_stream: 启用 binary 音频流接收 (默认 True)
            bidirectional_sample_rate: 下行音频采样率 (默认 16000)
        """
        be = "true" if bidirectional_enabled else "false"
        bs = "true" if bidirectional_stream else "false"
        return await self.bgapi(
            f"uuid_audio_fork {uuid} start {ws_url} {mode} {sample_rate} "
            f"audio_fork {{}} {be} {bs} {bidirectional_sample_rate}"
        )

    async def audio_fork_stop(self, uuid: str) -> str:
        """停止音频旁路。"""
        return await self.api(f"uuid_audio_fork {uuid} stop")

    async def broadcast_silence(self, uuid: str) -> str:
        """向通话播放无限静音流，保持拨号计划活跃（barge-in打断后重播）。"""
        return await self.bgapi(f"uuid_broadcast {uuid} silence_stream://-1")

    async def get_var(self, uuid: str, var: str) -> str:
        """获取通道变量。"""
        return await self.api(f"uuid_getvar {uuid} {var}")

    # ── Event subscription ──

    def on_event(self, event_name: str, handler) -> None:
        """注册事件处理器。"""
        self._event_handlers.setdefault(event_name, []).append(handler)

    async def subscribe(self, events: list[str]) -> None:
        """订阅 FreeSWITCH 事件 (记录到列表, 重连后自动重订阅)。"""
        self._subscribed_events = list(set(self._subscribed_events + events))
        await self._subscribe_on_wire()

    async def _subscribe_on_wire(self) -> None:
        """向 FreeSWITCH 发送事件订阅命令。"""
        if not self._subscribed_events or not self._connected:
            return
        events_str = " ".join(self._subscribed_events)
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

    # ── Connection management ──

    async def _connect(self) -> None:
        """建立 TCP 连接并认证。"""
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)

        greeting = await self._read_response()
        if not greeting or "auth" not in greeting.headers.get("Content-Type", "").lower():
            raise ConnectionError("ESL: 未收到认证挑战")

        resp = await self._send_command(f"auth {self._password}\n\n")
        if not resp or "+OK" not in resp.headers.get("Reply-Text", ""):
            raise ConnectionError("ESL: 认证失败")

        self._connected = True
        self._reconnect_delay = self.RECONNECT_BASE_DELAY

    async def _reconnect(self) -> None:
        """断线后自动重连 (指数退避)。"""
        if self._closing:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._connected = False
        self._cleanup_connection()
        logger.warning("ESL connection lost, reconnecting to %s:%d ...", self._host, self._port)
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        while not self._closing:
            try:
                await asyncio.sleep(self._reconnect_delay)
                await self._connect()
                await self._subscribe_on_wire()
                self._event_task = asyncio.create_task(self._event_loop())
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                logger.info("ESL reconnected to %s:%d", self._host, self._port)
                return
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("ESL reconnect failed: %s, retry in %.1fs", e, self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self.RECONNECT_MAX_DELAY)

    def _cleanup_connection(self) -> None:
        if self._event_task:
            self._event_task.cancel()
            self._event_task = None
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def _heartbeat_loop(self) -> None:
        """定期发送 api status 检测连接存活性。"""
        while self._connected and not self._closing:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            if not self._connected:
                return
            resp = await self._send_command("api status\n\n")
            if resp is None:
                logger.warning("ESL heartbeat failed, triggering reconnect")
                await self._reconnect()
                return

    # ── Low-level IO ──

    async def _send_command(self, command: str) -> Optional[ESLEvent]:
        """发送命令并读取响应（加锁防止事件循环和心跳并发读取）。"""
        if self._writer is None:
            return None
        async with self._io_lock:
            try:
                self._writer.write(command.encode())
                await self._writer.drain()
                return await self._read_response()
            except Exception as e:
                logger.error("ESL send error: %s", e)
                await self._reconnect()
                return None

    async def _read_response(self) -> Optional[ESLEvent]:
        """读取一个 ESL 响应/事件。"""
        if self._reader is None:
            return None
        try:
            headers: Dict[str, str] = {}
            while True:
                line = await asyncio.wait_for(self._reader.readline(), timeout=10.0)
                line = line.decode().strip()
                if not line:
                    break
                if ": " in line:
                    key, _, val = line.partition(": ")
                    headers[key] = val

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
                async with self._io_lock:
                    event = await self._read_response()
                if event is None:
                    if self._connected:
                        await asyncio.sleep(0.1)
                    continue

                content_type = event.headers.get("Content-Type", "")
                if content_type == "text/event-plain":
                    # plain 事件: 所有事件数据在 body 中，需要解析合并到 headers
                    for line in event.body.splitlines():
                        if ": " in line:
                            key, _, val = line.partition(": ")
                            event.headers[key] = val
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
