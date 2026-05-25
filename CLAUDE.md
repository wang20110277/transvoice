# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

智能外呼系统 (Smart Outbound Call System) — a telephony AI platform using FreeSWITCH for SIP/RTP with mod_audio_fork WebSocket audio streaming, built-in GPU ASR/TTS inference (SenseVoice + CosyVoice3), and a LangGraph-orchestrated Python agent driving LLM-powered conversations with full streaming pipeline, barge-in support, gRPC streaming (ASR/TTS), uvloop event loop, pre-VAD audio denoising, ESL auto-reconnect + heartbeat, and Docker Compose deployment.

## Coding Conventions

- **接口命名见名知意**：HTTP/gRPC/WebSocket 接口路径和函数名必须从名字就能看出用途，不使用模糊缩写。例如 `/call/text-turn`（文本输入轮次）、`/call/audio-turn`（音频输入轮次）、`/ws/streaming-call`（流式双向通话）、`/tts/synthesize-binary`（返回二进制音频）、`/asr/audio-meta/{id}`（音频元数据查询）。

## Commands

### Test
```bash
# ASR adapter (must cd first)
cd agent-asr && PYTHONPATH=$(pwd) pytest tests/ -v

# TTS adapter (must cd first)
cd agent-tts && PYTHONPATH=$(pwd) pytest tests/ -v

# Orchestrator (main.py at root, source in src/)
cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v

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
cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src uvicorn main:app --host 0.0.0.0 --port 8000
```

### DB Migrations
```bash
cd agent-flow && PYTHONPATH=$(pwd)/src alembic upgrade head
```

### Local (conda, all services)
```bash
# Start all
./scripts/local.sh

# Start specific services
./scripts/local.sh asr        # ASR only
./scripts/local.sh tts        # TTS only
./scripts/local.sh flow       # agent-flow only
./scripts/local.sh asr tts    # ASR + TTS

# Management
./scripts/local.sh stop       # Stop all
./scripts/local.sh status     # Check status
```

### Docker Compose (production)
```bash
# Full deployment
./scripts/prod.sh

# With rebuild
./scripts/prod.sh --build

# Management
./scripts/prod.sh --down      # Stop all
./scripts/prod.sh --status    # Check status
./scripts/prod.sh --logs [svc] # View logs
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
    └─ mod_audio_fork (WebSocket) → agent-flow (:8000)
         ├─ Node ①: agent-asr (:8080) 内置 GPU 推理 → 识别文本
         ├─ Node ②/③: MCP client → java-mcp-server (:9090) 用户中心
         ├─ Node ⑥: Qwen LLM (GPU2 :8083) → 流式回复文本
         └─ Node ⑦: agent-tts (:8081) 内置 GPU 推理 → 句级合成音频 → TTSOutputBuffer → 回传 FreeSWITCH
    └─ ESL (Event Socket :8021) → CHANNEL_HANGUP 事件 → ActiveCallRegistry 取消通话
```

Data flow per turn (two modes):
```
[同步模式] POST /call/text-turn 或 /call/audio-turn
呼入: HTTP 文本/音频 → agent-flow → ASR → LangGraph 7节点全量执行 → TTS → 返回

[流式模式] WebSocket /ws/streaming-call (生产路径, uvloop 事件循环)
呼入: FreeSWITCH → mod_audio_fork → agent-flow WebSocket → JitterBuffer → Denoiser降噪 → WebRTC VAD → ASR → 识别文本
并行: MCP身份查询 ‖ 记忆召回 ‖ RAG检索 (fan-out 并发)
决策: LLM 流式输出 → IncrementalJSONParser → SentenceSplitter → 句级文本
合成: 每句并行 TTS(gRPC/HTTP) → WAV→PCM → TTSOutputBuffer 稳态30ms帧 → WebSocket → FreeSWITCH
打断: 用户说话检测 → ESL uuid_break → 取消流式任务 → 新一轮对话
挂断: ESL CHANNEL_HANGUP → ActiveCallRegistry 取消 → 清理资源
```

### Three Components

**agent-asr** — FastAPI + gRPC service with pluggable ASR engines and built-in GPU inference. Loads SenseVoice (FunASR) model directly in-process, no separate inference server needed. Receives audio from agent-flow, runs recognition, uploads to MinIO. HTTP endpoints: `POST /asr/recognize-speech`, `GET /asr/audio-meta/{call_id}`, `GET /healthz`. gRPC endpoint: `ASRService.StreamingRecognize` (client-streaming, port 50051).

**agent-tts** — FastAPI + gRPC service with pluggable TTS engines and built-in GPU inference. Loads CosyVoice3 model directly in-process, no separate inference server needed. Receives text from orchestrator, synthesizes audio, uploads to MinIO. Disk cache keyed by voice+text hash, biz_type voice profiles. HTTP endpoints: `POST /tts/synthesize-binary` (binary audio response), `POST /tts/synthesize-json` (JSON with base64 audio + minio_key), `GET /healthz`. gRPC endpoint: `TTSService.Synthesize` (unary, port 50052).

**agent-flow** — FastAPI HTTP + WebSocket service (uvloop event loop). FreeSWITCH connects via mod_audio_fork WebSocket (`/ws/streaming-call`) for bidirectional audio streaming. Also exposes HTTP endpoints: `POST /call/text-turn` (text input), `POST /call/audio-turn` (audio input). Two execution modes: **sync mode** (HTTP path, full LangGraph pipeline) and **streaming mode** (WebSocket path, production). Streaming mode: LLM tokens streamed via `IncrementalJSONParser`, split into sentences by `SentenceSplitter`, each sentence synthesized by TTS in parallel (gRPC or HTTP), PCM audio paced through `TTSOutputBuffer` at steady 30ms frames. Barge-in: concurrent audio receive during AI speech with WebRTC VAD detection, ESL `uuid_break` to stop FreeSWITCH playback. Input audio smoothed through `JitterBuffer`, pre-VAD denoising via configurable denoiser (highpass/noisereduce/rnnoise). ESL (Event Socket Library) client subscribes to CHANNEL_HANGUP for call cancellation via `ActiveCallRegistry`. Conversation history via langchain-redis. Agentic RAG with adaptive retrieval + document grading + query rewriting. VAD using WebRTC VAD for endpointing. ASR/TTS gRPC streaming optional via feature flags (`CALLBOT_ASR_USE_GRPC`, `CALLBOT_TTS_USE_GRPC`).

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

Parallel fan-out: nodes ② mcp_identity, ④ recall_memory, ⑤ rag_retrieve execute concurrently after ① receive_asr.

**Streaming mode** (WebSocket path): `run_pre_llm_phase()` runs ① + parallel fan-out, then `run_streaming_pipeline()` streams LLM tokens through `SentenceSplitter`, spawning parallel TTS tasks per sentence with `audio_callback(pcm, index)` for ordered delivery via `TTSOutputBuffer`.

### Engine Plugin Pattern (ASR & TTS)

1. `asradapter/base.py` / `ttsadapter/base.py` defines ABC (`ASREngine` / `TTSEngine`)
2. `asradapter/engines/{name}/engine.py` implements ABC, exports `Engine = ConcreteClass`
3. `asradapter/config.yaml` / `ttsadapter/config.yaml` selects active engine by name
4. `asradapter/config.py` / `ttsadapter/config.py` loads via `importlib`

To add a new engine: create engine directory + `engine.py` implementing the ABC, update `config.yaml`.

Current engines: SenseVoice (ASR, built-in FunASR GPU inference), VibeVoice (ASR, remote HTTP), CosyVoice (TTS, built-in CosyVoice3 GPU inference), VibeVoice (TTS, remote HTTP).

### Business Type Isolation

Three biz_types: `customer_service`, `collection`, `marketing`. Isolated at:
- TTS: voice profiles per engine (`BIZ_TYPE_PROFILES` dict with voice_id/speed/volume/pitch)
- Redis: key prefix `cb:{biz_type}:...`
- PostgreSQL: `biz_type` column on all tables; sharding strategy: 单表起步，后期 Citus/pgcat 水平扩展，分布键 `user_id`（非 biz_type）
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
- **ASR/TTS**: `config.yaml` for engine name + env vars for model paths, API URLs and MinIO
- **MinIO**: `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET` (optional, disabled when empty)
- **ASR model**: `MODEL_DIR` (SenseVoice path), `SENSEVOICE_LANGUAGE`
- **TTS model**: `MODEL_DIR` (CosyVoice3-0.5B path), `COSYVOICE_RUNTIME`, `VOICES_DIR`, `TTS_CACHE_DIR`
- **Remote engines**: `VIBEVOICE_ASR_API_URL`, `VIBEVOICE_TTS_API_URL`
- **RAG**: `CALLBOT_RAG_TOP_K` (default 3), `CALLBOT_RAG_SIMILARITY_THRESHOLD` (default 0.7), `CALLBOT_RAG_MAX_RETRIES` (default 2)
- **ESL**: `CALLBOT_ESL_HOST`, `CALLBOT_ESL_PORT` (default 8021), `CALLBOT_ESL_PASSWORD`, `CALLBOT_HANDOFF_EXT` (default 1001)
- **VAD**: `CALLBOT_VAD_AGGRESSIVENESS` (0-3), `CALLBOT_VAD_SILENCE_FRAMES` (default 15), `CALLBOT_VAD_MIN_AUDIO_BYTES` (default 8000)
- **Barge-in**: `CALLBOT_BARGE_IN_MIN_AUDIO_BYTES` (default 4000, lower than VAD for faster reaction)
- **Jitter Buffer**: `CALLBOT_JITTER_TARGET_DEPTH` (default 3), `CALLBOT_JITTER_MAX_DEPTH` (default 10)
- **Denoise**: `CALLBOT_DENOISE_ENABLED` (`""` disabled, `"highpass"`, `"noisereduce"`, `"rnnoise"`), `CALLBOT_DENOISE_HIGHPASS_CUTOFF` (default 200.0 Hz)
- **ASR gRPC**: `CALLBOT_ASR_USE_GRPC` (default false), `CALLBOT_ASR_GRPC_TARGET` (default `127.0.0.1:50051`)
- **TTS gRPC**: `CALLBOT_TTS_USE_GRPC` (default false), `CALLBOT_TTS_GRPC_TARGET` (default `127.0.0.1:50052`)
- **uvloop**: enabled via Dockerfile CMD `--loop uvloop`, no config needed
- **MCP Server**: `application.yaml` with `spring.ai.mcp.server.*` properties, STATELESS protocol, WebMVC transport, port 9090

### Key Orchestrator Modules

| Module | Role |
|--------|------|
| `main.py` | FastAPI app with lifespan init, ESL lifecycle, `POST /call/text-turn`, `POST /call/audio-turn`, `WS /ws/streaming-call`, `GET /healthz` |
| `src/config.py` | pydantic-settings, all config via `CALLBOT_` env prefix |
| `src/database.py` | SQLAlchemy 2.0 async engine + session factory |
| `src/graph/flow.py` | LangGraph 7-node StateGraph pipeline + `run_pre_llm_phase` / `run_streaming_pipeline` for streaming mode |
| `src/graph/prompt.py` | System prompt + RAG + memory + chat history assembly |
| `src/clients/mcp.py` | MCP client → java-mcp-server (identity/credit query via langchain-mcp-adapters) |
| `src/clients/esl.py` | Async ESL client → FreeSWITCH Event Socket (auto-reconnect, heartbeat, hangup, transfer, break_media, event subscription) |
| `src/clients/tts.py` | TTS adapter HTTP client (full + raw WAV for streaming) |
| `src/clients/tts_grpc_client.py` | TTS gRPC client — unary synthesis, used by streaming pipeline when `CALLBOT_TTS_USE_GRPC=true` |
| `src/clients/asr.py` | ASR adapter HTTP client |
| `src/clients/asr_grpc_client.py` | ASR gRPC client — client-streaming for streaming audio transfer, batch fallback |
| `src/clients/asr_grpc/` | Generated gRPC proto stubs (asr_pb2, asr_pb2_grpc) |
| `src/clients/tts_grpc/` | Generated gRPC proto stubs (tts_pb2, tts_pb2_grpc) |
| `src/ws/handler.py` | WebSocket handlers: `CallWebSocketHandler` (sync) + `StreamingCallHandler` (streaming + barge-in) |
| `src/ws/vad.py` | WebRTC VAD for endpointing and barge-in speech detection |
| `src/ws/denoise.py` | Configurable pre-VAD denoiser (highpass/noisereduce/rnnoise), factory via `CALLBOT_DENOISE_ENABLED` |
| `src/ws/jitter_buffer.py` | `JitterBuffer` (input smoothing) + `TTSOutputBuffer` (steady 30ms frame delivery) |
| `src/ws/registry.py` | `ActiveCallRegistry` — per-call `asyncio.Event` for CHANNEL_HANGUP cancellation |
| `src/llm/service.py` | LangChain ChatOpenAI with structured output + streaming + embeddings |
| `src/llm/json_stream.py` | `IncrementalJSONParser` — extracts structured fields from LLM token stream |
| `src/llm/sentence_splitter.py` | `SentenceSplitter` — splits streaming tokens into TTS-ready sentences |
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
├── agent-asr/           # ASR service (FastAPI + gRPC, built-in GPU inference)
│   ├── asradapter/      # main.py, base.py, config.py, requirements.txt
│   │   ├── store/       # storage.py (MinIO upload)
│   │   ├── engines/     # sensevoice/ (GPU), vibevoice/ (remote HTTP)
│   │   ├── grpc_server.py  # gRPC ASR service (client-streaming, :50051)
│   │   └── proto/       # asr.proto + generated stubs (asr_pb2, asr_pb2_grpc)
│   ├── models/          # SenseVoiceSmall/ (local model weights)
│   ├── deploy/          # systemd units (sensevoice-asr.service, vibevoice-asr.service)
│   ├── Dockerfile       # PyTorch GPU image, model download
│   ├── README.md        # Component docs
│   └── tests/           # test_base, test_main, test_storage, engines/
├── agent-tts/           # TTS service (FastAPI + gRPC, built-in GPU inference)
│   ├── ttsadapter/      # main.py, base.py, config.py, requirements.txt
│   │   ├── store/       # storage.py (MinIO upload)
│   │   ├── engines/     # cosyvoice/ (CosyVoice3 GPU), vibevoice/ (remote HTTP)
│   │   ├── grpc_server.py  # gRPC TTS service (unary, :50052)
│   │   └── proto/       # tts.proto + generated stubs (tts_pb2, tts_pb2_grpc)
│   ├── models/          # CosyVoice3-0.5B/ (local model weights)
│   ├── deploy/          # systemd units (cosyvoice-tts.service, vibevoice-tts.service)
│   ├── Dockerfile       # PyTorch GPU image, model download
│   ├── README.md        # Component docs
│   └── tests/           # test_base, test_main, test_storage, engines/
├── agent-flow/  # LangGraph 7-node pipeline (FastAPI HTTP + WebSocket)
│   ├── main.py          # FastAPI entry point (HTTP + WebSocket + ESL lifecycle)
│   ├── src/             # 核心源码 (PYTHONPATH includes src/)
│   │   ├── config.py    # pydantic-settings (ESL/VAD/jitter/barge-in configs)
│   │   ├── database.py  # SQLAlchemy async engine
│   │   ├── clients/     # mcp.py, tts.py, asr.py, esl.py, tts_grpc_client.py, asr_grpc_client.py
│   │   │                # asr_grpc/ (proto stubs), tts_grpc/ (proto stubs)
│   │   ├── ws/          # handler.py (sync+streaming), vad.py (WebRTC VAD),
│   │   │                # jitter_buffer.py, registry.py (ActiveCallRegistry), denoise.py
│   │   ├── graph/       # flow.py, prompt.py, prompts/
│   │   ├── llm/         # service.py, json_stream.py, sentence_splitter.py
│   │   ├── memory/      # assembler.py, chat_history.py, redis_memory.py, store.py
│   │   ├── rag/         # retriever.py (Agentic RAG)
│   │   ├── db/          # models.py (ORM)
│   │   └── storage/     # repository.py
│   ├── llm/             # Qwen LLM 推理引擎 Dockerfile (vLLM)
│   ├── alembic/         # DB migrations (alembic.ini at root)
│   ├── alembic.ini      # Alembic config
│   ├── requirements.txt # Python dependencies
│   ├── Dockerfile       # Application image (auto alembic upgrade head)
│   ├── README.md        # Component docs
│   └── tests/           # test suite + memory/
├── mcp-server/              # MCP servers (user center backend)
│   └── java-mcp-server/ # Spring Boot 3.5 + Spring AI 1.1.6 stateless MCP server
│       ├── src/main/java/com/trans/mcp/
│       │   ├── McpApplication.java     # Entry point + tool registration
│       │   ├── model/                  # IdentityResult, CreditResult records
│       │   └── service/                # UserService, CreditService (@Tool)
│       ├── src/test/java/              # McpApplicationTests
│       ├── src/main/resources/
│       │   └── application.yaml        # MCP server config (STATELESS, /mcp endpoint)
│       ├── Dockerfile       # MCP server container
│       └── pom.xml          # Maven build
├── freeswitch/          # FreeSWITCH configs
│   ├── vars.xml         # Global variables (SIP, RTP, WebSocket URL)
│   ├── modules.conf     # mod_sofia, mod_audio_fork, mod_event_socket
│   ├── autoload_configs/    # modules.conf.xml (XML modules config)
│   ├── sip_profiles/        # internal.xml (SIP profile)
│   ├── event_socket.conf.xml  # ESL listener config
│   ├── dialplan/public.xml    # Call routing + audio_fork WebSocket
│   └── mrcp-plugin/          # UniMRCP 1.5.0 (MRCP/ASR fallback)
├── scripts/             # Startup scripts
│   ├── local.sh         # Local dev (conda): asr/tts/flow, stop, status
│   └── prod.sh          # Production deploy (Docker Compose): GPU check, ordered startup
├── voices/              # TTS voice samples
│   ├── default_female.wav
│   └── tts_test.wav
├── openspec/            # Change proposals (OpenSpec)
├── docker-compose.yml       # Base Docker Compose (infra + services)
├── docker-compose.prod.yml  # Production overrides (GPU pinning, health checks)
├── env.example              # Environment variable template
└── docs/                # design specs, implementation plans
```

### Infrastructure

- **PostgreSQL 17** with pgvector extension, schema `callbot`, 9 tables
- **Redis** for hot memory, conversation history (langchain-redis), session state
- **MinIO** for audio archiving (optional, disabled when `MINIO_ENDPOINT` empty)
- **FreeSWITCH 1.10.12** compiled from source with mod_audio_fork + mod_event_socket (ESL)
- **Java MCP Server** Spring Boot 3.5 + Spring AI 1.1.6, Java 25, Maven build
- **GPU allocation**: ASR=GPU0 (agent-asr内置), TTS=GPU1 (agent-tts内置), LLM(Qwen3.5-9B)=GPU2(:8083)
- **uvloop**: libuv C-based event loop replacing std asyncio in agent-flow (via `--loop uvloop`), reduces GC pauses under high concurrency
- **gRPC**: ASR client-streaming (:50051), TTS unary (:50052), both optional feature-flagged alongside HTTP fallback
- **ESL**: Auto-reconnect with heartbeat detection, recovers from FreeSWITCH restarts without service interruption
- **Docker Compose**: `docker-compose.yml` (base) + `docker-compose.prod.yml` (production overrides), GPU pinning, health checks, ordered startup
