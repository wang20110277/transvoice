# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

智能外呼系统 (Smart Outbound Call System) — a telephony AI platform using FreeSWITCH for SIP/RTP with mod_audio_fork WebSocket audio streaming, built-in GPU ASR/TTS inference (SenseVoice + CosyVoice3), and a LangGraph-orchestrated Python agent driving LLM-powered conversations with full streaming pipeline, barge-in support, gRPC streaming (ASR/TTS), uvloop event loop, pre-VAD audio denoising, ESL auto-reconnect + heartbeat, and Docker Compose deployment.

## Coding Conventions

- **接口命名见名知意**：HTTP/gRPC/WebSocket 接口路径和函数名必须从名字就能看出用途，不使用模糊缩写。例如 `/call/text-turn`（文本输入轮次）、`/call/audio-turn`（音频输入轮次）、`/ws/streaming-call`（流式双向通话）、`/tts/synthesize-binary`（返回二进制音频）、`/asr/audio-meta/{id}`（音频元数据查询）。
- **Python 代码规范**：遵循 PEP 8，使用 `async/await` 异步模式，type hints 必选。ASR/TTS 引擎实现 ABC 基类（`asradapter/base.py` / `ttsadapter/base.py`），通过 `config.yaml` + `importlib` 动态加载。
- **注释原则**：不写解释 WHAT 的注释（命名已自解释）。只在 WHY 不明显时加注释：隐藏约束、微妙不变量、特定 bug 的 workaround。
- **错误处理**：只在系统边界验证（用户输入、外部 API）。内部代码信任框架保证，不为不可能发生的场景加 fallback。
- **安全**：禁止 OWASP Top 10 漏洞（命令注入、XSS、SQL 注入等）。发现不安全代码立即修复。
- **不提前设计**：不为假设的未来需求添加抽象。三行相似代码优于一个过早的抽象。不做半成品实现。

## Development Workflow (OpenFlow + OpenSpec)

### 变更管理流程

使用 OpenFlow 五阶段协调开发：`proposal → brainstorming → spec → build → close`。

| 阶段 | 命令 | 说明 |
|------|------|------|
| proposal | `/openflow proposal` | 轻量提问，快速收敛需求 |
| brainstorming | `/openflow brainstorming` | 深度设计，多轮探索 |
| spec | `/openflow spec` | 生成规格文档 + 翻译为实现计划 |
| build | `/openflow build` | 调用 Superpowers 执行实现 |
| close | `/openflow close` | 验证一致性 + 归档 |

### OpenSpec 变更目录

```
openspec/
├── changes/<change-name>/     # 活跃变更
│   ├── proposal.md            # 变更提案
│   ├── design.md              # 设计决策
│   ├── specs/                 # 能力规格
│   ├── tasks.md               # 任务拆分
│   └── plan-ready.md          # 构建就绪标记
├── specs/<capability>/        # 稳定规格
│   └── spec.md
└── archive/                   # 已归档变更
```

### 流程规则

- **所有非 trivial 变更必须走 OpenFlow**：新功能、架构改动、破坏性变更必须先创建 proposal，经 brainstorming/探索后再实现。
- **单行修复/typo 可跳过**：明确的小修改直接修改代码，无需 OpenSpec 流程。
- **探索阶段不实现代码**：`/openspec-explore` 模式下只思考和分析，不写实现代码。洞察成型后创建 proposal。
- **变更完成必须归档**：`/openspec-archive-change` 将已完成变更移入 `archive/`。

## Code Intelligence (CodeGraph + Code Review Graph)

### CodeGraph — 结构化代码查询

项目已初始化 CodeGraph 索引（30K+ 节点，76K+ 边），优先使用 CodeGraph 进行结构化查询。

**工具选择规则**：

| 场景 | 工具 | 说明 |
|------|------|------|
| "X 在哪定义？" | `codegraph_search` | 比 grep 快，返回类型+位置+签名 |
| "这个功能的上下文？" | `codegraph_context` | 一次调用组合 search+node+callers+callees |
| "X 怎么到达 Y？" | `codegraph_trace` | 一调用返回完整调用路径，含动态分发跳转 |
| "谁调用这个函数？" | `codegraph_callers` | 影响分析 |
| "这个函数调用了什么？" | `codegraph_callees` | 依赖分析 |
| "改这个会影响什么？" | `codegraph_impact` | 爆炸半径分析 |
| "看几个相关符号的源码" | `codegraph_explore` | 一次调用返回多个符号源码，优于多次 node/Read |
| "目录下有什么文件？" | `codegraph_files` | 比文件系统扫描快 |
| "索引是否健康？" | `codegraph_status` | 检查索引状态 |

**使用原则**：

- **直接回答，不委派探索**：结构性问题用 2-3 次 codegraph 调用直接回答，不启动子 agent 做 grep+read 循环。
- **信任 codegraph 结果**：来自完整 AST 解析，不要用 grep 重新验证。
- **不链式调用**：需要上下文时用 `codegraph_context`（一次调用），不要 `search` → `node` → `callers` 链。
- **不循环 node**：需要多个符号源码时用 `codegraph_explore`（一次调用），不要循环 `codegraph_node`。
- **索引进后用 Read**：当响应包含 "⚠️ Some files referenced below were edited since the last index sync" 时，对列出的文件用 Read 获取准确内容。

### Code Review Graph — 代码审查

**Token 高效审查流程**：

1. 先调 `get_minimal_context(task="review changes")` 获取风险概览。
2. 低风险：`detect_changes(detail_level="minimal")` → 报告摘要 + 测试缺口。
3. 中/高风险：`detect_changes(detail_level="standard")` → 对高风险函数查 callers → 查 affected_flows。
4. 只在需要源码片段时才调 `get_review_context`。

**审查原则**：

- **每次代码变更后同步索引**：`codegraph sync` 或 CRG 的 `build_or_update_graph_tool`。
- **风险驱动审查深度**：低风险快速过，高风险深入查。
- **关注测试缺口**：高连接度节点（hub nodes）必须有测试覆盖。

## AI-Assisted Development

### Superpowers 技能系统

项目配置了 Superpowers 技能框架。技能通过 `Skill` 工具调用，加载后直接遵循。

**技能优先级**：

1. 用户显式指令（CLAUDE.md、直接请求）— 最高优先级
2. Superpowers 技能 — 覆盖系统默认行为
3. 系统默认提示 — 最低优先级

**关键技能**：

| 技能 | 触发场景 |
|------|----------|
| `superpowers:brainstorming` | 设计决策前，探索多种方案 |
| `superpowers:test-driven-development` | 实现新功能/修复 bug 时 |
| `superpowers:systematic-debugging` | 调试复杂问题时 |
| `superpowers:writing-plans` | 多步骤任务，需要规划时 |
| `superpowers:dispatching-parallel-agents` | 独立子任务可并行时 |
| `superpowers:verification-before-completion` | 实现完成后验证 |

**技能调用规则**：

- 即使只有 1% 可能性相关的技能，也要先调用检查。
- 流程技能优先（brainstorming、debugging），实现技能其次。
- 刚性技能（TDD、debugging）严格遵循；弹性技能（patterns）可适配上下文。
- 技能检查在澄清问题和任何操作之前。

### Oh-My-ClaudeCode (OMC)

**可用模式**：

| 模式 | 命令 | 说明 |
|------|------|------|
| ultrawork | `/oh-my-claudecode:ultrawork` | 深度专注执行模式 |
| autopilot | `/oh-my-claudecode:autopilot` | 自主迭代执行 |
| ralph | `/oh-my-claudecode:ralph` | 代码审查模式 |
| plan | `/oh-my-claudecode:plan` | 规划模式 |

**工作记忆 (Notepad)**：

- Priority Context：每次会话加载，存放关键上下文（<500字符）。
- Working Memory：带时间戳，7天自动清理。
- Manual：手动条目，永不清理。
- 使用 `notepad_write_priority` 写入关键信息，`notepad_write_working` 记录临时发现。

**项目记忆 (Project Memory)**：

- 持久化项目级知识（技术栈、构建、约定、结构）。
- 通过 `project_memory_write` 写入，跨会话持久化。
- 重要的用户反馈和行为偏好写入 memory 文件。

### Claude Code 通用规范

- **先读后改**：编辑文件前必须先 Read。优先 Edit 而非 Write。
- **任务追踪**：非 trivial 任务用 TaskCreate 创建任务列表，完成后立即 TaskUpdate。
- **并行调用**：独立操作并行发起工具调用，依赖操作按序执行。
- **权限敏感操作**：破坏性操作（删除、force push）必须确认。不跳 git hooks。
- **上下文管理**：对话过长时系统自动压缩，不需要提前收尾。

### GLM Model Notes

当前使用 **GLM-5.1** 模型驱动。注意事项：

- **工具调用能力**：GLM-5.1 支持并行工具调用，充分利用此特性提高效率。
- **中文理解**：项目为中英混合代码库，GLM-5.1 对中文指令和注释理解良好，可直接使用中文交流。
- **CodeGraph 信任**：CodeGraph 的 AST 解析结果比模型推测更准确，始终优先信任 CodeGraph。
- **Claude 家族模型对照**：如需切换模型，参考 Claude 家族 — Opus 4.7 (`claude-opus-4-7`)、Sonnet 4.6 (`claude-sonnet-4-6`)、Haiku 4.5 (`claude-haiku-4-5-20251001`)。构建 AI 应用默认使用最新最强模型。

## Code Review Process

### 自审流程（实现完成后）

1. 实现完成后运行 `/code-review` 或 `/review` 进行自审。
2. 使用 CRG 工具链：`get_minimal_context` → `detect_changes` → 按风险深入。
3. 关注点：
   - **正确性**：逻辑缺陷、边界条件、竞态条件
   - **安全性**：注入、敏感数据泄露、认证绕过
   - **性能**：不必要的同步、资源泄漏、N+1 查询
   - **测试覆盖**：关键路径（WebSocket 流式、ESL 断连重连、barge-in）必须有测试
   - **接口命名**：遵循"见名知意"规范

### 审查清单

- [ ] 变更是否影响流式通话路径（WebSocket → JitterBuffer → VAD → ASR → LLM → TTS → OutputBuffer）
- [ ] ESL 连接管理（auto-reconnect、heartbeat）是否正确
- [ ] asyncio 并发安全（共享状态是否正确使用 Lock/Event）
- [ ] 新增配置项是否使用 `CALLBOT_` 前缀 + pydantic-settings
- [ ] 跨 biz_type 隔离是否正确（Redis key prefix、TTS voice profile）
- [ ] 错误路径是否正确清理资源（WebSocket 连接、gRPC channel、ESL session）

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

**启动顺序（必须严格遵守）**：`fs → asr → tts → flow`，每步等前一个服务就绪再启动下一个。FreeSWITCH 必须先于 agent-flow，否则 ESL 连接失败；ASR/TTS 必须先于 agent-flow，否则首轮通话 TTS/ASR 请求超时。

**FreeSWITCH 日志**：`/Users/lindaw/freeswitch/var/log/freeswitch/freeswitch.log`（mod_audio_fork 诊断、音频播放问题排查必查此日志）

```bash
# 重启所有服务（按顺序逐个启动）
./scripts/local.sh stop           # 先停全部
./scripts/local.sh fs             # 1. FreeSWITCH (SIP/RTP)
./scripts/local.sh asr            # 2. ASR (GPU 推理)
./scripts/local.sh tts            # 3. TTS (GPU 推理)
./scripts/local.sh flow           # 4. agent-flow (最后启动，依赖以上全部)

# 单独管理
./scripts/local.sh status         # 检查运行状态
./scripts/local.sh stop           # 停止全部

# 仅重启 agent-flow（其他服务不变）
./scripts/local.sh stop flow && ./scripts/local.sh flow
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
    ├─ Dialplan: set variables → answer → playback silence_stream://-1 (无限静音保活)
    ├─ ESL CHANNEL_ANSWER → agent-flow uuid_audio_fork start → FS connects WebSocket to /media/{uuid}
    │   ├─ Node ①: agent-asr (:8080) 内置 GPU 推理 → 识别文本
    │   ├─ Node ②/③: MCP client → java-mcp-server (:9090) 用户中心
    │   ├─ Node ⑥: Qwen LLM (GPU2 :8083) → 流式回复文本
    │   └─ Node ⑦: agent-tts (:8081) 内置 GPU 推理 → 句级合成音频 → TTSOutputBuffer → 回传 FreeSWITCH
    └─ ESL CHANNEL_HANGUP → uuid_audio_fork stop → ActiveCallRegistry 取消通话 → 清理资源
```

Data flow per turn (event-driven, dynamic uuid_audio_fork):
```
[事件驱动流程]
来电: FreeSWITCH 拨号计划 answer → playback silence_stream://-1 → 触发 CHANNEL_ANSWER 事件
注册: ESL handler 提取 uuid/biz_type/user_key → ActiveCallRegistry.register()
启动: esl.audio_fork_start() → FS 连接 WebSocket /media/{uuid}
音频: JitterBuffer → Denoiser降噪 → WebRTC VAD → ASR → 识别文本
并行: MCP身份查询 ‖ 记忆召回 ‖ RAG检索 (fan-out 并发)
决策: LLM 流式输出 → IncrementalJSONParser → SentenceSplitter → 句级文本
合成: 每句并行 TTS(gRPC/HTTP/WS) → WAV→PCM → _resample_pcm(22050→16000) → TTSOutputBuffer 稳态30ms帧(960B) → WebSocket → FreeSWITCH
打断: 用户说话检测 → ESL uuid_break → 取消流式任务 → 新一轮对话
挂断: ESL CHANNEL_HANGUP → audio_fork_stop → ActiveCallRegistry 取消 → 清理资源
```

### Three Components

**agent-asr** — FastAPI + gRPC + WebSocket service with pluggable ASR engines and built-in GPU inference. Loads SenseVoice (FunASR) model directly in-process, no separate inference server needed. Receives audio from agent-flow, runs recognition, uploads to MinIO. HTTP endpoints: `POST /asr/recognize-speech`, `GET /asr/audio-meta/{call_id}`, `GET /healthz`. gRPC endpoint: `ASRService.StreamingRecognize` (client-streaming, port 50051). WebSocket endpoint: streaming audio recognition via `ws_server.py`.

**agent-tts** — FastAPI + gRPC + WebSocket service with pluggable TTS engines and built-in GPU inference. Loads CosyVoice3 model directly in-process, no separate inference server needed. Receives text from orchestrator, synthesizes audio, uploads to MinIO. Disk cache keyed by voice+text hash, biz_type voice profiles. HTTP endpoints: `POST /tts/synthesize-binary` (binary audio response), `POST /tts/synthesize-json` (JSON with base64 audio + minio_key), `GET /healthz`. gRPC endpoint: `TTSService.Synthesize` (unary, port 50052). WebSocket endpoint: streaming text-to-speech via `ws_server.py`.

**agent-flow** — FastAPI WebSocket service (uvloop event loop). **Event-driven audio fork**: ESL subscribes to `CHANNEL_ANSWER` + `CHANNEL_HANGUP`. On CHANNEL_ANSWER: registers call in `ActiveCallRegistry`, calls `esl.audio_fork_start()` → FreeSWITCH connects WebSocket to `/media/{uuid}` for bidirectional 16kHz audio. On CHANNEL_HANGUP: calls `esl.audio_fork_stop()` + `cancel_call()` for cleanup. Streaming mode: LLM tokens streamed via `IncrementalJSONParser`, split into sentences by `SentenceSplitter`, each sentence synthesized by TTS in parallel (gRPC, HTTP, or WebSocket), resampled from 22050→16000 via `_resample_pcm()`, PCM audio paced through `TTSOutputBuffer` at steady 30ms frames (960B @ 16kHz). TTSOutputBuffer 无 TTS 数据时自动填充静音帧保活（silence_timeout=120s），与拨号计划 `silence_stream://-1` 双重保活。Barge-in: concurrent audio receive during AI speech with WebRTC VAD detection, ESL `uuid_break` to stop FreeSWITCH playback. Input audio smoothed through `JitterBuffer`, pre-VAD denoising via configurable denoiser (highpass/noisereduce/rnnoise). Endpoints: `GET /healthz`, `WS /media/{uuid}`. ASR/TTS gRPC streaming optional via feature flags (`CALLBOT_ASR_USE_GRPC`, `CALLBOT_TTS_USE_GRPC`). WebSocket streaming as third transport via `asr_ws_client.py` and `tts_ws_client.py`.

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

Current engines: SenseVoice (ASR, built-in FunASR GPU inference), Streaming (ASR, WebSocket streaming), VibeVoice (ASR, remote HTTP), CosyVoice (TTS, built-in CosyVoice3 GPU inference), VibeVoice (TTS, remote HTTP).

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
- **VAD**: `CALLBOT_VAD_AGGRESSIVENESS` (0-3), `CALLBOT_VAD_SILENCE_FRAMES` (default 15), `CALLBOT_VAD_MIN_AUDIO_BYTES` (default 3200)
- **Barge-in**: `CALLBOT_BARGE_IN_MIN_AUDIO_BYTES` (default 1600, lower than VAD for faster reaction)
- **Media**: `CALLBOT_MEDIA_SAMPLE_RATE` (default 16000), 全链路 16kHz，帧大小 960B (30ms @ 16kHz 16-bit)，TTS 输出 22050Hz 经 `_resample_pcm()` 降采样到 16kHz，FreeSWITCH 内部下采样到 G.711 8kHz
- **Jitter Buffer**: `CALLBOT_JITTER_TARGET_DEPTH` (default 3), `CALLBOT_JITTER_MAX_DEPTH` (default 10)
- **Denoise**: `CALLBOT_DENOISE_ENABLED` (`""` disabled, `"highpass"`, `"noisereduce"`, `"rnnoise"`), `CALLBOT_DENOISE_HIGHPASS_CUTOFF` (default 200.0 Hz)
- **Audio gain**: `CALLBOT_AUDIO_GAIN` (default 1.0, pre-ASR amplification for quiet SIP audio)
- **ASR WebSocket**: `CALLBOT_ASR_USE_WS` (default false), `CALLBOT_ASR_WS_URL` (default `ws://127.0.0.1:8080/ws/asr/streaming-recognize`)
- **TTS WebSocket**: `CALLBOT_TTS_USE_WS` (default false), `CALLBOT_TTS_WS_URL` (default `ws://127.0.0.1:8081/ws/tts/streaming-synthesize`)
- **Streaming ASR**: `CALLBOT_ASR_STREAMING_ENABLED` (default false, engine-level streaming)
- **Streaming TTS**: `CALLBOT_TTS_STREAMING_ENABLED` (default false, chunk-level streaming)
- **TTS pre-buffer**: `CALLBOT_TTS_PREBUFFER_FRAMES` (default 0, accumulate N 30ms frames before playback)
- **TTS skip**: `CALLBOT_TTS_SKIP` (default false, local testing without GPU)
- **Sentence splitter**: `CALLBOT_SPLITTER_MIN_LENGTH` (default 2), `CALLBOT_SPLITTER_FLUSH_TIMEOUT` (default 0.2), `CALLBOT_SPLITTER_EAGER_FIRST` (default true)
- **CosyVoice device**: `COSYVOICE_DEVICE` (engine-level, `cpu`/`mps`/`auto`, local.sh defaults to `cpu` on Mac to avoid MPS fallback overhead)
- **ASR gRPC**: `CALLBOT_ASR_USE_GRPC` (default false), `CALLBOT_ASR_GRPC_TARGET` (default `127.0.0.1:50051`)
- **TTS gRPC**: `CALLBOT_TTS_USE_GRPC` (default false), `CALLBOT_TTS_GRPC_TARGET` (default `127.0.0.1:50052`)
- **uvloop**: enabled via Dockerfile CMD `--loop uvloop`, no config needed
- **MCP Server**: `application.yaml` with `spring.ai.mcp.server.*` properties, STATELESS protocol, WebMVC transport, port 9090

### Key Orchestrator Modules

| Module | Role |
|--------|------|
| `main.py` | FastAPI app with lifespan init, ESL lifecycle, `WS /media/{uuid}` (event-driven audio fork), `GET /healthz` |
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
| `src/clients/asr_ws_client.py` | ASR WebSocket client — streaming audio recognition via WebSocket |
| `src/clients/tts_ws_client.py` | TTS WebSocket client — streaming text-to-speech via WebSocket |
| `src/clients/asr_grpc/` | Generated gRPC proto stubs (asr_pb2, asr_pb2_grpc) |
| `src/clients/tts_grpc/` | Generated gRPC proto stubs (tts_pb2, tts_pb2_grpc) |
| `src/ws/handler.py` | WebSocket handlers: `CallWebSocketHandler` (sync) + `StreamingCallHandler` (streaming + barge-in) |
| `src/ws/vad.py` | WebRTC VAD for endpointing and barge-in speech detection |
| `src/ws/denoise.py` | Configurable pre-VAD denoiser (highpass/noisereduce/rnnoise), factory via `CALLBOT_DENOISE_ENABLED` |
| `src/ws/jitter_buffer.py` | `JitterBuffer` (input smoothing, 960B frames @ 16kHz) + `TTSOutputBuffer` (steady 30ms frame delivery) |
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
| `src/storage/minio_storage.py` | MinIO object storage client — audio file upload/download by biz_type |

### Project Structure

```
aiphone/
├── agent-asr/           # ASR service (FastAPI + gRPC + WebSocket, built-in GPU inference)
│   ├── asradapter/      # main.py, base.py, config.py, requirements.txt
│   │   ├── engines/     # sensevoice/ (GPU), streaming/ (WebSocket), vibevoice/ (remote HTTP)
│   │   ├── grpc_server.py  # gRPC ASR service (client-streaming, :50051)
│   │   ├── ws_server.py    # WebSocket ASR service (streaming recognition)
│   │   └── proto/       # asr.proto + generated stubs (asr_pb2, asr_pb2_grpc)
│   ├── models/          # SenseVoiceSmall/ (local model weights)
│   ├── deploy/          # systemd units (sensevoice-asr.service, vibevoice-asr.service)
│   ├── Dockerfile       # PyTorch GPU image, model download
│   ├── README.md        # Component docs
│   └── tests/           # test_base, test_main, test_storage, engines/*/
├── agent-tts/           # TTS service (FastAPI + gRPC + WebSocket, built-in GPU inference)
│   ├── ttsadapter/      # main.py, base.py, config.py, requirements.txt
│   │   ├── engines/     # cosyvoice/ (CosyVoice3 GPU), vibevoice/ (remote HTTP)
│   │   ├── grpc_server.py  # gRPC TTS service (unary, :50052)
│   │   ├── ws_server.py    # WebSocket TTS service (streaming synthesis)
│   │   └── proto/       # tts.proto + generated stubs (tts_pb2, tts_pb2_grpc)
│   ├── models/          # CosyVoice3-0.5B/ (local model weights)
│   ├── deploy/          # systemd units (cosyvoice-tts.service, vibevoice-tts.service)
│   ├── Dockerfile       # PyTorch GPU image, model download
│   ├── README.md        # Component docs
│   └── tests/           # test_base, test_main, test_storage, engines/*/
├── agent-flow/  # LangGraph 7-node pipeline (FastAPI HTTP + WebSocket)
│   ├── main.py          # FastAPI entry point (HTTP + WebSocket + ESL lifecycle)
│   ├── src/             # 核心源码 (PYTHONPATH includes src/)
│   │   ├── config.py    # pydantic-settings (ESL/VAD/jitter/barge-in configs)
│   │   ├── database.py  # SQLAlchemy async engine
│   │   ├── clients/     # mcp.py, tts.py, asr.py, esl.py
│   │   │                # tts_grpc_client.py, asr_grpc_client.py
│   │   │                # tts_ws_client.py, asr_ws_client.py
│   │   │                # asr_grpc/ (proto stubs), tts_grpc/ (proto stubs)
│   │   ├── ws/          # handler.py (sync+streaming), vad.py (WebRTC VAD),
│   │   │                # jitter_buffer.py, registry.py (ActiveCallRegistry), denoise.py
│   │   ├── graph/       # flow.py, prompt.py, prompts/{biz_type}.yaml
│   │   ├── llm/         # service.py, json_stream.py, sentence_splitter.py
│   │   ├── memory/      # assembler.py, chat_history.py, redis_memory.py, store.py
│   │   ├── rag/         # retriever.py (Agentic RAG)
│   │   ├── db/          # models.py (ORM)
│   │   └── storage/     # repository.py, minio_storage.py
│   ├── llm/             # Qwen LLM 推理引擎 Dockerfile (vLLM)
│   ├── alembic/         # DB migrations (versions/0001_initial_schema.py)
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
│   ├── dialplan/public.xml    # Call routing: answer → playback silence_stream://-1 (无限静音保活, ESL 事件驱动 audio_fork)
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
└── env.example              # Environment variable template
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
- **WebSocket**: Third transport for ASR/TTS streaming (`ws_server.py` in agent-asr/agent-tts, `asr_ws_client.py`/`tts_ws_client.py` in agent-flow)
- **ESL**: Auto-reconnect with heartbeat detection, subscribes to CHANNEL_ANSWER + CHANNEL_HANGUP; dynamic `uuid_audio_fork` start/stop per call lifecycle
- **Docker Compose**: `docker-compose.yml` (base) + `docker-compose.prod.yml` (production overrides), GPU pinning, health checks, ordered startup
