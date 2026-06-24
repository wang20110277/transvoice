# 实现计划：外呼任务执行引擎

## 来源
- 提案：openspec/changes/add-outbound-call-execution/proposal.md
- 设计：openspec/changes/add-outbound-call-execution/design.md
- 规格：openspec/changes/add-outbound-call-execution/specs/call-task-management/spec.md
- 任务：openspec/changes/add-outbound-call-execution/tasks.md

## 前置状态
复用 inbound 事件驱动管线（`_on_channel_answer`/`_on_channel_hangup` 只加 outbound 分支，不重写）。验证基线：每个 DB 改动 `cd agent-flow && PYTHONPATH=$(pwd)/src alembic upgrade head`；Python 单测 `PYTHONPATH=$(pwd):$(pwd)/src pytest`；CLI 手测 originate 走 FreeSWITCH。

---

## 实现步骤

### Task 1: DB Schema（M1 基础）
- **目标**：CallSession 加外呼关联列 + 新建 call_target 号码清单表
- **步骤**：
  1. `agent-flow/src/db/models.py`：`CallSession` 增 `call_task_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("callbot.call_task.id"), nullable=True)` + `call_target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)`
  2. `agent-flow/src/db/models.py`：新增 `CallTarget` 类（`callbot.call_target`：id/task_id FK/tenant_id/phone_hash/phone_masked/user_key/status(Text default pending)/attempt_count(int 0)/max_attempts(int)/next_attempt_ts(DateTime nullable)/last_call_session_id(BigInteger nullable)/last_hangup_cause(Text nullable) + create_time/update_time；`UniqueConstraint(task_id, phone_hash, name="uq_call_target_task_phone")`、`Index("ix_call_target_task_status", task_id, status)`）
  3. `agent-flow/alembic/versions/0004_outbound_call_execution.py`：`call_session` ADD COLUMN call_task_id/call_target_id（nullable）+ CREATE TABLE call_target + 约束/索引
- **验证**：`cd agent-flow && PYTHONPATH=$(pwd)/src alembic upgrade head` 成功；`\d callbot.call_target` 含全部列与约束

### Task 2: originate 命令构造 + 端点配置（M1）
- **目标**：把三元组/任务ID 注入 channel vars 的 originate 命令构造器
- **步骤**：
  1. `agent-flow/src/config.py`：Settings 增 `outbound_endpoint_template: str = "sofia/internal/{phone}"`、`outbound_caller_id: str = ""`、`outbound_scheduler_tick_sec: int = 10`、`outbound_global_concurrency: int = 0`
  2. `agent-flow/src/outbound/__init__.py`（新建空）+ `agent-flow/src/outbound/originate.py`（新建）：`build_originate_command(target_row, task_row, settings) -> str`，channel vars 含 `ai_outbound=true,call_task_id=,call_target_id=,tenant_id=,biz_type=,scenario=,user_key=,ignore_early_media=true`(+ caller_id 非空时)，端点 `outbound_endpoint_template.format(phone=...)`，B-leg `&park()`，返回完整 `originate {...} {endpoint} &park()` 串
  3. `agent-flow/src/outbound/test_originate.py`（新建，单测）：断言输出含 `ai_outbound=true`/三元组/`sofia/internal/1000`/`&park()`/`ignore_early_media=true`
- **验证**：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest src/outbound/test_originate.py -v` 通过

### Task 3: answer/hangup 处理器 outbound 分支（M1 核心，打通拨 1000）
- **目标**：复用 inbound 管线，外呼接通后进入等价对话流水线
- **步骤**：
  1. `agent-flow/src/storage/repository.py`：增 `claim_call_target_for_dial(target_id) -> bool`（pending→dialing，CAS UPDATE 返回是否成功）+ `update_call_target_outcome(target_id, status, hangup_cause, call_session_id)`（更新 status/last_hangup_cause/last_call_session_id）
  2. `agent-flow/main.py _on_channel_answer`（~L266）：开头加分支 `if event.headers.get("variable_ai_outbound"):` → 从 vars 读 tenant_id/biz_type/scenario/user_key/call_task_id/call_target_id，赋给后续用到的变量，`else:` 走原 DID 解析（原逻辑包进 else）；`insert_call_session` dict 增 `"call_task_id": ..., "call_target_id": ...`（inbound 路径这两键 None）
  3. `agent-flow/main.py _on_channel_hangup`（~L224）：加 outbound 分支 `call_target_id = event.headers.get("variable_call_target_id")`，非空时按 `Hangup-Cause` 调 `update_call_target_outcome`（NORMAL_CLEARING→answered；NO_ANSWER/RECOVERY_ON_TIMER_EXPIRE→no_answer；其余→failed）
- **验证**：本地 `./scripts/local.sh` 起 fs+flow；FreeSWITCH 手测 `bgapi originate {ai_outbound=true,call_task_id=1,call_target_id=1,tenant_id=default,biz_type=marketing,scenario=default,user_key=1000,ignore_early_media=true}sofia/internal/1000 &park()`；1000 软电话摘机 → 日志见外呼分支三元组 → call_session 落库 call_task_id=1 → AI 对话+录音归档正常

### Task 4: 进程内执行器（M2 核心）
- **目标**：tick 调度 + 并发限流地 originate pending 号码
- **步骤**：
  1. `agent-flow/src/outbound/executor.py`（新建）：`OutboundExecutor` 单例，`__init__(esl, settings)`，`dict[task_id, Semaphore]` 缓存，`async def start()/stop()`，`async def _tick()`：查 status=running 任务 → 校验 allowed_hours → 查 pending&可拨号码 → per-task semaphore 限流 `esl.bgapi(originate)`，fire-and-forget
  2. `_tick` 内调 `repository` 查询：`list_running_tasks()`、`list_dialable_targets(task_id, limit)`（pending AND (next_attempt_ts IS NULL OR <=now)）；originate 前 `claim_call_target_for_dial` 成功才拨
  3. `agent-flow/src/storage/repository.py`：补 `list_running_tasks()` + `list_dialable_targets(task_id, limit)` + `is_within_allowed_hours(allowed_hours_str)`（解析 "HH:MM-HH:MM"，空/None=True）
  4. `agent-flow/main.py` lifespan：ESL 就绪后 `_outbound_executor = OutboundExecutor(esl, settings); await _outbound_executor.start()`；shutdown 时 `await _outbound_executor.stop()`；tick 周期 `settings.outbound_scheduler_tick_sec`
- **验证**：插 1 个 call_task(status=running) + 3 个 call_target(pending)；等 1 tick → 日志见 ≤concurrent_limit 个 originate + 对应行转 dialing

### Task 5: Console 号码清单 + 启停（M2 UI）
- **目标**：Console 管理 call_target + 启停任务
- **步骤**：
  1. `console/server/src/db/schema.ts`：增 `callTarget` 表（列名 snake_case 对齐 SQLAlchemy）+ `callSession` 增 callTaskId/callTargetId
  2. `console/server/src/lib/call-targets-service.ts`（新建）：`listByTask(taskId, tenantId)`/`create`/`bulkCreateFromCsv(text, taskId, tenantId, userEmail)`（任务内 phone_hash 去重）/`remove`/`progress(taskId, tenantId)`（状态聚合）
  3. `console/server/src/app/api/call-tasks/[id]/targets/route.ts`（新建）：GET 列表 / POST 单条或批量；`[id]/targets/[targetId]/route.ts`（新建）：DELETE；`call-task-status` 在现有 `call-tasks/[id]/route.ts` PATCH 已支持 status
  4. `console/server/src/components/CallTasksManager.tsx`：任务行展开区 = 号码清单表（上传 CSV 按钮 + 单条录入 + 删除）+ 启停按钮（PATCH status running/paused）；CSV 上传调批量 API
- **验证**：`cd console/server && npm run lint`；上传 CSV → call_target 入库去重；点启动 → agent-flow 日志见 originate；切租户互不可见

### Task 6: 重拨状态机（M3）
- **目标**：未接通按 redial_strategy 重拨，达上限终态
- **步骤**：
  1. `agent-flow/main.py _on_channel_hangup` outbound 分支完善：读 `task.redial_strategy`（max_retries/interval_min/retry_on_causes）；可重拨原因 ∈ retry_on_causes 且 attempt_count < max_attempts → `reset_call_target_for_redial(target_id, interval_min)`（置 pending + attempt_count+1 + next_attempt_ts=now+interval_min）；否则终态 failed/done
  2. `agent-flow/src/storage/repository.py`：增 `reset_call_target_for_redial(target_id, interval_min)`；`update_call_target_outcome` 增 attempt_count 自增
  3. `OutboundExecutor._tick`：任务完成判定（所有 call_target 终态且无 pending）→ `mark_task_completed(task_id)`
- **验证**：构造 NO_ANSWER（关机分机/拒接）→ 见重拨 attempt_count++ next_attempt_ts 推后 → 达 max_attempts 转 failed；接通转 done

### Task 7: allowed_hours 时段调度（M3）
- **目标**：窗口外不发起新外呼
- **步骤**：
  1. `agent-flow/src/outbound/executor.py _tick`：对每任务先 `is_within_allowed_hours(task.allowed_hours)`，False 则 skip originate（不中断在通话）
  2. `console/server/src/components/CallTasksManager.tsx`：任务编辑表单增 allowed_hours 输入（校验 `^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$`，空=不限）
- **验证**：配 allowed_hours 覆盖当前时间外 → 不外呼；改回 → 自动恢复

### Task 8: 进度查询 + 前端轮询（M4）
- **目标**：实时展示任务进度
- **步骤**：
  1. `console/server/src/lib/call-targets-service.ts progress()` 已于 Task 5 写入；`console/server/src/app/api/call-tasks/[id]/progress/route.ts`（新建）：GET → requirePermission → progress
  2. `console/server/src/lib/call-tasks-api.ts`：增 `progress(taskId)`
  3. `console/server/src/components/CallTasksManager.tsx`：running 任务 `setInterval` 轮询 progress 渲染进度条/计数；任务转 completed/failed/paused 停止轮询
- **验证**：运行任务进度计数与 call_target 实际一致；终态停止轮询

### Task 9: 收尾验证
- **目标**：inbound 回归 + 端到端 + 多租户 + 资源清理
- **步骤**：
  1. inbound 回归：呼入通话 call_task_id/call_target_id 为 NULL，对话/录音/归档正常（answer/hangup else 分支未受影响）
  2. 端到端：任务上传号码 → 启动 → originate 拨 1000 → 接通 AI 对话 → 落库关联 → 挂断回写 → 重拨/终态 → completed
  3. 多租户：A 任务号码/进度对 B 不可见（跨租户 404）
  4. 资源清理：任务暂停/完成/进程 stop → in-flight 通话不被中断，executor 无泄漏
  5. `openspec validate add-outbound-call-execution --strict` 通过；`codegraph sync`
