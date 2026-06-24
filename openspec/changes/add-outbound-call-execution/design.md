# Design: 外呼任务执行引擎

> 本文档记录 brainstorming 阶段确认的架构方向。细节参数（重拨间隔、tick 粒度、状态码映射表等）在 spec 阶段细化。

## 1. 核心复用洞察

inbound 的 `_on_channel_answer`（`agent-flow/main.py:266-338`）几乎可原样复用。它从 ESL 事件读 channel vars（`variable_biz_type` / `variable_user_key` / `variable_did`），唯一外部依赖是 `_resolve_inbound_dimensions(did)` 把 DID → 三元组。downstream 的 `register` / `insert_call_session` / `audio_fork_start` / `record_start` **不区分呼入呼出**——只认 uuid + 三元组。

**复用手段**：originate 时把三元组通过 channel vars 注入，给 answer 处理器加一个 outbound 分支——检测到外呼标记就从 vars 读维度、跳过 DID 解析。对话/录音/barge-in/归档这条高风险大头全部已验证，无需重写。

## 2. 架构决策

### 2.1 执行器位置：方案 A（agent-flow 进程内 asyncio）

lifespan 启动的后台执行器，跑在现有 uvloop，复用 ESL 连接 / `ActiveCallRegistry` / repository。

**理由**：
- 当前阶段（单租户 / 内部分机验证）规模，in-process asyncio 最贴合现有事件驱动架构
- `bgapi` 是 fire-and-forget，originator 不阻塞主通话 loop
- 符合「不提前设计」——真上量（多租户高并发）再演进到独立进程 / 外部队列，届时 PG 中的号码/任务状态已是天然的跨进程协调介质

### 2.2 originate 命令形态（M1，拨本地分机 1000）

```
originate {ai_outbound=true,call_task_id=5,call_target_id=12,
           tenant_id=t1,biz_type=marketing,scenario=default,
           user_key=13800138000,
           ignore_early_media=true,
           absolute_codec_string=PCMA,
           origination_caller_id_number=<主叫号>}
          user/1000@192.168.0.192 &park()
```

实测验证的两个硬性要求（已在 originate 构造器 + config 固化，见 M1 验证日志）：

- **端点必须用 `user/{phone}@{domain}` 直连**：`sofia/internal/{phone}` 会让被叫号码重新进 dialplan（public context），触发 unloop/deflect 循环最终 480 失败；不带域名的 `user/1000` 会 USER_NOT_REGISTERED（注册域 ≠ profile 名）。domain 取软电话注册域（FS `local_ip_v4`）。
- **必须 `absolute_codec_string=PCMA` 强制线性编解码**：profile 默认协商 G.722 时，mod_audio_fork 抓到的帧格式不对，ASR 完全收不到有效音频（连 `ASR stream created` 都不出现）。强制 PCMA 后 ASR 正常识别。

- `&playback(silence_stream://-1)` 作为 B-leg app：**必须镜像 inbound 的 silence_stream**。实测 `&park()` 的 channel 不产生持续 write 帧，mod_audio_fork 下行 PCM 无载体播不到 RTP（你听不到 AI 说话）。silence_stream 维持持续 write media path，AI 下行音频才能搭便车播到软电话 + TTSOutputBuffer 静音帧保活。
- 端点模板抽象为配置项 `CALLBOT_OUTBOUND_ENDPOINT_TEMPLATE`（默认 `user/{phone}@{domain}`），后期换 SIP 网关改 `sofia/gateway/<gw>/{phone}` 只改模板，代码不动
- 端点变量：`{phone}`（被叫）、`{domain}`（注册域）；其余三元组 / 任务 ID 进 channel vars

### 2.3 answer 处理器 outbound 分支

`_on_channel_answer` 开头加分支：
```
if event.headers.get("variable_ai_outbound"):
    # 外呼路径：三元组 + task/target ID 全从 channel vars 读，跳过 DID 解析
    tenant_id  = var("tenant_id"); biz_type = var("biz_type")
    scenario   = var("scenario"); user_key  = var("user_key")
    call_task_id = var("call_task_id"); call_target_id = var("call_target_id")
else:
    # 原有 inbound 路径（DID → _resolve_inbound_dimensions），不改
```
其后 register / insert_call_session（**携带 call_task_id / call_target_id**）/ audio_fork / record 全复用。

## 3. 数据模型变更（alembic 新迁移）

### 3.1 新增表 `call_target`（号码清单）

| 列 | 类型 | 说明 |
|----|------|------|
| id | bigserial PK | |
| task_id | bigint FK→call_task.id | 所属任务 |
| tenant_id | text | 隔离 |
| phone_hash | text | 号码哈希（任务内 unique，去重） |
| phone_masked | text | 脱敏展示 |
| user_key | text | 明文 key（渲染/查询） |
| status | text | `pending` / `dialing` / `answered` / `no_answer` / `failed` / `done` |
| attempt_count | int default 0 | 已拨次数 |
| max_attempts | int | 上限（继承 redial_strategy.max_retries+1） |
| next_attempt_ts | timestamptz nullable | 下次可拨时间（重拨退避） |
| last_call_session_id | bigint nullable FK→call_session.id | 最近一次通话 |
| last_hangup_cause | text nullable | 最近挂断原因 |
| create_time / update_time | timestamptz | 审计 |

约束：`unique(task_id, phone_hash)`（任务内去重）。

### 3.2 `call_session` 加列（关联外呼）

新增可空列 `call_task_id` / `call_target_id`（外呼通话才有值；inbound 为 NULL，向后兼容）。inbound 路径 insert 时这两个键就是 None，无需改 inbound 代码逻辑（dict 里不传即 NULL）。

### 3.3 `call_task.status` 状态机（执行器消费，不改表）

```
idle ──start──▶ running ──pause──▶ paused ──resume──▶ running
                   │                    │
                   ├─号码清空/全终态──▶ completed
                   └─致命错误────────▶ failed
```
`call_task` 已有 `status` 字段，无需改表，执行器读写它即可。

## 4. 执行器设计（进程内 asyncio）

### 4.1 组件

- **`OutboundExecutor`**（lifespan 启动单例）：持有每任务的 `(semaphore, state)`；一个周期 tick 协程
- **tick（~10s）**：扫描 `status=running` 任务 → 检查 `allowed_hours` 是否在窗口 → 拉取 `status=pending AND next_attempt_ts<=now` 的号码 → 受 semaphore 限流地并发 originate
- **并发**：`asyncio.Semaphore(task.concurrent_limit)` per task（可选全局上限）
- **originate**：`esl.bgapi(originate_cmd)`，fire-and-forget；号码行先置 `dialing`

### 4.2 结果回写 + 重拨判定（CHANNEL_HANGUP 分支）

`_on_channel_hangup` outbound 分支：查 `call_target`（attempt_count/max_attempts）+ `call_task.redial_strategy` → `decide_redial(hangup_cause, attempt_count, max_attempts, retry_on_causes)` 纯函数判定：

- `NORMAL_CLEARING` → 终态 `done`（接通成功，不重拨）
- 失败原因 ∈ `retry_on_causes` 且 `attempt_count < max_attempts` → 重拨：`reset_call_target_for_redial`（pending + attempt_count+1 + next_attempt_ts 退避 interval_min）
- 否则 → 终态 `failed`

`decide_redial` 是纯函数（`outbound/redial.py`，6 单测覆盖：接通终态/可重拨/不可重拨原因/达上限/空策略/未知原因）。

> **已知限制（originate 失败路径）**：channel 建立失败（号码不存在/未注册，如 `SUBSCRIBER_ABSENT`/`USER_NOT_REGISTERED`）的 originate **不产生 CHANNEL_HANGUP 事件**（channel 从未建立），hangup 分支不触发，target 卡在 dialing。完整覆盖需订阅 ESL `BACKGROUND_JOB` 事件拿 originate 失败结果。本期 hangup 路径（接通后挂断的 NO_ANSWER/失败重拨，真实外呼主要场景）已完整；originate 失败路径的 BACKGROUND_JOB 订阅作为后续增强。真实号码清单应前置过滤无效号码降低此影响。

### 4.3 `redial_strategy` JSONB 结构（spec 定型）

```json
{ "max_retries": 2, "interval_min": 30, "retry_on_causes": ["NO_ANSWER","USER_NOT_REGISTERED"] }
```

### 4.4 `allowed_hours` 格式（先简单）

`"HH:MM-HH:MM"`（如 `"09:00-21:00"`），调度 tick 据此判断是否在窗口内。复杂时段（多段/按天）留后期。

## 5. Console 增量

- 号码清单管理：任务详情页内上传 CSV / 单条录入 / 列表删除（走 `calltask:*` 权限）
- 启停按钮：`PATCH /api/call-tasks/[id]` 改 `status`（`running`/`paused`）→ 执行器 tick 感知
- 进度查询：`GET /api/call-tasks/[id]/progress` → 聚合 `call_target` 状态计数（pending/dialing/answered/no_answer/failed/done）
- 实时进度：前端轮询进度 API（本期不做 WS 推送）

## 6. 配置增量（`CALLBOT_` 前缀）

- `CALLBOT_OUTBOUND_ENDPOINT_TEMPLATE`（默认 `sofia/internal/{phone}`）
- `CALLBOT_OUTBOUND_CALLER_ID`（主叫号，分机验证阶段可留空）
- `CALLBOT_OUTBOUND_SCHEDULER_TICK_SEC`（默认 10）
- `CALLBOT_OUTBOUND_GLOBAL_CONCURRENCY`（默认 0=不限）

## 7. 风险与验证

- **复用正确性**：M1 必须验证外呼通话的 audio_fork / 对话 / 录音归档与呼入完全等价（`call_session.call_task_id` 落库）
- **ESL 多源 bgapi**：执行器与 inbound handler 共用 ESL 连接；`_audio_fork_started` set 已防重复触发，需确认 bgapi originate 的 job-uuid 不与现有流程冲突
- **号码隐私**：`phone_hash` 去重 + `phone_masked` 展示，明文 `user_key` 仅内部（与现有 inbound 一致）
- **死信保护**：`max_attempts` 硬上限 + 终态落库，防止号码被无限重拨

## 8. 里程碑对照（与 proposal §验证策略）

- **M1**：§2.2 originate + §2.3 answer 分支 + §3.2 call_session 加列 → 跑通 1000
- **M2**：§3.1 call_target 表 + §4.1 执行器 + §5 号码 UI + 并发
- **M3**：§4.2 重拨回写 + §4.4 调度 + §4.3 redial_strategy
- **M4**：§5 进度查询 + 前端轮询
