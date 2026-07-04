# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

智能外呼系统 (Smart Outbound Call System) — a telephony AI platform using FreeSWITCH for SIP/RTP with mod_audio_fork WebSocket audio streaming, built-in GPU ASR/TTS inference (SenseVoice + CosyVoice3) plus cloud-based EdgeTTS (no GPU), FSMN-VAD server-side endpoint detection (agent-asr) + RMSGate barge-in (agent-flow), WebRTC APM (AEC +ANS + AGC), and a LangGraph-orchestrated Python agent driving LLM-powered conversations with full streaming pipeline, barge-in support, uvloop event loop, pre-VAD audio denoising, ESL auto-reconnect + heartbeat, Docker Compose deployment, dual-channel call recording, multi-tenant `(tenant_id, biz_type, scenario)` isolation, and a Next.js management console (`console/`).

## Coding Conventions

- **接口命名见名知意**：HTTP/WebSocket 接口路径和函数名必须从名字就能看出用途，不使用模糊缩写。例如 `/calls/{uuid}/archive-recording`（录音归档）、`/ws/asr/streaming-recognize`（WebSocket 流式识别）、`/ws/tts/streaming-synthesize`（WebSocket 流式合成）、`/media/{uuid}`（mod_audio_fork 双向音频流）。
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
├── changes/<change-name>/     # 活跃变更（proposal.md / design.md / specs/ / tasks.md / plan-ready.md）
│   └── archive/<date>-<name>/ # 已归档变更（归档后 specs/ 内容并入下方 specs/）
└── specs/<capability>/        # 稳定规格（8 个能力）
    ├── spec.md                #   call-recording, call-records-console, call-records-persistence,
    └── ...                    #   call-task-management, inbound-routing, prompt-config-{consumption,management}, tenant-management
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

- [ ] 变更是否影响流式通话路径（WebSocket → JitterBuffer → WebRTCAPM/Denoise → ASR 全量喂(服务端 FSMN-VAD 分段 → on_final)→ LLM → TTS → OutputBuffer）
- [ ] ESL 连接管理（auto-reconnect、heartbeat）是否正确
- [ ] asyncio 并发安全（共享状态是否正确使用 Lock/Event）
- [ ] 新增配置项是否使用 `CALLBOT_` 前缀 + pydantic-settings（MinIO 也走 `CALLBOT_MINIO_*` 前缀，由 pydantic 加载；无前缀 `MINIO_*` 已废弃，仅 console 侧 Node.js 直读 `process.env.MINIO_*`）
- [ ] 多租户隔离：`tenant_id` 是否全程透传（dialplan DID → `_resolve_inbound_route` → registry → graph state → prompt 三维加载），prompt 加载是否用完整 `(tenant_id, biz_type, scenario)` 三元组，不得 `default` 兜底替代 tenant/scenario
- [ ] Redis key / prompt key 是否带 `tenant_id`（`cb:{tenant_id}:{biz_type}:...`、`cb:prompt:{tenant_id}:{biz_type}:{scenario}`）
- [ ] 跨 biz_type 隔离是否正确（TTS voice profile per biz_type）
- [ ] 录音归档：fire-and-forget `_archive_recording` 是否强引用防 GC（`_ongoing_archives` set）、MinIO 未配置时是否静默跳过、`recording_notice_played` 是否随提示音开关置位
- [ ] 错误路径是否正确清理资源（WebSocket 连接、ESL session）

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

**启动顺序（必须严格遵守）**：`基础设施(Docker) → fs → asr → tts → mcp → flow → console`，每步等前一个就绪再启动下一个。依赖链：

- **Docker 基础服务（pg/redis/minio）最先** —— flow + console 依赖 PG + Redis，console 录音回放依赖 MinIO；`docker compose up -d` 后等 `callbot-postgres` healthcheck 转 healthy（~10s）。
- **fs → flow**：FreeSWITCH 先于 agent-flow，否则 ESL 连接失败。
- **asr/tts → flow**：ASR/TTS 先于 agent-flow，否则首轮通话 ASR/TTS 请求超时。
- **mcp → flow**：flow 节点 ②/③ 调用 MCP 用户中心（`CALLBOT_MCP_SERVER_URL`）。
- **console 最后**：依赖上述全部（DB/Redis/MinIO）。

**FreeSWITCH 日志**：`/Users/lindaw/freeswitch/var/log/freeswitch/freeswitch.log`（mod_audio_fork 诊断、音频播放问题排查必查此日志）

```bash
# ── 第 0 步：Docker 基础服务（首次或重启机器后）──
docker compose up -d                 # 启动 postgres + redis + minio
docker compose ps                    # 等 callbot-postgres (healthy)

# ── 一次性启动全部应用服务（推荐，脚本内部按顺序逐个启动并等待就绪）──
./scripts/local.sh                   # = fs asr tts mcp flow console 全部

# ── 或逐个启动（便于排查）──
./scripts/local.sh stop              # 先停全部
./scripts/local.sh fs                # 1. FreeSWITCH (SIP/RTP, 5060/8021)
./scripts/local.sh asr               # 2. ASR (SenseVoice GPU, 8080)
./scripts/local.sh tts               # 3. TTS (CosyVoice GPU, 8081)
./scripts/local.sh mcp               # 4. MCP Server (Spring Boot, 9090)
./scripts/local.sh flow              # 5. agent-flow (8000)
./scripts/local.sh console           # 6. Console (Next.js pm2, 3001)

# console 首次需先装 pm2（nvm 用户级 prefix，无需 sudo）
npm install -g pm2 && pm2 install pm2-logrotate

# 单独管理
./scripts/local.sh status            # 检查运行状态
./scripts/local.sh stop              # 停止全部

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
cd agent-mcp/java-mcp-server && JAVA_HOME=/opt/homebrew/opt/openjdk@21 ./mvnw clean compile

# Run (port 9090)
cd agent-mcp/java-mcp-server && JAVA_HOME=/opt/homebrew/opt/openjdk@21 ./mvnw spring-boot:run
```

## Architecture

```
SIP Caller → FreeSWITCH (mod_sofia, SIP/RTP)
    ├─ Dialplan: catch-all ^(\d+)$ → answer → playback silence_stream://-1 (无限静音保活)
    │            (DID/三元组不在 dialplan 硬编码，由 agent-flow 查 inbound_route 解析)
    ├─ ESL CHANNEL_ANSWER → agent-flow 解析 DID → _resolve_inbound_route() → (tenant_id, biz_type, scenario)
    │   ├─ ActiveCallRegistry.register() + insert_call_session(recording_notice_played)
    │   ├─ uuid_audio_fork start → FS connects WebSocket to /media/{uuid}
    │   ├─ Node ①: agent-asr (:8080) 内置 GPU 推理 → 识别文本
    │   ├─ Node ②/③: MCP client → java-mcp-server (:9090) 用户中心
    │   ├─ Node ⑥: Qwen LLM (GPU2 :8083) → 流式回复文本
    │   ├─ Node ⑦: agent-tts (:8081) 内置 GPU 推理 → 句级合成音频 → TTSOutputBuffer → 回传 FreeSWITCH
    │   └─ FreeSWITCH uuid_record 录双声道 (L=caller / R=AI PCM)
    └─ ESL CHANNEL_HANGUP → uuid_audio_fork stop → ActiveCallRegistry 取消通话
        └─ fire-and-forget _archive_recording (延迟3s 读 FS wav → 上传 MinIO → insert_artifact)

console (:3001) ⇄ PostgreSQL callbot schema + Redis (prompt 草稿/发布/回滚, DID 路由, 外呼任务, 通话记录, 租户运营)
```

Data flow per turn (event-driven, dynamic uuid_audio_fork):
```
[事件驱动流程]
来电: FreeSWITCH 拨号计划 catch-all ^(\d+)$ → answer → playback silence_stream://-1 → 触发 CHANNEL_ANSWER 事件
路由: ESL handler 提取 uuid/DID/user_key → _resolve_inbound_route(DID) → (tenant_id, biz_type, scenario)
注册: ActiveCallRegistry.register(uuid, biz_type, user_key, tenant_id, scenario) + insert_call_session
启动: esl.audio_fork_start() → FS 连接 WebSocket /media/{uuid}
录音: FreeSWITCH uuid_record 录双声道（audio_fork_start 之后发起，record bug 排在 WRITE_REPLACE 之后 tap 到 AI 下行）
音频: JitterBuffer → WebRTCAPM(AEC/NS/AGC)或 Denoiser 降噪 → VAD(WebRTC/Silero) → ASR → 识别文本
提示词: get_system_prompt(tenant_id, biz_type, scenario) Redis(5min)→DB 降级 + render.py 变量渲染
并行: MCP身份查询 ‖ 记忆召回 ‖ RAG检索 (fan-out 并发)
决策: LLM 流式输出 → IncrementalJSONParser → SentenceSplitter → 句级文本
合成: 每句并行 TTS(HTTP/WS) → WAV→PCM → _resample_pcm(22050→16000) → TTSOutputBuffer 稳态30ms帧(960B) → WebSocket → FreeSWITCH
打断: 用户说话检测 → TTS buffer 清空（不调用 uuid_break，避免终止 dialplan playback）→ 冷却期防误触发 → 新一轮对话
挂断: ESL CHANNEL_HANGUP → audio_fork_stop → record_stop → ActiveCallRegistry 取消 → _archive_recording(读 FS wav → MinIO) → 清理资源
```

### Five Components

**agent-asr** — FastAPI + WebSocket service with pluggable ASR engines and built-in GPU inference. Loads SenseVoice (FunASR) model directly in-process, no separate inference server needed. Receives audio from agent-flow, runs recognition, uploads to MinIO. Endpoints: `GET /healthz`, `WS /ws/asr/streaming-recognize` via `ws_server.py` (FSMN-VAD 流式分段 → 段级 recognize → 多 final 主动推).

**agent-tts** — FastAPI + WebSocket service with pluggable TTS engines and built-in GPU inference. Loads CosyVoice3 model directly in-process, no separate inference server needed. Receives text from orchestrator, synthesizes audio, uploads to MinIO. Disk cache keyed by voice+text hash, biz_type voice profiles. Endpoints: `GET /healthz`, streaming text-to-speech WebSocket via `ws_server.py`.

**agent-flow** — FastAPI WebSocket service (uvloop event loop). **Event-driven audio fork**: ESL subscribes to `CHANNEL_ANSWER` + `CHANNEL_HANGUP`. On CHANNEL_ANSWER: parses DID via `_resolve_inbound_route()` → `(tenant_id, biz_type, scenario)`, registers call in `ActiveCallRegistry`, writes `call_session` row (`recording_notice_played`), calls `esl.audio_fork_start()` → FreeSWITCH connects WebSocket to `/media/{uuid}` for bidirectional 16kHz audio. On CHANNEL_HANGUP: calls `esl.audio_fork_stop()` + `cancel_call()` + fire-and-forget `_archive_recording` (delay 3s read FS wav → MinIO upload → `insert_artifact`). **Prompt loading**: three-dimension `get_system_prompt(tenant_id, biz_type, scenario)` — Redis cache `cb:prompt:{tenant_id}:{biz_type}:{scenario}` (5min TTL) → PostgreSQL `callbot.prompt_config` two-level fallback via `prompt_config.py`, then `render.py` variable substitution; prompt content logged per turn. **Call recording**: FreeSWITCH `uuid_record` records dual-channel PCM (L=caller, R=AI TTS) for the whole call — started by agent-flow right after `audio_fork_start` (`RECORD_STEREO=true`), the record bug is registered after mod_audio_fork's `WRITE_REPLACE` bug so it taps the dubbed AI downstream; `CALLBOT_RECORDINGS_DIR/{uuid}.wav` is read by `_archive_recording` on hangup. Streaming mode: LLM tokens streamed via `IncrementalJSONParser`, split into sentences by `SentenceSplitter`, each sentence synthesized by TTS in parallel (WebSocket), resampled from 22050→16000 via `_resample_pcm()`, PCM audio paced through `TTSOutputBuffer` at steady 30ms frames (960B @ 16kHz). TTSOutputBuffer 无 TTS 数据时自动填充静音帧保活（silence_timeout=120s），与拨号计划 `silence_stream://-1` 双重保活。Barge-in: concurrent audio receive during AI speech with RMSGate (RMS+SNR 自适应门禁) detection, clears `TTSOutputBuffer` (not `uuid_break`) to avoid terminating dialplan playback, followed by cooldown period to prevent residual noise false positives. Input audio smoothed through `JitterBuffer`, pre-VAD audio processing via WebRTCAPM (AEC + NS + AGC, `audio_processing.py`) or configurable denoiser (highpass/noisereduce/rnnoise). Endpoints: `GET /healthz`, `WS /media/{uuid}`, `POST /calls/{uuid}/archive-recording` (手动录音归档兜底——自动归档在 MinIO 不可用时静默跳过，本接口事后补归档：读 FS wav → upload_recording → insert_artifact，404/409/410/502 状态码区分未找到/已归档/文件丢失/MinIO 不可用). ASR/TTS 均走 WebSocket(`asr_ws_client.py` / `tts_ws_client.py`)：ASR 经 FSMN-VAD 分段后回推 final → on_final 触发轮次；TTS 句级并发合成。

**java-mcp-server** — Spring Boot 4.0 + Spring AI 2.0.0 (GA) stateless MCP server (WebMVC transport). Serves as the user center backend for orchestrator nodes ② and ③. Uses `@McpTool`/`@McpToolParam` annotations (from `spring-ai-mcp-annotations`) with `annotation-scanner` auto-detection, no manual `ToolCallbackProvider` bean needed. Exposes two MCP tools: `user_identity_query` (phone + biz_type → user_id, phone_masked, id_card_last_four) and `user_credit_query` (user_id → credit_qualified, risk_level). Endpoints: `POST /mcp` (MCP 协议) + `GET /healthz` (探活，供 scripts/local.sh 的 stop_svc 判活) on port 9090.

**console** — Next.js 15 (App Router) + Drizzle ORM + Better Auth management console (port 3001, `console/server/`). Shares the same `callbot` PostgreSQL schema and Redis instance as agent-flow — publish/rollback directly deletes Redis key `cb:prompt:{tenant_id}:{biz_type}:{scenario}` for zero-latency config propagation. Capabilities: three-dimension prompt management (draft/publish/version-rollback/clone/variable-render test), DID inbound route CRUD (`callbot.inbound_route`), outbound call task definitions (`callbot.call_task`) — execution by agent-flow `OutboundExecutor` (tick 轮询 + 时段/并发控制 + CAS 认领 + originate + redial), read-only call records list + detail + recording replay (MinIO presigned URL), and multi-tenant + RBAC (`prompt:*` / `route:*` / `calltask:*` / `call:view` / `tenant:*`). Data model: Drizzle maps `callbot.*` tables read-only (same snake_case column names as agent-flow SQLAlchemy); `prompt_config` / `prompt_version` / `inbound_route` / `call_task` are built by agent-flow alembic, `console.*` (Better Auth + tenant/user_tenant/session) built by console migrations. DID routing: triple `(tenant_id, biz_type, scenario)` resolved by agent-flow at CHANNEL_ANSWER (not hardcoded in dialplan), so adding DIDs/tenants/scenarios in console takes effect immediately without touching FreeSWITCH.

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

The `(tenant_id, biz_type, scenario, user_key)` tuple threads through the whole pipeline: ① loads the system prompt via `get_system_prompt(tenant_id, biz_type, scenario)` (Redis→DB) + `render.py` variable substitution; turns are persisted to PG via fire-and-forget `fire_insert_turn` (`persistence_helpers.py`).

Parallel fan-out: nodes ② mcp_identity, ④ recall_memory, ⑤ rag_retrieve execute concurrently after ① receive_asr.

**Streaming mode** (WebSocket path): `run_pre_llm_phase()` runs ① + parallel fan-out, then `run_streaming_pipeline()` streams LLM tokens through `SentenceSplitter`, spawning parallel TTS tasks per sentence with `audio_callback(pcm, index)` for ordered delivery via `TTSOutputBuffer`.

### Engine Plugin Pattern (ASR & TTS)

1. `asradapter/base.py` / `ttsadapter/base.py` defines ABC (`ASREngine` / `TTSEngine`)
2. `asradapter/engines/{name}/engine.py` implements ABC, exports `Engine = ConcreteClass`
3. `asradapter/config.yaml` / `ttsadapter/config.yaml` selects active engine by name
4. `asradapter/config.py` / `ttsadapter/config.py` loads via `importlib`

To add a new engine: create engine directory + `engine.py` implementing the ABC, update `config.yaml`.

Current engines: SenseVoice (ASR, built-in FunASR GPU inference), Streaming (ASR, WebSocket streaming), VibeVoice (ASR, remote HTTP), CosyVoice (TTS, built-in CosyVoice3 GPU inference), EdgeTTS (TTS, Microsoft Edge online, no GPU), VibeVoice (TTS, remote HTTP).

### Multi-Tenant Four-Dimension Isolation

Isolation key is the `(tenant_id, biz_type, scenario)` triple (plus `user_key` for per-user partitioning). Three biz_types: `customer_service`, `collection`, `marketing`. Isolated at:
- TTS: voice profiles per engine (`BIZ_TYPE_PROFILES` dict keyed by biz_type — voice_id/speed/volume/pitch)
- Redis: key prefix `cb:{tenant_id}:{biz_type}:...`; prompt cache key `cb:prompt:{tenant_id}:{biz_type}:{scenario}` (5min TTL)
- PostgreSQL: `tenant_id` + `biz_type` columns on business tables; sharding strategy: 单表起步，后期 Citus/pgcat 水平扩展，分布键 `user_id`（非 biz_type / tenant_id）
- Prompts: `callbot.prompt_config` keyed by `(tenant_id, biz_type, scenario)` UNIQUE; `prompt_version` snapshot for rollback; loaded via Redis→DB two-level fallback (`prompt_config.py`) + variable rendering (`render.py`); managed by console (`console/server`)
- Inbound routing: `callbot.inbound_route` maps DID/号段 → `(tenant_id, biz_type, scenario)`, resolved by agent-flow at CHANNEL_ANSWER
- Outbound tasks: `callbot.call_task` (任务定义) + `call_target` (待拨号码队列 + 每号码 render 变量) — agent-flow `OutboundExecutor` 已实现执行引擎：tick 轮询 (`outbound_scheduler_tick_sec`) + `allowed_hours` 时段调度 + per-task `concurrent_limit`/全局并发 + CAS 认领 (pending→dialing) + `originate` + `redial` (按 Hangup-Cause + `redial_strategy` 重拨)；originate 注入 ai_outbound 三元组 + `call_task_id`/`call_target_id`/`user_key` channel vars，摘机触发 CHANNEL_ANSWER 复用 inbound 管线；`call_target.vars`（TEXT，格式 `key:value|key:value`）摘机经 `render.parse_call_target_vars()` 解析 → `ActiveCallRegistry.call_target_vars` → graph state `call_task_vars`，每号码渲染进各自话术；号码清单由 console 结构化 CSV 导入（5 列：序号|业务类型|手机号|客户id|vars）+ 占位符覆盖率校验，`customer_id` 仅展示/审计不入渲染
- Credit query: only marketing biz_type
- Console RBAC: `prompt:*` / `route:*` / `calltask:*` / `call:view` / `tenant:*`, scoped per `tenant_id` (cross-tenant returns 404 to avoid existence leakage); `session.active_tenant_id ?? user.tenant_id ?? 'default'`

### Agentic RAG (node ⑤)

Full adaptive + corrective RAG inside `rag_retrieve_node`:
1. **Adaptive** — `should_retrieve()`: LLM decides if query needs knowledge base (skips greetings/closings)
2. **Retrieve** — `retrieve_scripts()`: pgvector cosine similarity on `callbot.script_library`
3. **Grade** — `grade_documents()`: LLM evaluates each script's relevance
4. **Rewrite** — `rewrite_query()`: if all docs irrelevant, LLM rewrites query and retries (max 2 retries)

### Configuration

- **Orchestrator**: `pydantic-settings` with `CALLBOT_` env prefix, reads `.env`
- **ASR/TTS**: `config.yaml` for engine name + env vars for model paths, API URLs and MinIO
- **MinIO** (agent-flow): `CALLBOT_MINIO_ENDPOINT`, `CALLBOT_MINIO_ACCESS_KEY`, `CALLBOT_MINIO_SECRET_KEY`, `CALLBOT_MINIO_BUCKET`, `CALLBOT_MINIO_SECURE` (pydantic-settings 加载，endpoint 为空时禁用归档)；console 侧 Node.js 仍直读 `process.env.MINIO_*`（同实例同 bucket，生成 presigned URL）
- **ASR model**: `MODEL_DIR` (SenseVoice path), `SENSEVOICE_LANGUAGE`
- **TTS model**: `MODEL_DIR` (CosyVoice3-0.5B path), `COSYVOICE_RUNTIME`, `VOICES_DIR`, `TTS_CACHE_DIR`
- **Remote engines**: `VIBEVOICE_ASR_API_URL`, `VIBEVOICE_TTS_API_URL`
- **RAG**: `CALLBOT_RAG_TOP_K` (default 3), `CALLBOT_RAG_SIMILARITY_THRESHOLD` (default 0.7), `CALLBOT_RAG_MAX_RETRIES` (default 2)
- **ESL**: `CALLBOT_ESL_HOST`, `CALLBOT_ESL_PORT` (default 8021), `CALLBOT_ESL_PASSWORD`, `CALLBOT_HANDOFF_EXT` (default 1001)
- **Outbound 外呼执行器**: `CALLBOT_OUTBOUND_ENDPOINT_TEMPLATE` (default `user/{phone}@{domain}` — 本地注册分机直连；外呼真实号码需改 `sofia/gateway/<gw>/{phone}`), `CALLBOT_OUTBOUND_DOMAIN` (软电话注册域，= FS `local_ip_v4`；留空则启动时 `_detect_local_ip()` 自动探测本机主网卡 IP——agent-flow 与 FS 同机即注册域零配置可用，端点模板含 `{domain}` 时必填，切 gateway 模板 `sofia/gateway/<gw>/{phone}` 后此项失效), `CALLBOT_OUTBOUND_CODEC_STRING` (default `PCMA`), `CALLBOT_OUTBOUND_CALLER_ID` (主叫号，分机验证阶段可空), `CALLBOT_OUTBOUND_SCHEDULER_TICK_SEC` (default 10), `CALLBOT_OUTBOUND_GLOBAL_CONCURRENCY` (default 0 = 不限，仅 per-task `concurrent_limit` 生效)
- **RMS gate(barge-in 检测,agent-flow 本地)**: `CALLBOT_RMS_GATE_THRESHOLD` (default 300.0, 帧能量低于此视为静音), `CALLBOT_RMS_GATE_SNR_FACTOR` (default 3.0, 自适应门限 = noise_floor × snr_factor), `CALLBOT_RMS_GATE_NOISE_FLOOR_INIT` (default 300.0), `CALLBOT_RMS_GATE_NOISE_ADAPT_RATE` (default 0.1, EMA 底噪更新率)
- **VAD 端点检测**: 由 agent-asr FSMN-VAD 服务端分段 → 主动推 `result/is_final` → agent-flow `on_final` 回调触发轮次(无本地 VAD 引擎)
- **Barge-in cooldown**: `CALLBOT_COOLDOWN_AFTER_BARGEIN` (default 0.5s, barge-in 后丢弃残余音频防误触发)
- **Barge-in**: `CALLBOT_BARGE_IN_MIN_AUDIO_BYTES` (default 1600, 触发 barge-in 的最小累积音频量)
- **Media**: `CALLBOT_MEDIA_SAMPLE_RATE` (default 16000), 全链路 16kHz，帧大小 960B (30ms @ 16kHz 16-bit)，TTS 输出 22050Hz 经 `_resample_pcm()` 降采样到 16kHz，FreeSWITCH 内部下采样到 G.711 8kHz
- **Jitter Buffer**: `CALLBOT_JITTER_TARGET_DEPTH` (default 3), `CALLBOT_JITTER_MAX_DEPTH` (default 10)
- **Denoise**: `CALLBOT_DENOISE_ENABLED` (`""` disabled, `"highpass"`, `"noisereduce"`, `"rnnoise"`), `CALLBOT_DENOISE_HIGHPASS_CUTOFF` (default 200.0 Hz) — 互斥于 AEC：开启 WebRTCAPM 时不再走 denoiser
- **WebRTC APM (AEC + NS + AGC)**: `CALLBOT_AEC_ENABLED` (default false, 替换 denoise + 固定增益), `CALLBOT_AEC_TYPE` (default 2, 1=AECM 移动端 / 2=老AEC), `CALLBOT_AEC_NS_LEVEL` (default 2, 0-3), `CALLBOT_AEC_AGC_TYPE` (default 1, 0=关/1=AdaptiveDigital/2=AdaptiveAnalog), `CALLBOT_AEC_SYSTEM_DELAY_MS` (default 80, 回声延迟先验)
- **Audio gain**: `CALLBOT_AUDIO_GAIN` (default 1.0, pre-ASR amplification for quiet SIP audio; AEC 开启时 AGC 由 WebRTCAPM 逐帧处理，不再叠加固定增益)
- **ASR WebSocket**: `CALLBOT_ASR_WS_URL` (default `ws://127.0.0.1:8080/ws/asr/streaming-recognize`, 唯一传输)
- **TTS WebSocket**: `CALLBOT_TTS_WS_URL` (default `ws://127.0.0.1:8081/ws/tts/streaming-synthesize`, 唯一传输)
- **Streaming ASR**: `CALLBOT_ASR_STREAMING_ENABLED` (default false, engine-level streaming)
- **Streaming TTS**: `CALLBOT_TTS_STREAMING_ENABLED` (default false, chunk-level streaming)
- **TTS pre-buffer**: `CALLBOT_TTS_PREBUFFER_FRAMES` (default 0, accumulate N 30ms frames before playback)
- **TTS skip**: `CALLBOT_TTS_SKIP` (default false, local testing without GPU)
- **Sentence splitter**: `CALLBOT_SPLITTER_MIN_LENGTH` (default 2), `CALLBOT_SPLITTER_FLUSH_TIMEOUT` (default 0.2), `CALLBOT_SPLITTER_EAGER_FIRST` (default true)
- **CosyVoice device**: `COSYVOICE_DEVICE` (engine-level, `cpu`/`mps`/`auto`, local.sh defaults to `cpu` on Mac to avoid MPS fallback overhead)
- **uvloop**: enabled via Dockerfile CMD `--loop uvloop`, no config needed
- **MCP Server**: `application.yaml` with `spring.ai.mcp.server.*` properties, STATELESS protocol, WebMVC transport, `annotation-scanner.enabled: true`, port 9090
- **Call recording**: `CALLBOT_RECORDINGS_DIR` (FS writes `${uuid}.wav` here), `CALLBOT_RECORDING_NOTICE_ENABLED` (default true, 播放录音提示音并置 `recording_notice_played`), `CALLBOT_RECORDING_NOTICE_SOUND` (default `ivr/recording_notice.wav`), `CALLBOT_RECORDING_ARCHIVE_DELAY_SEC` (default 3, 挂断后读 FS wav 的延迟), `CALLBOT_RECORDING_ARCHIVE_TIMEOUT` (default 30)
- **Console** (`console/server`): `DATABASE_URL` / `pg` pool, `REDIS_URL` (与 agent-flow 共享), `MINIO_*` (presigned 录音下载), `CONSOLE_ADFS_ENABLED` (ADFS OAuth 预留), Better Auth 本地账密;详见 `console/server/.env.example`

### Key Orchestrator Modules

| Module | Role |
|--------|------|
| `main.py` | FastAPI app with lifespan init, ESL lifecycle, `WS /media/{uuid}` (event-driven audio fork), `GET /healthz`, `POST /calls/{uuid}/archive-recording` (手动录音归档兜底) |
| `src/config.py` | pydantic-settings, all config via `CALLBOT_` env prefix |
| `src/database.py` | SQLAlchemy 2.0 async engine + session factory |
| `src/graph/flow.py` | LangGraph 7-node StateGraph pipeline + `run_pre_llm_phase` / `run_streaming_pipeline` for streaming mode |
| `src/graph/prompt.py` | System prompt + RAG + memory + chat history assembly |
| `src/graph/prompt_config.py` | Prompt loading — Redis cache (5min TTL) → DB `prompt_config` table two-level fallback |
| `src/clients/mcp.py` | MCP client → java-mcp-server (identity/credit query via langchain-mcp-adapters) |
| `src/clients/esl.py` | Async ESL client → FreeSWITCH Event Socket (auto-reconnect, heartbeat, hangup, transfer, break_media, event subscription) |
| `src/clients/asr_ws_client.py` | ASR WebSocket client — streaming audio recognition (唯一传输) |
| `src/clients/tts_ws_client.py` | TTS WebSocket client — streaming text-to-speech (唯一传输) |
| `src/ws/handler.py` | WebSocket handler: `StreamingCallHandler` (streaming + barge-in, event-driven audio processing, wires WebRTCAPM) |
| `src/ws/rms_gate.py` | `RMSGate` — RMS+SNR 自适应门禁(barge-in 检测) |
| `src/ws/denoise.py` | Configurable pre-VAD denoiser (highpass/noisereduce/rnnoise), factory via `CALLBOT_DENOISE_ENABLED` |
| `src/ws/audio_processing.py` | `WebRTCAPM` — livekit AudioProcessingModule 帧级封装 (AEC + NS + AGC + HPF), replaces denoise + fixed gain when `CALLBOT_AEC_ENABLED=true`; `create_audio_processing()` factory |
| `src/ws/jitter_buffer.py` | `JitterBuffer` (input smoothing, 960B frames @ 16kHz) + `TTSOutputBuffer` (steady 30ms frame delivery) |
| `src/ws/registry.py` | `ActiveCallRegistry` — per-call `asyncio.Event` for CHANNEL_HANGUP cancellation; carries `(tenant_id, biz_type, scenario)` + `call_target_vars`（外呼每号码 render 变量，呼入恒 {}） |
| `src/llm/service.py` | LangChain ChatOpenAI with structured output + streaming + embeddings |
| `src/llm/json_stream.py` | `IncrementalJSONParser` — extracts structured fields from LLM token stream |
| `src/llm/sentence_splitter.py` | `SentenceSplitter` — splits streaming tokens into TTS-ready sentences |
| `src/memory/assembler.py` | Aggregates Redis hot facts + PG long-term facts |
| `src/memory/chat_history.py` | langchain-redis `RedisChatMessageHistory` conversation memory |
| `src/memory/redis_memory.py` | Per-user hot fact storage (Redis hash) |
| `src/memory/store.py` | PG fact + vector data access |
| `src/rag/retriever.py` | Agentic RAG: adaptive retrieval + document grading + query rewriting |
| `src/graph/render.py` | `render(template, vars_context)` — prompt template variable substitution (MCP + memory + call_task vars); `parse_call_target_vars(raw)` — 解析 `call_target.vars`（`key:value|key:value` 字符串）为 dict（首个 `:` 拆分，容错空/坏对） |
| `src/db/models.py` | SQLAlchemy 2.0 ORM models (callbot schema, 13 tables: call_session, call_turn, call_event, call_artifact, config_snapshot, user_memory_fact, user_memory_vector, script_library, prompt_config, prompt_version, inbound_route, call_task, call_target) |
| `src/storage/repository.py` | Async repository for sessions/turns/events/artifacts |
| `src/storage/minio_storage.py` | MinIO object storage client — audio + recording upload (`upload_recording`) / download / presigned URL by tenant_id + biz_type |
| `src/storage/persistence_helpers.py` | `fire_insert_turn` / `fire_insert_event` — fire-and-forget PG double-write (turns + events) alongside Redis save_turn |
| `src/outbound/executor.py` | `OutboundExecutor` — 外呼调度单例 (lifespan 启停)：tick 轮询 `running` 任务 → `allowed_hours` 时段校验 → 并发槽位 → CAS 认领 `call_target` (pending→dialing) → `bgapi originate` (fire-and-forget) |
| `src/outbound/originate.py` | `build_originate_command()` — 构造 originate 串，注入 ai_outbound 三元组 channel vars，endpoint `user/{phone}@{domain}` (B-leg `&playback(silence_stream://-1)`)，摘机触发 CHANNEL_ANSWER 复用 inbound 管线 |
| `src/outbound/{schedule,redial}.py` | `is_within_allowed_hours()` 时段判定 / `decide_redial()` 按 Hangup-Cause + `redial_strategy` 判重拨或终态 (done/failed) |

### Project Structure

```
aiphone/
├── agent-asr/           # ASR service (FastAPI + WebSocket, built-in GPU inference)
│   ├── asradapter/      # main.py, base.py, config.py, requirements.txt
│   │   ├── engines/     # sensevoice/ (GPU), streaming/ (WebSocket), vibevoice/ (remote HTTP)
│   │   ├── vad_segmenter.py  # FSMN-VAD 流式分段层
│   │   ├── ws_server.py    # WebSocket ASR service (FSMN-VAD 分段 + 多 final)
│   ├── models/          # SenseVoiceSmall/ (local model weights)
│   ├── deploy/          # systemd units (sensevoice-asr.service, vibevoice-asr.service)
│   ├── Dockerfile       # PyTorch GPU image, model download
│   ├── README.md        # Component docs
│   └── tests/           # (empty, pending)
├── agent-tts/           # TTS service (FastAPI + WebSocket, built-in GPU inference)
│   ├── CosyVoice/       # CosyVoice source code (inferred runtime)
│   ├── ttsadapter/      # main.py, base.py, config.py, requirements.txt
│   │   ├── engines/     # cosyvoice/ (CosyVoice3 GPU), edgetts/ (Edge online, no GPU), vibevoice/ (remote HTTP)
│   │   ├── ws_server.py    # WebSocket TTS service (streaming synthesis)
│   ├── models/          # CosyVoice3-0.5B/ (local model weights)
│   ├── deploy/          # systemd units (cosyvoice-tts.service, vibevoice-tts.service)
│   ├── Dockerfile       # PyTorch GPU image, model download
│   ├── README.md        # Component docs
│   └── tests/           # (empty, pending)
├── agent-flow/  # LangGraph 7-node pipeline (FastAPI HTTP + WebSocket)
│   ├── main.py          # FastAPI entry point (HTTP + WebSocket + ESL lifecycle)
│   ├── src/             # 核心源码 (PYTHONPATH includes src/)
│   │   ├── config.py    # pydantic-settings (ESL/VAD/jitter/barge-in/AEC/recording configs)
│   │   ├── database.py  # SQLAlchemy async engine
│   │   ├── clients/     # mcp.py, esl.py, asr_ws_client.py, tts_ws_client.py
│   │   ├── ws/          # handler.py (StreamingCallHandler, TurnController+on_final+barge-in+APM), rms_gate.py (RMS 门禁),
│   │   │                # audio_processing.py (WebRTCAPM AEC),
│   │   │                # jitter_buffer.py, registry.py (ActiveCallRegistry), denoise.py
│   │   ├── graph/       # flow.py, prompt.py, prompt_config.py (Redis→DB prompt loading), render.py (变量渲染)
│   │   ├── llm/         # service.py, json_stream.py, sentence_splitter.py
│   │   ├── memory/      # assembler.py, chat_history.py, redis_memory.py, store.py
│   │   ├── rag/         # retriever.py (Agentic RAG)
│   │   ├── db/          # models.py (ORM, 13 tables)
│   │   └── storage/     # repository.py, minio_storage.py (upload_recording), persistence_helpers.py (fire-and-forget)
│   ├── llm/             # Qwen LLM 推理引擎 Dockerfile (vLLM)
│   ├── alembic/         # DB migrations (0001_init_full_schema — 合并旧 0001-0005 全量初始化)
│   ├── alembic.ini      # Alembic config
│   ├── requirements.txt # Python dependencies
│   ├── Dockerfile       # Application image (auto alembic upgrade head)
│   ├── README.md        # Component docs
│   └── tests/           # (empty, pending)
├── agent-mcp/                # MCP servers (user center backend)
│   └── java-mcp-server/ # Spring Boot 4.0 + Spring AI 2.0.0 (GA) stateless MCP server
│       ├── src/main/java/com/trans/mcp/
│       │   ├── McpApplication.java     # Entry point (annotation-scanner auto-registers tools)
│       │   ├── model/                  # IdentityResult, CreditResult records
│       │   └── service/                # UserService, CreditService (@McpTool + @McpToolParam)
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
│   ├── dialplan/public/       # 00_biz_type.xml — catch-all 呼入 (answer → playback silence_stream://-1); DID/三元组由 agent-flow 查 inbound_route 解析
│   └── mrcp-plugin/          # UniMRCP 1.5.0 (MRCP/ASR fallback)
├── console/             # Next.js 15 管理控制台 (Drizzle + Better Auth, :3001)
│   └── server/
│       ├── src/app/      # App Router pages (calls, call-tasks, inbound-routes, prompts, tenants, login) + route handlers (/api/prompts, /api/calls, /api/inbound-routes, /api/call-tasks, /api/tenants, /api/session)
│       ├── src/lib/      # services (prompts/calls/routes/call-tasks) + minio/redis/llm/perms/auth-client
│       ├── src/db/       # schema.ts (Drizzle 只读映射 callbot.*), migrations (0001_init_console.sql), seed
│       ├── src/components/ # ConsoleShell, CallRecordsList, CallDetail, PromptManager, InboundRoutesManager, CallTasksManager, TenantsManager, TenantSwitcher
│       ├── auth.ts / src/auth/session.ts  # Better Auth (本地账密 + active_tenant_id)
│       ├── ecosystem.config.cjs  # PM2 部署
│       └── README.md    # Console docs
├── scripts/             # Startup scripts
│   ├── local.sh         # Local dev (conda): asr/tts/flow, stop, status
│   └── prod.sh          # Production deploy (Docker Compose): GPU check, ordered startup
├── voices/              # TTS voice samples
│   ├── default_female.wav
│   └── tts_test.wav
├── openspec/            # Stable specs (8 capabilities) + changes/archive/
├── docs/                # superpowers plans/specs (design records)
├── docker-compose.yml       # Base Docker Compose (infra + services, MCP in prod override only)
├── docker-compose.prod.yml  # Production overrides (GPU pinning, health checks, MCP server)
└── .env                     # Environment variables (CALLBOT_ prefix)
```

### Infrastructure

- **PostgreSQL 17** with pgvector extension, schema `callbot`, 13 tables (call_session, call_turn, call_event, call_artifact, config_snapshot, user_memory_fact, user_memory_vector, script_library, prompt_config, prompt_version, inbound_route, call_task, call_target); Console adds `console` schema (Better Auth: user/session/account/verification + tenant/user_tenant)
- **Redis** for hot memory, conversation history (langchain-redis), session state, prompt cache (5min TTL)
- **MinIO** for audio archiving (optional, agent-flow disabled when `CALLBOT_MINIO_ENDPOINT` empty)
- **FreeSWITCH 1.11.0** compiled from source with mod_audio_fork + mod_event_socket (ESL)
- **Java MCP Server** Spring Boot 4.0 + Spring AI 2.0.0 (GA), Java 21, Maven build, `@McpTool` annotation-driven tool registration
- **GPU allocation**: ASR=GPU0 (agent-asr内置), TTS=GPU1 (agent-tts内置), LLM(Qwen3.5:4B-instruct)=GPU2(:8083)
- **uvloop**: libuv C-based event loop replacing std asyncio in agent-flow (via `--loop uvloop`), reduces GC pauses under high concurrency
- **WebSocket**: ASR/TTS 唯一传输 (`ws_server.py` in agent-asr/agent-tts, `asr_ws_client.py`/`tts_ws_client.py` in agent-flow)；ASR 经 FSMN-VAD 分段 + 多 final 驱动 agent-flow 轮次。
- **ESL**: Auto-reconnect with heartbeat detection (read error triggers reconnect), subscribes to CHANNEL_ANSWER + CHANNEL_HANGUP; dynamic `uuid_audio_fork` start/stop per call lifecycle; `break_media` uses fire-and-forget (bypasses lock contention)
- **Docker Compose**: `docker-compose.yml` (base) + `docker-compose.prod.yml` (production overrides with MCP server), GPU pinning, health checks, ordered startup
