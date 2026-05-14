"""Agent Orchestrator — FastAPI HTTP 服务"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel

from fastapi import HTTPException

from config import settings
from database import engine as db_engine
from graph.flow import create_call_graph, set_services, CallGraphState
from memory.assembler import MemoryAssembler
from clients.mcp import MCPClient
from clients.tts import TTSClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

_graph = None
_initialized = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _graph, _initialized

    assembler = MemoryAssembler()
    mcp = MCPClient(settings.mcp_server_url)
    tts = TTSClient(settings.tts_adapter_url)
    set_services(assembler, mcp, tts)

    _graph = create_call_graph()
    _initialized = True
    logger.info("=== Agent Orchestrator 启动 ===")

    yield

    _initialized = False
    logger.info("=== Agent Orchestrator 关闭 ===")


app = FastAPI(title="Agent Orchestrator", lifespan=lifespan)


class SpeechRequest(BaseModel):
    call_id: str
    biz_type: str
    user_key: str
    text: str
    minio_key: str | None = None


@app.get("/healthz")
async def healthz():
    return {"status": "ok" if _initialized else "initializing"}


@app.post("/call/speech")
async def handle_speech(request: SpeechRequest):
    if _graph is None:
        raise HTTPException(status_code=503, detail="Service not initialized")

    initial_state: CallGraphState = {
        "call_id": request.call_id,
        "biz_type": request.biz_type,
        "user_key": request.user_key,
        "user_input": request.text,
        "asr_minio_key": request.minio_key,
        "identity": None,
        "credit_result": None,
        "memory_block": "",
        "rag_block": "",
        "llm_action": None,
        "tts_minio_key": None,
        "tts_audio": None,
        "chat_history": [],
    }

    result = await _graph.ainvoke(initial_state)

    action = result.get("llm_action")
    return {
        "action": action.action if action else "say",
        "text": action.text if action else "",
        "tts_minio_key": result.get("tts_minio_key"),
        "tts_audio": result.get("tts_audio"),
    }
