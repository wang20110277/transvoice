"""MCP Client — 基于 langchain-mcp-adapters 对接用户中心"""
import json
import logging
from dataclasses import dataclass

from langchain_mcp_adapters.client import MultiServerMCPClient

logger = logging.getLogger(__name__)


@dataclass
class IdentityResult:
    user_id: str
    phone_masked: str
    id_card_last_four: str


@dataclass
class CreditResult:
    user_id: str
    credit_qualified: bool
    risk_level: str
    details: dict


class MCPClient:
    """Wraps langchain-mcp-adapters MultiServerMCPClient for the user center MCP server."""

    def __init__(self, server_url: str, transport: str = "http"):
        self._client = MultiServerMCPClient(
            {"user_center": {"transport": transport, "url": server_url}},
        )
        self._tools: dict = {}
        self._server_url = server_url

    async def initialize(self) -> None:
        """Discover tools from the MCP server. Must be called before use."""
        tools = await self._client.get_tools()
        self._tools = {t.name: t for t in tools}
        logger.info("MCP tools discovered: %s", list(self._tools.keys()))

    async def _call_tool(self, name: str, arguments: dict) -> dict:
        tool = self._tools.get(name)
        if tool is None:
            raise RuntimeError(f"MCP tool not found: {name}")
        result = await tool.ainvoke(arguments)
        if isinstance(result, str):
            return json.loads(result)
        return result

    async def query_user_identity(self, phone: str, biz_type: str) -> IdentityResult:
        data = await self._call_tool("user_identity_query", {
            "phone": phone,
            "biz_type": biz_type,
        })
        return IdentityResult(
            user_id=data.get("user_id", ""),
            phone_masked=data.get("phone_masked", ""),
            id_card_last_four=data.get("id_card_last_four", ""),
        )

    async def query_credit_profile(self, user_id: str) -> CreditResult:
        data = await self._call_tool("user_credit_query", {
            "user_id": user_id,
        })
        return CreditResult(
            user_id=user_id,
            credit_qualified=data.get("credit_qualified", False),
            risk_level=data.get("risk_level", "unknown"),
            details=data,
        )

    async def health_check(self) -> bool:
        try:
            return bool(self._tools)
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.close()
