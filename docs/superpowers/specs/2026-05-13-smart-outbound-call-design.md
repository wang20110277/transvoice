# 智能外呼系统 — 实现设计文档

> 日期: 2026-05-13
> 策略: 分层自底向上（Strategy A）
> 状态: 已确认

---

## 1. 概述

智能外呼系统采用分层架构，从底层通信到上层智能逐层构建，共 8 个 Phase。每个 Phase 有明确的交付物和验收标准，完成后独立验证再进入下一阶段。

### 系统架构

```
Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 4 ──→ Phase 5
  infra        adapter      orch core    LLM          langgraph
                                                 ↓
                                            Phase 6 ──→ Phase 7 ──→ Phase 8
                                              memory      identity     monitoring
```

### 核心链路

```
SIP User → FreeSWITCH → UniMRCP → [ASR/TTS 适配服务] → VibeVoice
                 ↓ (ESL)
          Orchestrator (Python, LangGraph)
                 ↓
           Qwen3.5-9B (LLM) + Redis + PG17 + mem0 + MCP/ESB
```

---

## 2. 代码仓库结构

```
aiphone/
├── freeswitch/               # Phase 1 - FreeSWITCH 配置文件（已有）
│   ├── modules.conf
│   ├── vars.xml
│   ├── event_socket.conf.xml
│   ├── unimrcp.conf.xml
│   ├── dialplan/
│   │   └── public.xml
│   └── unimrcp/
│       └── unimrcpserver.xml
├── mrcp-asr/                 # Phase 2 - ASR 适配层（待实现）
│   ├── src/
│   │   ├── asr_engine.c      # UniMRCP ASR 引擎插件（C）
│   │   └── asr_engine.h
│   ├── adapter/
│   │   ├── main.py           # FastAPI 入口
│   │   ├── asr_service.py    # VibeVoice ASR 封装
│   │   ├── config.py
│   │   └── requirements.txt
│   ├── deploy/
│   │   ├── vibevoice-asr.service
│   │   └── install.sh
│   └── README.md
├── mrcp-tts/                 # Phase 2 - TTS 适配层（待实现）
│   ├── src/
│   │   ├── tts_engine.c      # UniMRCP TTS 引擎插件（C）
│   │   └── tts_engine.h
│   ├── adapter/
│   │   ├── main.py           # FastAPI 入口
│   │   ├── tts_service.py    # VibeVoice TTS 封装
│   │   ├── config.py
│   │   └── requirements.txt
│   ├── deploy/
│   │   ├── vibevoice-tts.service
│   │   └── install.sh
│   └── README.md
├── agent-orchestrator/       # Phase 3-7 - Python 编排器（待实现）
│   ├── main.py               # 入口
│   ├── fs_esl.py             # ESL 连接管理
│   ├── fs_actions.py         # FS 动作封装
│   ├── call_state.py         # 通话状态管理
│   ├── event_handlers.py     # 事件分发与处理
│   ├── graph_flow.py         # LangGraph 状态图
│   ├── llm_qwen.py           # Qwen LLM 调用
│   ├── mcp_client.py         # MCP Client（身份核验）
│   ├── config.py             # 统一配置
│   ├── prompts/              # Prompt 模板
│   │   ├── customer_service.yaml
│   │   ├── collection.yaml
│   │   └── marketing.yaml
│   ├── memory/               # 记忆系统
│   │   ├── redis_memory.py
│   │   ├── pg_facts.py
│   │   ├── pg_vector.py
│   │   ├── mem0_adapter.py
│   │   └── assembler.py
│   ├── storage/              # 存储封装
│   │   ├── db_pg.py
│   │   ├── storage_artifacts.py
│   │   └── minio_client.py
│   ├── compliance.py         # 合规门禁
│   └── requirements.txt
├── deploy/                   # Phase 1,8 - 部署脚本
│   ├── install_all.sh
│   ├── install_fs.sh
│   ├── install_unimrcp.sh
│   ├── init_db.sql
│   ├── install_deps.sh
│   └── systemd/
│       ├── freeswitch.service
│       ├── unimrcp.service
│       ├── vibevoice-asr.service
│       ├── vibevoice-tts.service
│       ├── qwen-llm.service
│       ├── orchestrator.service
│       └── mcp-bridge.service
├── monitoring/               # Phase 8
│   ├── prometheus/
│   │   └── prometheus.yml
│   └── grafana/
│       └── dashboards/
└── doc/                      # 文档（已有）
```

---

## 3. Phase 1: 基础设施部署

### 目标

一键部署 FreeSWITCH、UniMRCP、PG17、Redis、MinIO，执行 DDL。

### 交付物

| 文件 | 职责 |
|------|------|
| `deploy/install_fs.sh` | FreeSWITCH 编译安装 + 配置部署 + 模块加载验证 |
| `deploy/install_unimrcp.sh` | UniMRCP 编译安装 + 配置 + systemd 守护 |
| `deploy/init_db.sql` | PG17 DDL（schema callbot + 8 张表 + 分区 + HNSW 索引） |
| `deploy/install_deps.sh` | Redis、MinIO 安装与初始化（bucket 创建、lifecycle 配置） |

### 验收标准

- `fs_cli -x "show modules"` 显示 mod_sofia、mod_unimrcp、mod_event_socket、mod_dptools
- `systemctl status unimrcp` 运行中，端口 8060 监听
- PG `callbot` schema 下 8 张表存在
- MinIO 三个 bucket（rec-cs、rec-collection、rec-marketing）已创建

---

## 4. Phase 2: ASR/TTS 适配层

### 目标

实现 UniMRCP → VibeVoice 的 Python 代理适配服务。

### 架构链路

```
FreeSWITCH (mod_unimrcp) → UniMRCP Server → [Python 适配服务] → VibeVoice 模型
```

UniMRCP Server 通过 C 引擎插件（薄 C 层）将 MRCPv2 请求转发为 HTTP 调用，Python 适配服务就是 `unimrcpserver.xml` 中 `backend-url` 指向的目标。

### ASR 适配服务（mrcp-asr/）

FastAPI 服务，端口 8080，GPU0。

**端点：**
- `POST /asr/recognize` — 接收音频流，调 VibeVoice ASR，返回识别文本
- `GET /healthz` — 健康检查
- `GET /metrics` — Prometheus 指标

**关键设计：**
- 启动时加载 VibeVoice-ASR 模型到 GPU0（CUDA_VISIBLE_DEVICES=0）
- 支持流式音频输入（chunked transfer）
- 返回 JSON：`{"text": "...", "confidence": 0.95, "is_final": true}`
- VAD 参数可配置
- 并发控制：信号量限制最大同时识别数（默认 50）
- 指标：`asr_requests_total`、`asr_latency_p95`、`asr_errors_total`、`asr_concurrent_sessions`

### TTS 适配服务（mrcp-tts/）

FastAPI 服务，端口 8080，GPU1。

**端点：**
- `POST /tts/synthesize` — 接收文本 + voice_id/speed/volume/pitch，返回音频
- `GET /healthz` — 健康检查
- `GET /metrics` — Prometheus 指标

**关键设计：**
- 启动时加载 VibeVoice-TTS 模型到 GPU1（CUDA_VISIBLE_DEVICES=1）
- 三业务 profile 强隔离：
  - 请求参数含 `biz_type`，映射到对应 voice_id/speed/volume/pitch
  - 缓存目录按 biz_type 分离
- TTS 缓存：`text_hash → 音频文件`，避免重复合成
- 返回 audio/wav 二进制流
- 并发控制：营销 profile 允许更高并发（50），其他 30
- 指标：`tts_requests_total`、`tts_latency_p95`、`tts_queue_length`、`tts_cache_hit_rate`

### UniMRCP C 引擎插件

薄 C 层，功能：
- 接收 MRCPv2 会话（SIP/MRCP 信令 + RTP 媒体流）
- 把音频流以 HTTP POST 发送给 Python 适配服务
- 把 Python 返回的文本/音频转回 MRCP 响应

### 验收标准

- UniMRCP 调用 VibeVoice ASR 返回文本（MRCP 链路端到端通）
- UniMRCP 调用 VibeVoice TTS 返回音频（不同 biz_type 音色不同）
- `/healthz` 返回 200，`/metrics` 可访问

---

## 5. Phase 3: Orchestrator 核心

### 目标

实现 ESL 连接管理、事件循环、通话状态管理，打通基本对话循环。

### 核心模块

| 模块 | 职责 |
|------|------|
| `main.py` | 入口，启动 ESL 连接 + 事件循环 |
| `fs_esl.py` | ESL 连接管理（订阅、断线重连、指数退避） |
| `fs_actions.py` | FS 动作封装（play/record/detect_speech/TTS/transfer） |
| `call_state.py` | 通话状态管理（内存为主 + Redis 同步） |
| `event_handlers.py` | 事件分发与处理（CHANNEL_*/DETECTED_SPEECH/PLAYBACK_*） |
| `config.py` | 统一配置（连接信息、业务参数） |

### ESL 连接模型

单连接事件循环：
- `ESLEventLoop` 类，阻塞等待事件，按 `Unique-ID` 分发到对应通话状态机
- 断线自动重连（5 秒间隔，指数退避）
- 连接状态监控 + 告警

### 通话状态管理

双层状态：
- 内存 dict：通话进行中直接读写
- Redis Hash（`cb:call:state:{fs_uuid}`）：异步持久化，TTL 24h
- 挂断时从内存移除，Redis 保留 1 小时供排查

`CallState` dataclass 字段：fs_uuid、biz_type、user_key、turn_count、silence_count、identity_verified、recording_notice_played 等。

### 事件处理流程

**CHANNEL_ANSWER：**
1. 初始化 CallState（从 channel variables 取 biz_type/user_key）
2. `play_legal_notice()` — 播放录音告知（失败 CRITICAL 告警）
3. `start_recording()` — 分轨录音（caller.wav/bot.wav）
4. `start_detect_speech()` — 启动 ASR 监听
5. `insert_call_session()` — 写 PG 会话表

**DETECTED_SPEECH：**
1. 提取识别文本（空则跳过）
2. 写 Redis 滑窗（`cb:call:window:{fs_uuid}`）
3. 调 LLM 决策（Phase 3 用规则引擎替代）
4. 执行动作（say/ask → TTS，handoff → transfer，end → hangup）
5. 恢复 detect_speech

**CHANNEL_HANGUP：**
1. 停止 detect_speech + 录音
2. 更新 PG 会话表（结束时间、挂机原因）
3. 清理 Redis 状态
4. 异步 finalize 记忆（Phase 6 实现）
5. 录音归档到 MinIO（Phase 6 实现）

### Phase 3 规则引擎（临时）

Phase 3 阶段用简单规则替代 Qwen：

```python
RULES = {
    "marketing": {"default": "您好，感谢您的接听，请问有什么可以帮助您的？"},
    "customer_service": {"default": "您好，请问有什么可以帮您？"},
    "collection": {"default": "您好，这里有一笔账单需要确认。"},
}
```

Phase 4 接入 Qwen 后替换此规则。

### 验收标准

- ESL 连接稳定，断线重连正常
- CHANNEL_ANSWER 后自动播放录音告知 + 录音 + detect_speech
- 用户说话后 DETECTED_SPEECH 正常回调
- TTS 播报使用正确的 biz_type 音色
- CHANNEL_HANGUP 后状态清理完整

---

## 6. Phase 4: LLM 集成

### 目标

接入 Qwen3.5-9B，实现结构化对话决策。

### 核心模块

**llm_qwen.py** — Qwen 调用封装：

```python
def invoke(biz_type, user_input, memory_block, turn_history) -> LLMAction:
    """调用 Qwen，返回结构化动作"""

@dataclass
class LLMAction:
    type: str           # "say" | "ask" | "handoff" | "end"
    text: str           # 播报文本
    intent: str         # 意图标签
    labels: list[str]   # 业务标签
```

### Prompt 模板（按 biz_type 隔离）

三个 YAML 文件（`prompts/{biz_type}.yaml`），各含：
- system prompt（角色、规则、语气约束）
- response schema（JSON 结构定义）
- 回复长度限制（催收 50 字、营销 80 字、客服不限）

### 上下文组装

```
[System Prompt (biz_type 专属)]
[Memory Block (Redis hot + PG facts + pgvector recall)]
[对话历史 (最近 N 轮)]
[用户最新输入]
```

长度控制：Memory Block < 500 token，对话历史最近 10 轮，总输入 < 2000 token。

### 容错机制

- JSON parse 失败 → 正则提取 → 兜底固定话术
- 超时 3 秒 → 降级固定话术 + 告警
- 连续失败 2 次 → 熔断转人工
- GPU2（CUDA_VISIBLE_DEVICES=2）独立部署

### 验收标准

- Qwen 返回结构化 JSON，parse 成功率 > 95%
- 三个 biz_type 使用不同 Prompt，话术风格差异明显
- 超时/失败降级正常，不中断通话

---

## 7. Phase 5: LangGraph 流程编排

### 目标

用 LangGraph StateGraph 实现多节点状态机，替换 Phase 3 的线性处理逻辑。

### 状态定义

```python
class CallGraphState(TypedDict):
    fs_uuid: str
    biz_type: str
    user_key: str
    user_input: str
    memory_block: str
    llm_action: LLMAction
    identity_verified: bool
    turn_count: int
    handoff_reason: str
```

### 图结构

```
recall_memory → llm_decide → [compliance_check] → execute_action → (continue or finalize)
```

节点：
| 节点 | 职责 | 输入 → 输出 |
|------|------|-------------|
| `recall_memory` | 召回 Redis hot + PG facts + pgvector | user_input → memory_block |
| `llm_decide` | 调 Qwen 获取结构化动作 | memory_block + user_input → llm_action |
| `compliance_check` | 催收敏感字段门禁 | llm_action → filtered_action |
| `execute_action` | 执行 say/ask/handoff/end | llm_action → FS action + 循环判断 |
| `finalize` | 通话结束清理 | state → 记忆写入 + 录音归档 |

条件边：
- `llm_decide` → 催收场景且未核验 → `compliance_check`，其他 → `execute_action`
- `execute_action` → action 非 end → `recall_memory`（下一轮），action=end → `finalize`

### 与 Phase 3 的集成

Phase 3 的事件循环保持不变，`DETECTED_SPEECH` handler 中替换决策逻辑：

```python
# Phase 3: 固定话术
action = RULES[biz_type]["default"]

# Phase 5: LangGraph 驱动
result = await graph.ainvoke({
    "fs_uuid": fs_uuid, "user_input": speech_text, ...
})
action = result["llm_action"]
```

### 验收标准

- LangGraph 图能完整走完 recall → llm → compliance → execute 循环
- 催收场景未核验时敏感字段被拦截
- 每轮循环总延迟 < 2 秒（不含 LLM 推理时间）

---

## 8. Phase 6: 记忆系统

### 目标

实现三层记忆架构，支持跨通话用户记忆持久化与召回。

### 模块结构

```
agent-orchestrator/memory/
├── redis_memory.py      # Redis hot memory（短期）
├── pg_facts.py          # PG facts（长期）
├── pg_vector.py         # pgvector 相似召回
├── mem0_adapter.py      # mem0 抽取/更新/衰减
└── assembler.py         # Memory Block 组装
```

### 三层记忆

**Layer 1: Redis Hot Memory（TTL 90天）**
- Key: `cb:mem:hot:{biz_type}:{user_key}:{yyyymm}`
- 内容：用户偏好、核验状态、拒绝标记
- 毫秒级读取，每轮决策前必读

**Layer 2: PG Facts（持久）**
- Table: `callbot.user_memory_fact`
- 内容：结构化事实（do_not_call、偏好、核验结果）
- 可审计、可追溯（source_call_id/source_turn_id）

**Layer 3: PG Vector（180天窗口）**
- Table: `callbot.user_memory_vector`（按月分区 + HNSW 索引）
- 内容：对话摘要、用户异议、处理片段的向量
- 检索条件：`biz_type + user_key + ts >= now() - 180d`，top K=3

### Memory Block 组装（assembler.py）

每轮调用，输出一段结构化文本（< 500 token）：

```
1. Redis hot facts → 最关键 5 条
2. PG facts → 最近 90 天 top 5（按 last_seen_ts 排序）
3. pgvector → 相似召回 top 3（cosine < 0.3 阈值）
4. 拼接为:
   ## 用户记忆
   - [偏好]: 周末上午联系（Redis）
   - [历史]: 上次咨询还款计划（PG fact, 2026-04-15）
   - [相似]: 用户提到"太忙"的对话片段（向量召回）
```

### 记忆写入时机

| 时机 | 写入内容 | 目标层 |
|------|---------|--------|
| 通话结束 finalize | 对话摘要 + 关键 facts + 向量 | PG facts + pgvector |
| 用户明确拒绝营销 | do_not_call = true | Redis hot + PG fact |
| 催收核验通过 | 核验结果（脱敏） | Redis hot + PG fact |
| 每轮对话 | turn 文本 | Redis 滑窗（自动过期） |

### mem0 适配器

- 通话结束时抽取 facts（规则优先 + LLM 辅助）
- 更新已有 facts 的 last_seen_ts
- 过期 facts 衰减标记（expire_ts）

### 验收标准

- Redis hot facts 读取延迟 < 5ms
- pgvector 召回 top 3 延迟 < 50ms
- Memory Block 组装总延迟 < 100ms
- 通话结束 finalize 写入 PG facts + pgvector 成功

---

## 9. Phase 7: 身份核验 & 合规

### 目标

通过 MCP Client + MCP Server + ESB/RPC 链路实现用户身份核验和业务合规校验。

### 架构链路

```
LangGraph 业务节点 → MCP Client → MCP Server → ESB/RPC → 用户中心 User Service
```

### 核心模块

**mcp_client.py** — MCP 客户端封装：

```python
class MCPClient:
    async def query_user_identity(self, phone_hash: str, biz_type: str) -> IdentityResult:
        """MCP Client → MCP Server → ESB → 用户中心，优先传手机号"""

    async def query_credit_profile(self, user_id: str, phone_hash: str) -> CreditResult:
        """复用同一 MCP 链路，手机号+用户ID双维度查询征信"""
```

### LangGraph 核验节点

**Node 1: identity_verify**
- MCP Client 调用户中心（传 phone_hash）
- 获取：user_id、姓名（脱敏）、身份证后四位、性别
- 可选：调取声纹数据实时比对
- 存入 CallState.identity_verified = true/false
- 声纹不一致 → 风险预警

**Node 2: credit_check（仅催收/贷款场景）**
- MCP Client 调金融核心系统（传 phone_hash + user_id）
- 获取：征信档案
- 校验征信资质 → 不合规触发风控预警

### 合规门禁规则

```python
def compliance_check(action: LLMAction, state: CallState) -> LLMAction:
    # 催收敏感字段门禁
    if state.biz_type == "collection" and not state.identity_verified:
        if contains_sensitive_fields(action.text):
            action.text = sanitize(action.text)
            alert("SENSITIVE_FIELD_BLOCKED", state.fs_uuid)

    # 营销 do_not_call 拦截
    if state.biz_type == "marketing" and get_do_not_call(state.user_key):
        action = LLMAction(type="end", text="抱歉打扰了，再见")

    # 录音告知未播放
    if not state.recording_notice_played:
        alert("LEGAL_NOTICE_NOT_PLAYED", state.fs_uuid)

    return action
```

### 验收标准

- MCP 链路调用用户中心成功返回用户信息
- 催收未核验时敏感字段被拦截并告警
- 身份核验超时降级正常（标记未核验，继续通话）
- 三大风控预警点均可正常触发

---

## 10. Phase 8: 监控运维

### systemd 服务

7 个守护服务，全部 `Restart=always`：

| 服务 | GPU | 关键配置 |
|------|-----|---------|
| `freeswitch.service` | — | LimitNOFILE=1000000 |
| `unimrcp.service` | — | LimitNOFILE=1000000 |
| `vibevoice-asr.service` | GPU0 | CUDA_VISIBLE_DEVICES=0 |
| `vibevoice-tts.service` | GPU1 | CUDA_VISIBLE_DEVICES=1 |
| `qwen-llm.service` | GPU2 | CUDA_VISIBLE_DEVICES=2 |
| `orchestrator.service` | — | Python 3.12 venv |
| `mcp-bridge.service` | — | MCP Server |

### Prometheus 指标

| 组件 | 关键指标 |
|------|---------|
| Orchestrator | `call_active`, `call_total`, `turn_total`, `llm_latency_p95`, `llm_errors`, `tts_cache_hit_rate` |
| ASR 适配 | `asr_requests_total`, `asr_latency_p95`, `asr_errors_total`, `asr_concurrent_sessions` |
| TTS 适配 | `tts_requests_total`, `tts_latency_p95`, `tts_queue_length`, `tts_cache_hit_rate` |
| UniMRCP | `mrcp_sessions_active`, `mrcp_resource_latency` |

### Grafana 面板

- **通话总览**: 活跃通话数（按 biz_type）、通话量趋势、平均通话时长、转人工率
- **语音链路**: ASR/TTS 延迟 P95、TTS 排队长度、错误率
- **LLM 决策**: Qwen 延迟 P95、parse 成功率、动作分布
- **合规告警**: 录音告知未播放次数、敏感字段拦截次数、身份核验失败次数

### 告警规则

| 规则 | 级别 | 条件 |
|------|------|------|
| 通话失败激增 | CRITICAL | 5min 内失败率 > 30% |
| ASR 连续失败 | ERROR | 1min 内连续失败 > 5 次 |
| TTS 排队过长 | WARNING | 排队长度 > 20 持续 1min |
| 录音告知未播放 | CRITICAL | 任何 1 次 |
| LLM 响应超时 | ERROR | P95 > 5s 持续 2min |
| 服务宕机 | CRITICAL | 任何服务 down > 30s |

---

## 11. 各 Phase 交付物汇总

| Phase | 交付物 | 验收标准 |
|-------|--------|----------|
| 1 | deploy/ 安装脚本 + DDL | FS/UniMRCP/PG/Redis/MinIO 全部就绪 |
| 2 | mrcp-asr/ + mrcp-tts/ 适配层 | MRCPv2 → VibeVoice ASR/TTS 端到端通 |
| 3 | agent-orchestrator/ 核心代码 | ESL 事件循环 + detect_speech 对话循环正常 |
| 4 | llm_qwen.py + prompts/ | Qwen 结构化输出，三个 biz_type 风格差异明显 |
| 5 | graph_flow.py | LangGraph 多节点流转，合规门禁正常 |
| 6 | memory/ 模块 | 三层记忆读写 + Memory Block 组装 < 100ms |
| 7 | mcp_client.py + compliance.py | MCP 身份核验 + 合规拦截正常 |
| 8 | systemd/ + monitoring/ | 服务自启 + 指标可观测 + 告警可触发 |

---

## 12. 技术栈

| 层次 | 技术 |
|------|------|
| 通信 | SIP/RTP (FreeSWITCH + mod_sofia) |
| 语音协议 | MRCPv2 (UniMRCP) |
| ASR/TTS | VibeVoice-ASR, VibeVoice-Realtime-0.5B |
| LLM | Qwen3.5-9B |
| 编排 | Python 3.12 + LangGraph + LangChain |
| 消息 | MCP + ESB/RPC |
| 短期存储 | Redis |
| 长期存储 | PostgreSQL 17 + pgvector |
| 记忆 | mem0 |
| 对象存储 | MinIO + NAS |
| 监控 | Prometheus + Grafana |
| 守护 | systemd |
