# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

智能外呼系统 (Smart Outbound Call System) — a telephony AI platform using MRCPv2 for speech recognition/synthesis, FreeSWITCH for SIP/RTP, and a LangGraph-orchestrated Python agent driving LLM-powered conversations.

## Commands

### Test
```bash
# ASR adapter (must cd first)
cd agent-asr && PYTHONPATH=$(pwd) pytest tests/ -v

# TTS adapter (must cd first)
cd agent-tts && PYTHONPATH=$(pwd) pytest tests/ -v

# Orchestrator (main.py at root, source in src/)
cd agent-orchestrator && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v

# Run single test file
cd agent-asr && PYTHONPATH=$(pwd) pytest tests/engines/sensevoice/test_engine.py -v
```

### Run
```bash
# ASR adapter (port 8080)
cd agent-asr/asradapter && PYTHONPATH=$(cd .. && pwd) uvicorn main:app --host 0.0.0.0 --port 8080

# TTS adapter (port 8081)
cd agent-tts/ttsadapter && PYTHONPATH=$(cd .. && pwd) uvicorn main:app --host 0.0.0.0 --port 8081

# Orchestrator (main.py at root, source in src/)
cd agent-orchestrator && PYTHONPATH=$(pwd):$(pwd)/src uvicorn main:app --host 0.0.0.0 --port 8000
```

### DB Migrations
```bash
cd agent-orchestrator && PYTHONPATH=$(pwd)/src alembic upgrade head
```

### MCP Server (Java)
```bash
# Build
cd mcp-server/java-mcp-server && JAVA_HOME=/opt/homebrew/opt/openjdk ./mvnw clean compile

# Run (port 9090)
cd mcp-server/java-mcp-server && JAVA_HOME=/opt/homebrew/opt/openjdk ./mvnw spring-boot:run
```

## Architecture

```
SIP Caller → FreeSWITCH (mod_sofia, SIP/RTP)
    ├─ mod_unimrcp (MRCPv2 client) → UniMRCP Server (:8060)
    │    ├─ ASR resource → HTTP POST → agent-asr adapter (:8080)
    │    └─ TTS resource → HTTP POST → agent-tts adapter (:8081)
    └─ 外部调度系统 → HTTP POST /call/speech → agent-orchestrator (:8000)
         ├─ 7-node LangGraph pipeline → LLM decision → TTS via HTTP
         ├─ Node ②/③: MCP client → java-mcp-server (:9090) 用户中心
         └─ Returns: {action, text, tts_audio, tts_minio_key}
```

Data flow per turn:
```
呼入: FreeSWITCH → UniMRCP → agent-asr → 外部调度 → POST /call/speech → orchestrator
呼出: orchestrator → POST /tts/synthesize_json → agent-tts → UniMRCP → FreeSWITCH
```

### Three Components

**agent-asr** — FastAPI adapter with pluggable ASR engines. Receives audio from UniMRCP, uploads to MinIO, forwards to engine for recognition. Endpoints: `POST /asr/recognize`, `GET /asr/audio/{call_id}`, `GET /healthz`.

**agent-tts** — FastAPI adapter with pluggable TTS engines. Receives text from UniMRCP/orchestrator, synthesizes audio, uploads to MinIO. Endpoints: `POST /tts/synthesize` (binary), `POST /tts/synthesize_json` (JSON with base64 audio + minio_key), `GET /healthz`.

**agent-orchestrator** — FastAPI HTTP service. Receives ASR text via `POST /call/speech`, runs 7-node LangGraph pipeline, returns TTS audio. LLM via LangChain ChatOpenAI with structured output (`LLMAction`). Conversation history via langchain-redis `RedisChatMessageHistory`. Agentic RAG with adaptive retrieval + document grading + query rewriting.

**java-mcp-server** — Spring Boot 3.5 + Spring AI 1.1.6 stateless MCP server (WebMVC transport). Serves as the user center backend for orchestrator nodes ② and ③. Exposes two MCP tools: `user_identity_query` (phone + biz_type → user_id, phone_masked, id_card_last_four) and `user_credit_query` (user_id → credit_qualified, risk_level). Endpoint: `POST /mcp` on port 9090.

### LangGraph 7-Node Pipeline

```
① receive_asr    — 接收 ASR 文本，加载 Redis 对话历史
② mcp_identity   — 手机号查用户中心（用户ID/脱敏手机号/身份证后四位）
③ [条件] credit_query — 仅 marketing 查询征信
④ recall_memory  — Redis 热记忆 + PG 长期记忆
⑤ rag_retrieve   — Agentic RAG (自适应检索 → 文档评分 → 查询改写)
⑥ llm_decide     — LLM 结构化输出
⑦ tts_synthesize — 调用 TTS adapter，保存对话历史
```

### Engine Plugin Pattern (ASR & TTS)

1. `asradapter/base.py` / `ttsadapter/base.py` defines ABC (`ASREngine` / `TTSEngine`)
2. `asradapter/engines/{name}/engine.py` implements ABC, exports `Engine = ConcreteClass`
3. `asradapter/config.yaml` / `ttsadapter/config.yaml` selects active engine by name
4. `asradapter/config.py` / `ttsadapter/config.py` loads via `importlib`

To add a new engine: create engine directory + `engine.py` implementing the ABC, update `config.yaml`.

Current engines: SenseVoice (ASR, calls FunASR Server), VibeVoice (ASR), CosyVoice (TTS), VibeVoice (TTS). All call external model servers via httpx AsyncClient.

### Business Type Isolation

Three biz_types: `customer_service`, `collection`, `marketing`. Isolated at:
- TTS: voice profiles per engine (`BIZ_TYPE_PROFILES` dict with voice_id/speed/volume/pitch)
- Redis: key prefix `cb:{biz_type}:...`
- PostgreSQL: `biz_type` column on all tables, HASH partitioning
- Prompts: `prompts/{biz_type}.yaml`
- Credit query: only marketing biz_type

### Agentic RAG (node ⑤)

Full adaptive + corrective RAG inside `rag_retrieve_node`:
1. **Adaptive** — `should_retrieve()`: LLM decides if query needs knowledge base (skips greetings/closings)
2. **Retrieve** — `retrieve_scripts()`: pgvector cosine similarity on `callbot.script_library`
3. **Grade** — `grade_documents()`: LLM evaluates each script's relevance
4. **Rewrite** — `rewrite_query()`: if all docs irrelevant, LLM rewrites query and retries (max 2 retries)

### Configuration

- **Orchestrator**: `pydantic-settings` with `CALLBOT_` env prefix, reads `.env`
- **ASR/TTS adapters**: `config.yaml` for engine name + env vars for API URLs and MinIO
- **MinIO**: `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET` (optional, disabled when empty)
- **Engine URLs**: `SENSEVOICE_API_URL`, `COSYVOICE_API_URL`, `VIBEVOICE_ASR_API_URL`, `VIBEVOICE_TTS_API_URL`
- **RAG**: `CALLBOT_RAG_TOP_K` (default 3), `CALLBOT_RAG_SIMILARITY_THRESHOLD` (default 0.7), `CALLBOT_RAG_MAX_RETRIES` (default 2)
- **MCP Server**: `application.yaml` with `spring.ai.mcp.server.*` properties, STATELESS protocol, WebMVC transport, port 9090

### Key Orchestrator Modules

| Module | Role |
|--------|------|
| `main.py` | FastAPI app with lifespan init, `POST /call/speech`, `GET /healthz` (at project root) |
| `src/config.py` | pydantic-settings, all config via `CALLBOT_` env prefix |
| `src/database.py` | SQLAlchemy 2.0 async engine + session factory |
| `src/graph/flow.py` | LangGraph 7-node StateGraph pipeline |
| `src/graph/prompt.py` | System prompt + RAG + memory + chat history assembly |
| `src/clients/mcp.py` | MCP client → java-mcp-server (identity/credit query via langchain-mcp-adapters) |
| `src/clients/tts.py` | TTS adapter HTTP client |
| `src/llm/service.py` | LangChain ChatOpenAI with structured output + embeddings |
| `src/memory/assembler.py` | Aggregates Redis hot facts + PG long-term facts |
| `src/memory/chat_history.py` | langchain-redis `RedisChatMessageHistory` conversation memory |
| `src/memory/redis_memory.py` | Per-user hot fact storage (Redis hash) |
| `src/memory/store.py` | PG fact + vector data access |
| `src/rag/retriever.py` | Agentic RAG: adaptive retrieval + document grading + query rewriting |
| `src/db/models.py` | SQLAlchemy 2.0 ORM models (callbot schema, 9 tables) |
| `src/storage/repository.py` | Async repository for sessions/turns/events/artifacts |

### Project Structure

```
aiphone/
├── agent-asr/           # ASR adapter (FastAPI, pluggable engines)
│   ├── asradapter/      # main.py, base.py, config.py
│   │   ├── store/       # storage.py (MinIO upload)
│   │   └── engines/     # sensevoice/, vibevoice/
│   ├── asrengine/       # SenseVoice 推理引擎 (Dockerfile + server.py)
│   └── tests/           # test_base, test_main, test_storage, engines/
├── agent-tts/           # TTS adapter (FastAPI, pluggable engines)
│   ├── ttsadapter/      # main.py, base.py, config.py
│   │   ├── store/       # storage.py (MinIO upload)
│   │   └── engines/     # cosyvoice/, vibevoice/
│   ├── ttsengine/       # CosyVoice 推理引擎 (Dockerfile + server.py)
│   └── tests/           # test_base, test_main, test_storage, engines/
├── agent-orchestrator/  # LangGraph 7-node pipeline (FastAPI HTTP service)
│   ├── main.py          # FastAPI entry point
│   ├── src/             # 核心源码 (PYTHONPATH includes src/)
│   │   ├── config.py    # pydantic-settings
│   │   ├── database.py  # SQLAlchemy async engine
│   │   ├── clients/     # mcp.py, tts.py
│   │   ├── graph/       # flow.py, prompt.py, prompts/
│   │   ├── llm/         # service.py (ChatOpenAI + structured output)
│   │   ├── memory/      # assembler.py, chat_history.py, redis_memory.py, store.py
│   │   ├── rag/         # retriever.py (Agentic RAG)
│   │   ├── db/          # models.py (ORM)
│   │   └── storage/     # repository.py
│   ├── llm/             # Qwen LLM 推理引擎 Dockerfile
│   ├── alembic/         # DB migrations
│   └── tests/           # test suite
├── mcp-server/              # MCP servers (user center backend)
│   └── java-mcp-server/ # Spring Boot + Spring AI stateless MCP server
│       ├── src/main/java/com/trans/mcp/
│       │   ├── McpApplication.java     # Entry point + tool registration
│       │   ├── model/                  # IdentityResult, CreditResult records
│       │   └── service/                # UserService, CreditService (@Tool)
│       └── src/main/resources/
│           └── application.yaml        # MCP server config (STATELESS, /mcp endpoint)
├── deploy/              # systemd services, install scripts, monitoring
├── freeswitch/          # FreeSWITCH + UniMRCP configs
└── docs/                # design specs, implementation plans
```

### Infrastructure

- **PostgreSQL 17** with pgvector extension, schema `callbot`, 9 tables
- **Redis** for hot memory, conversation history (langchain-redis), session state
- **MinIO** for audio archiving
- **FreeSWITCH 1.10.12** compiled from source with mod_unimrcp
- **Java MCP Server** Spring Boot 3.5 + Spring AI 1.1.6, Java 25, Maven build
- **UniMRCP** compiled from source
- **GPU allocation**: ASR=GPU0, TTS=GPU1, LLM(Qwen)=GPU2
