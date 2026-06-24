# Tasks: 外呼任务执行引擎

> 按执行依赖排序（先依赖，后依赖方），并对照里程碑 M1→M4。每个任务可独立验证。
> 复用策略锚点：`_on_channel_answer`（`agent-flow/main.py:266`）/ `_on_channel_hangup`（`main.py:224`）只加 outbound 分支，不重写。

## M1：打通单条外呼链路（拨本地分机 1000）

### 1. DB Schema（agent-flow SQLAlchemy + alembic）
- [ ] 1.1 `CallSession` 模型增 `call_task_id: Mapped[int|None]`（FK→`call_task.id`，nullable）+ `call_target_id: Mapped[int|None]`（nullable）
- [ ] 1.2 新增 `CallTarget` 模型（`callbot.call_target`：id/task_id FK/tenant_id/phone_hash/phone_masked/user_key/status(pending)/attempt_count(0)/max_attempts/next_attempt_ts nullable/last_call_session_id nullable/last_hangup_cause nullable + 审计列；`UNIQUE(task_id, phone_hash)`、`INDEX(task_id, status)`）
- [ ] 1.3 alembic migration `0004_outbound_call_execution`：`call_session` add 2 cols（nullable）+ create `call_target` 表 + 约束
- [ ] 1.4 验证：`cd agent-flow && PYTHONPATH=$(pwd)/src alembic upgrade head` 成功，表结构与 Drizzle 侧映射一致

### 2. originate 命令构造 + 端点配置
- [ ] 2.1 `src/config.py` 增配置：`outbound_endpoint_template`（默认 `sofia/internal/{phone}`）、`outbound_caller_id`、`outbound_scheduler_tick_sec`(10)、`outbound_global_concurrency`(0=不限)
- [ ] 2.2 新增 `src/outbound/originate.py`：`build_originate_command(target, task, settings) -> str`，把三元组 + task_id/target_id/user_key/ai_outbound 注入 channel vars，端点由 template 渲染，B-leg app `&park()`
- [ ] 2.3 验证：单元测试 `build_originate_command` 输出含 `ai_outbound=true`/三元组/`sofia/internal/1000`/`&park()`

### 3. answer/hangup 处理器 outbound 分支（复用 inbound 管线）
- [ ] 3.1 `main.py _on_channel_answer` 开头加分支：`variable_ai_outbound` 存在时从 vars 读三元组 + call_task_id/call_target_id，跳过 `_resolve_inbound_dimensions`；`insert_call_session` dict 携带 call_task_id/call_target_id
- [ ] 3.2 `main.py _on_channel_hangup` 加 outbound 分支：读 channel vars 的 call_target_id，按 `Hangup-Cause` 更新 `call_target`（`NORMAL_CLEARING`→answered，`NO_ANSWER`/`RECOVERY_ON_TIMER_EXPIRE`→no_answer，其余→failed）；更新 `last_call_session_id`/`last_hangup_cause`
- [ ] 3.3 新增 `src/storage/repository.py`：`update_call_target_outcome(target_id, status, hangup_cause, call_session_id)` + `claim_call_target_for_dial(target_id)`（pending→dialing）
- [ ] 3.4 验证：本地手工 `bgapi originate sofia/internal/1000 &park()`（带 vars）→ 1000 摘机 → 日志见外呼分支三元组 → call_session 落库 call_task_id 非空 → AI 对话/录音归档与 inbound 等价

## M2：号码清单 + 执行器 + 并发 + Console 启停

### 4. 进程内执行器
- [ ] 4.1 新增 `src/outbound/executor.py` `OutboundExecutor`：lifespan 启停；持有 `dict[task_id -> asyncio.Semaphore(concurrent_limit)]` + 周期 tick 协程
- [ ] 4.2 tick：扫 `call_task.status=running` → 拉 `call_target.status=pending AND (next_attempt_ts IS NULL OR next_attempt_ts<=now)` → claim（pending→dialing）→ semaphore 内 `esl.bgapi(originate)`
- [ ] 4.3 `main.py` lifespan 注入执行器（启动/优雅停止）；ESL 就绪后 executor.start()
- [ ] 4.4 验证：建 1 个 task + 3 个 call_target，启动后日志见并发 originate（受 concurrent_limit 限流），dialing 行数 ≤ limit

### 5. Console 号码清单管理 + 启停
- [ ] 5.1 Drizzle schema 增 `callTarget` 表（列名与 SQLAlchemy 一致）；`callSession` 增 call_task_id/call_target_id
- [ ] 5.2 `console/server/src/lib/call-targets-service.ts`：`listByTask`/`create`/`bulkCreate`(CSV 解析去重)/`remove`，按 tenant_id 隔离
- [ ] 5.3 Route handlers `/api/call-tasks/[id]/targets`（GET/POST/DELETE）；CSV 上传端点
- [ ] 5.4 `CallTasksManager.tsx` 任务详情/展开区：号码清单表格（上传 CSV + 单条录入 + 删除）+ 启停按钮（PATCH status running/paused）
- [ ] 5.5 验证：Console 上传 CSV → call_target 入库去重；点启动 → agent-flow 日志见 originate；切租户互不可见

## M3：重拨 + 时段调度

### 6. 重拨状态机
- [ ] 6.1 `_on_channel_hangup` outbound 分支完善：可重拨原因 + `attempt_count < max_attempts` → `update` 置 pending + next_attempt_ts = now + interval_min；否则终态 failed/done
- [ ] 6.2 `redial_strategy` JSONB 规范：`{max_retries, interval_min, retry_on_causes[]}`；call_task 录入时 Console 校验结构
- [ ] 6.3 执行器任务完成判定：所有 call_target 终态且无 pending → `call_task.status=completed`
- [ ] 6.4 验证：构造 NO_ANSWER 号码（关机/拒接分机）→ 见重拨（attempt_count++，next_attempt_ts 推后）→ 达上限转 failed

### 7. allowed_hours 时段调度
- [ ] 7.1 tick 内校验 `allowed_hours`（`HH:MM-HH:MM` 解析，空=不限）→ 窗口外不发起新 originate（不中断在通话）
- [ ] 7.2 Console 任务编辑 allowed_hours 字段（输入校验 HH:MM-HH:MM）
- [ ] 7.3 验证：配 allowed_hours 覆盖当前时间外 → 不外呼；改回覆盖 → 自动恢复

## M4：进度查询 + 前端轮询

### 8. 进度查询
- [ ] 8.1 `console/server/src/lib/call-targets-service.ts` 增 `progress(taskId, tenantId)` → `{pending,dialing,answered,no_answer,failed,done,total}`
- [ ] 8.2 Route handler `/api/call-tasks/[id]/progress` GET（`calltask:*`/`call:view`）
- [ ] 8.3 `CallTasksManager.tsx` 运行中任务轮询进度条（setInterval，任务终态停止）
- [ ] 8.4 验证：运行任务进度计数与 call_target 实际一致；终态停止轮询

## 收尾验证

- [ ] 9.1 inbound 路径回归：呼入通话 call_task_id/call_target_id 为 NULL，对话/录音正常
- [ ] 9.2 端到端：任务上传号码 → 启动 → originate 拨 1000 → 接通 AI 对话 → 落库关联 → 挂断回写 → 重拨/终态 → 完成判定
- [ ] 9.3 多租户隔离：A 任务号码/进度对 B 不可见（跨租户 404）
- [ ] 9.4 资源清理：任务暂停/完成/进程停止，in-flight 通话不被中断；executor 优雅停止无泄漏
- [ ] 9.5 `openspec validate add-outbound-call-execution --strict` 通过（CLI 可用时）；codegraph sync
