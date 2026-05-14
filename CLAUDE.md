# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

智能外呼系统 (Smart Outbound Call System) — a telephony AI platform using MRCPv2 for speech recognition/synthesis, FreeSWITCH for SIP/RTP, and a LangGraph-orchestrated Python agent driving LLM-powered conversations.

## Commands

### Test
```bash
# ASR adapter (must cd first)
cd mrcp-asr && PYTHONPATH=$(pwd) pytest tests/ -v

# TTS adapter (must cd first)
cd mrcp-tts && PYTHONPATH=$(pwd) pytest tests/ -v

# Orchestrator
cd agent-orchestrator && PYTHONPATH=$(pwd) pytest tests/ -v

# Run single test file
cd mrcp-asr && PYTHONPATH=$(pwd) pytest tests/engines/sensevoice/test_engine.py -v
```

### Run
```bash
# ASR adapter (port 8080)
cd mrcp-asr/adapter && PYTHONPATH=$(cd .. && pwd) uvicorn main:app --host 0.0.0.0 --port 8080

# TTS adapter (port 8081)
cd mrcp-tts/adapter && PYTHONPATH=$(cd .. && pwd) uvicorn main:app --host 0.0.0.0 --port 8081

# Orchestrator
cd agent-orchestrator && python main.py
```

### DB Migrations
```bash
cd agent-orchestrator && alembic upgrade head
```

## Architecture

```
SIP Caller → FreeSWITCH (mod_sofia, SIP/RTP)
    ├─ mod_unimrcp (MRCPv2 client) → UniMRCP Server (:8060)
    │    ├─ ASR resource → HTTP POST → mrcp-asr adapter (:8080)
    │    └─ TTS resource → HTTP POST → mrcp-tts adapter (:8081)
    └─ mod_event_socket (ESL, :8021) → agent-orchestrator
         ├─ CHANNEL_ANSWER → play legal notice → start recording → start detect_speech
         ├─ DETECTED_SPEECH → LangGraph flow → LLM decision → TTS speak
         └─ CHANNEL_HANGUP → cleanup
```

### Three Components

**mrcp-asr** — FastAPI adapter with pluggable ASR engines. Receives audio from UniMRCP, uploads to MinIO, forwards to engine for recognition. Endpoints: `POST /asr/recognize`, `GET /asr/audio/{call_id}`, `GET /healthz`.

**mrcp-tts** — FastAPI adapter with pluggable TTS engines. Receives text from UniMRCP/orchestrator, synthesizes audio, uploads to MinIO. Endpoints: `POST /tts/synthesize` (binary), `POST /tts/synthesize_json` (JSON with base64 audio + minio_key), `GET /healthz`.

**agent-orchestrator** — FastAPI HTTP service. Receives ASR text via `POST /call/speech`, runs 7-node LangGraph pipeline (`receive_asr → mcp_identity → [credit_query] → recall_memory → rag_retrieve → llm_decide → tts_synthesize`), returns TTS audio. LLM via LangChain ChatOpenAI with structured output (`LLMAction`). Conversation history via langchain-redis `RedisChatMessageHistory`.

### Engine Plugin Pattern (ASR & TTS)

1. `adapter/base.py` defines ABC (`ASREngine` / `TTSEngine`)
2. `adapter/engines/{name}/engine.py` implements ABC, exports `Engine = ConcreteClass`
3. `adapter/config.yaml` selects active engine by name
4. `adapter/config.py` loads via `importlib.import_module(f"adapter.engines.{name}.engine")`

To add a new engine: create engine directory + `engine.py` implementing the ABC, update `config.yaml`.

Current engines: SenseVoice (ASR, calls FunASR Server), VibeVoice (ASR), CosyVoice (TTS), VibeVoice (TTS). All call external model servers via httpx AsyncClient.

### Business Type Isolation

Three biz_types: `customer_service`, `collection`, `marketing`. Isolated at:
- TTS: voice profiles per engine (`BIZ_TYPE_PROFILES` dict with voice_id/speed/volume/pitch)
- Redis: key prefix `cb:{biz_type}:...`
- PostgreSQL: `biz_type` column on all tables, HASH partitioning
- Prompts: `prompts/{biz_type}.yaml`
- Compliance: biz_type-specific rules (marketing do_not_call, collection field sanitization)

### Configuration

- **Orchestrator**: `pydantic-settings` with `CALLBOT_` env prefix, reads `.env`
- **ASR/TTS adapters**: `config.yaml` for engine name + env vars for API URLs and MinIO
- **MinIO**: `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET` (optional, disabled when empty)
- **Engine URLs**: `SENSEVOICE_API_URL`, `COSYVOICE_API_URL`, `VIBEVOICE_ASR_API_URL`, `VIBEVOICE_TTS_API_URL`

### Key Orchestrator Modules

| Module | Role |
|--------|------|
| `config.py` | pydantic-settings, all config via `CALLBOT_` env prefix |
| `main.py` | FastAPI app with lifespan init, `POST /call/speech`, `GET /healthz` |
| `graph/flow.py` | LangGraph 7-node StateGraph pipeline |
| `graph/prompt.py` | System prompt + RAG + memory + chat history assembly |
| `clients/mcp.py` | MCP user center (identity/credit query) |
| `clients/tts.py` | TTS adapter HTTP client |
| `llm/service.py` | LangChain ChatOpenAI with structured output |
| `memory/assembler.py` | Aggregates Redis hot facts + PG facts |
| `memory/chat_history.py` | langchain-redis `RedisChatMessageHistory` conversation memory |
| `memory/redis_memory.py` | Per-user hot fact storage (Redis hash) |
| `memory/store.py` | PG fact + vector data access |
| `rag/retriever.py` | pgvector cosine similarity on script_library |
| `db/models.py` | SQLAlchemy 2.0 ORM models (callbot schema) |
| `storage/repository.py` | Async repository for sessions/turns/events/artifacts |

### Infrastructure

- **PostgreSQL 17** with pgvector extension, schema `callbot`, 9 tables
- **Redis** for hot memory, conversation history (langchain-redis), session state
- **MinIO** for audio archiving
- **FreeSWITCH 1.10.12** compiled from source with mod_unimrcp
- **UniMRCP** compiled from source
- **GPU allocation**: ASR=GPU0, TTS=GPU1, LLM(Qwen)=GPU2
