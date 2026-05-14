import logging
import httpx
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class IdentityResult:
    user_id: str
    name_masked: str
    id_last_four: str
    gender: str
    verified: bool = True
    voiceprint_match: bool | None = None


@dataclass
class CreditResult:
    user_id: str
    credit_qualified: bool
    risk_level: str
    details: dict


class MCPClient:
    def __init__(self, server_url: str, timeout: float = 10.0):
        self._client = httpx.AsyncClient(base_url=server_url, timeout=timeout)

    async def _call_mcp(self, method: str, params: dict) -> dict:
        resp = await self._client.post("/mcp/call", json={"method": method, "params": params})
        resp.raise_for_status()
        return resp.json()

    async def query_user_identity(self, phone_hash: str, biz_type: str) -> IdentityResult:
        data = await self._call_mcp("user.identity.query", {"phone_hash": phone_hash, "biz_type": biz_type})
        return IdentityResult(
            user_id=data.get("user_id", ""),
            name_masked=data.get("name_masked", ""),
            id_last_four=data.get("id_last_four", ""),
            gender=data.get("gender", ""),
            verified=True,
        )

    async def query_credit_profile(self, user_id: str, phone_hash: str) -> CreditResult:
        data = await self._call_mcp("user.credit.query", {"user_id": user_id, "phone_hash": phone_hash})
        return CreditResult(
            user_id=user_id,
            credit_qualified=data.get("credit_qualified", False),
            risk_level=data.get("risk_level", "unknown"),
            details=data,
        )

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get("/healthz")
            return resp.status_code == 200
        except Exception:
            return False
