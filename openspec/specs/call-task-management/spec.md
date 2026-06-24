# call-task-management Specification

## Purpose
TBD - created by archiving change align-prompt-config-pipeline. Update Purpose after archive.
## Requirements
### Requirement: 外呼任务定义模型

系统 SHALL 提供 `callbot.call_task` 表存储外呼任务定义,字段含 `tenant_id`、`name`、`prompt_id`(引用 `prompt_config.id`)、`kb_ids`(JSONB)、`status`、`concurrent_limit`、`allowed_hours`、`redial_strategy`(JSONB)、`dept_id`(映射 biz_type)及审计列。`prompt_id` MUST 外键引用 `prompt_config.id`,作为外呼场景三维度 `(tenant_id, biz_type, scenario)` 的来源。

#### Scenario: 创建外呼任务绑定提示词
- **WHEN** 管理员创建外呼任务并选择提示词 X
- **THEN** 系统 SHALL 写入 `call_task`,`prompt_id=X`,记录任务名/策略参数

#### Scenario: 任务定义不含执行态
- **WHEN** 创建/编辑外呼任务
- **THEN** 系统 SHALL 仅持久化定义字段,**不得**触发 originate、调度或重拨执行

### Requirement: 外呼任务 CRUD(定义层)

系统 SHALL 在 Console 提供外呼任务的创建/查询/编辑/删除 API,受 Better Auth 认证与 `calltask:*` RBAC 守护,按 `tenant_id` 隔离。`status` 字段在本期仅作定义存储(idle/running/paused/completed),无执行引擎驱动状态流转。

#### Scenario: 任务编辑
- **WHEN** 管理员编辑任务的并发上限或时段策略
- **THEN** 系统 SHALL 更新 `call_task` 对应字段,本期不产生任何外呼动作

### Requirement: 多租户隔离(外呼任务)

系统 SHALL 按 `tenant_id` 隔离外呼任务。管理员仅可见/操作本租户任务;外呼任务绑定的 `prompt_id` MUST 属于同一 `tenant_id`。

#### Scenario: 跨租户提示词绑定拒绝
- **WHEN** A 租户管理员创建任务时绑定 B 租户的 promptId
- **THEN** 系统 SHALL 拒绝并返回错误

### Requirement: 执行边界声明

系统 SHALL 提供外呼执行引擎：实现 originate 发起、调度器（tick）、重拨/并发/时段执行逻辑。`callbot.call_task.status` 作为执行引擎驱动的状态字段，流转 `idle → running → paused → completed`（失败终态 `failed`）。Console 启停操作（`PATCH /api/call-tasks/:id` 置 `running`/`paused`）SHALL 触发 agent-flow 执行器在下一调度 tick 感知并执行/暂停。`call_task` 策略字段（`concurrent_limit`/`allowed_hours`/`redial_strategy`）SHALL 被执行器消费。

> 本条 **取代** 原稳定规格中的排除条款（原条款声明本期不实现 originate/调度/重拨/并发/时段执行）。执行能力由本变更补齐。

#### Scenario: 手动启动任务
- **WHEN** 管理员把任务 status 置为 `running`
- **THEN** 系统 SHALL 在下一调度 tick 内对 `call_target.status=pending` 的号码发起 originate
- **AND** SHALL 受 `concurrent_limit` 限流

#### Scenario: 暂停任务
- **WHEN** 管理员把运行中任务 status 置为 `paused`
- **THEN** 执行器 SHALL 停止拉取新号码外呼
- **AND** SHALL NOT 中断已在通话中的号码

#### Scenario: 任务完成
- **WHEN** 任务所有号码达到终态（`answered`/`done`/`failed`）且无 pending
- **THEN** 系统 SHALL 把 `call_task.status` 置为 `completed`

### Requirement: 外呼执行复用 inbound 对话管线

系统 SHALL 在 originate 命令中把 `(tenant_id, biz_type, scenario, user_key, call_task_id, call_target_id)` 作为 channel variables 注入，并置 `ai_outbound=true` 标记。`_on_channel_answer` SHALL 增加外呼分支：检测到 `variable_ai_outbound` 时从 channel vars 读取三元组与任务/号码 ID，**跳过** DID→`inbound_route` 解析；其后 `register`/`insert_call_session`/`audio_fork_start`/`record_start` SHALL 与 inbound 完全一致。

外呼 originate 的 B-leg 应用 SHALL 使用 `&playback(silence_stream://-1)` —— 镜像 inbound 的 silence_stream，维持持续 write media path。实测 `&park()` 的 channel 不产生持续 write 帧，mod_audio_fork 下行 PCM 无载体播不到 RTP（被叫听不到 AI）；silence_stream + `TTSOutputBuffer` 静音帧保活可让 AI 下行音频正常播出。呼出端点 SHALL 由配置 `CALLBOT_OUTBOUND_ENDPOINT_TEMPLATE` 提供（默认 `user/{phone}@{domain}`，`{domain}` 取 `CALLBOT_OUTBOUND_DOMAIN`），支持后期切换 SIP 网关而代码不变。

originiate 命令 SHALL 注入 `absolute_codec_string`（配置 `CALLBOT_OUTBOUND_CODEC_STRING`，默认 `PCMA`）强制线性编解码 —— 实测 profile 默认协商 G.722 会让 mod_audio_fork 抓到的帧格式不对、ASR 收不到有效音频。空串表示不强制。

#### Scenario: 外呼接通复用对话管线
- **WHEN** originate 拨打被叫且被叫摘机触发 `CHANNEL_ANSWER`，channel vars 含 `ai_outbound=true`
- **THEN** `_on_channel_answer` SHALL 从 vars 读取 `(tenant_id, biz_type, scenario, user_key, call_task_id, call_target_id)`
- **AND** SHALL 写入 `call_session` 且 `call_task_id`/`call_target_id` 非空
- **AND** SHALL 启动 audio_fork / record，进入与 inbound 等价的对话流水线（ASR/LLM/TTS/barge-in）

#### Scenario: inbound 路径不受影响
- **WHEN** 呼入触发 `CHANNEL_ANSWER` 且 channel vars 无 `ai_outbound`
- **THEN** 系统 SHALL 走原 DID→`inbound_route` 解析路径
- **AND** `call_session.call_task_id`/`call_target_id` SHALL 为 NULL

### Requirement: 外呼被叫号码清单（call_target）

系统 SHALL 提供 `callbot.call_target` 表存储外呼号码清单，每行关联 `task_id`，字段含 `tenant_id`、`phone_hash`、`phone_masked`、`user_key`、`status`、`attempt_count`、`max_attempts`、`next_attempt_ts`、`last_call_session_id`、`last_hangup_cause` 及审计列。`UNIQUE(task_id, phone_hash)` SHALL 保证任务内号码去重。`call_target.status` 取值 `pending`/`dialing`/`answered`/`no_answer`/`failed`/`done`。

Console SHALL 提供号码清单管理 UI（CSV 上传 / 单条录入 / 列表删除），受 `calltask:*` RBAC 守护并按 `tenant_id` 隔离。

#### Scenario: 上传号码清单
- **WHEN** 管理员在某任务下上传含号码的 CSV
- **THEN** 系统 SHALL 逐行写入 `call_target`（`status=pending`），任务内重复号码去重
- **AND** SHALL 存 `phone_hash` + `phone_masked`（脱敏展示）

#### Scenario: 号码去重
- **WHEN** 同一任务下录入已存在的 `phone_hash`
- **THEN** 系统 SHALL 拒绝/跳过，不产生重复行

### Requirement: 外呼执行器（进程内 asyncio）

系统 SHALL 在 agent-flow 进程内（lifespan 启动）运行 `OutboundExecutor` 单例，跑在现有 uvloop，复用 ESL 连接 / `ActiveCallRegistry` / repository。执行器 SHALL 运行周期调度 tick（`CALLBOT_OUTBOUND_SCHEDULER_TICK_SEC`，默认 10s），每个 tick 内：扫描 `call_task.status=running` 任务 → 校验 `allowed_hours` 时段窗口 → 拉取 `call_target.status=pending AND next_attempt_ts<=now` 的号码 → 受 `asyncio.Semaphore(task.concurrent_limit)` 限流地并发 originate（`esl.bgapi`，fire-and-forget）。

#### Scenario: 并发限流
- **WHEN** 任务 `concurrent_limit=N` 且有 M>N 个 pending 号码
- **THEN** 系统 SHALL 同时最多 N 个号码处于 `dialing` 状态
- **AND** 其余 pending 号码等待槽位释放后发起

#### Scenario: 时段控制
- **WHEN** 任务配置 `allowed_hours="09:00-21:00"` 且当前时间在窗口外
- **THEN** 执行器 SHALL NOT 发起新 originate
- **AND** 窗口恢复后自动继续

### Requirement: 外呼结果回写与重拨

系统 SHALL 在 `_on_channel_hangup` 增加外呼分支，读取 `Hangup-Cause` 更新 `call_target` 行：`NORMAL_CLEARING`→`answered`（终态，不重拨）；`NO_ANSWER`/`RECOVERY_ON_TIMER_EXPIRE`→`no_answer`（可重拨）；其余失败原因按 `redial_strategy.retry_on_causes` 判定。

重拨 SHALL 仅在 `attempt_count < max_attempts` 时进行：置 `call_target.status=pending` + `next_attempt_ts=now+interval_min`；否则置终态 `failed`/`done`。`redial_strategy` JSONB 结构 SHALL 为 `{max_retries, interval_min, retry_on_causes[]}`。

#### Scenario: 接通成功不重拨
- **WHEN** 被叫接通后挂断（`Hangup-Cause=NORMAL_CLEARING`）
- **THEN** `call_target.status` SHALL 置 `answered`→`done`
- **AND** SHALL NOT 重拨

#### Scenario: 未接通按策略重拨
- **WHEN** 拨打后 `Hangup-Cause=NO_ANSWER` 且 `attempt_count < max_attempts` 且 `NO_ANSWER ∈ retry_on_causes`
- **THEN** 系统 SHALL 置 `status=pending`、`attempt_count+=1`、`next_attempt_ts=now+interval_min`
- **AND** SHALL NOT 立即重拨

#### Scenario: 达到重拨上限
- **WHEN** `attempt_count >= max_attempts`
- **THEN** `call_target.status` SHALL 置终态 `failed`
- **AND** SHALL NOT 再发起 originate

### Requirement: call_session 外呼关联列

系统 SHALL 在 `callbot.call_session` 增加 `call_task_id`（可空 FK→`call_task.id`）与 `call_target_id`（可空）两列。外呼通话这两列非空，inbound 通话为 NULL（向后兼容，inbound 写入路径无需改动）。

#### Scenario: 外呼通话关联任务
- **WHEN** 外呼通话 `CHANNEL_ANSWER`
- **THEN** `call_session` 行的 `call_task_id`/`call_target_id` SHALL 非空
- **AND** 通话记录 SHALL 能通过 `call_task_id` 回溯到任务

### Requirement: 外呼任务执行进度查询

系统 SHALL 提供 `GET /api/call-tasks/:id/progress`，按 `tenant_id` 隔离，返回该任务下 `call_target` 状态聚合（`pending`/`dialing`/`answered`/`no_answer`/`failed`/`done` 各计数 + 总数）。Console 任务详情页 SHALL 轮询该 API 展示实时进度。

#### Scenario: 进度查询
- **WHEN** 管理员查看运行中任务进度
- **THEN** API SHALL 返回各状态号码计数与总数
- **AND** SHALL 仅返回当前租户数据

