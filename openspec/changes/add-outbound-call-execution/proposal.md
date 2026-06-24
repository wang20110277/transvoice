# Proposal: 外呼任务执行引擎

## 背景

现状：`callbot.call_task` 仅完成**定义层**——表（`agent-flow/src/db/models.py:324`）+ Console 全套 CRUD（`call-tasks-service.ts` / `CallTasksManager.tsx`）+ `calltask:*` RBAC + tenant 隔离 + prompt 三维度加载已就绪。但**执行引擎整层缺失**：`openspec/specs/call-task-management/spec.md` 的「执行边界声明」明确把 originate / 调度 / 重拨 / 并发 / 时段执行排除在本期能力之外，策略字段（`concurrent_limit` / `allowed_hours` / `redial_strategy`）为纯声明性存储，无执行器消费。

本变更补齐执行层，让外呼任务能真正拨打号码、驱动对话、跟踪结果。

## 需求（已确认决策）

1. **号码清单来源**：Console 手动管理 → 新增被叫号码表（关联 `task_id`），提供上传 / 录入 / 编辑 UI。
2. **触发模型**：手动立即启动 + 定时调度（按 `allowed_hours` 时段自动起停）两者都要。
3. **本期范围（完整闭环）**：
   - 按 `concurrent_limit` 并发拨打
   - 按 `redial_strategy` 对未接通 / 失败号码重拨
   - 按 `allowed_hours` 时段控制
   - Console 实时任务进度 + 每号结果统计
   - 号码清单管理 UI
4. **A-leg 呼出端点（分阶段，可配置）**：
   - **第一阶段**：拨打本地注册分机 **1000**（softphone）跑通整条链路——不依赖任何外部 SIP 中继。
   - **第二阶段（后期独立变更）**：接入真实 SIP 中继 / 网关。
   - originate 端点需抽象为可配置项（本地分机 ↔ 网关），避免硬编码。
5. **B-leg（接进 AI）**：复用 inbound 的事件驱动机制——originate 接通后触发同一个 `CHANNEL_ANSWER` 事件，agent-flow 现有 ESL 处理器接管（解析 → 注册 → `audio_fork` → 对话流水线 → 录音 → 归档）。区别仅在**三元组来源**：
   - inbound：DID → `_resolve_inbound_route()` → `(tenant_id, biz_type, scenario)`
   - outbound：`call_task.prompt_id` → 反查 `prompt_config` → `(tenant_id, biz_type, scenario)`（**新增外呼路由解析**，不改动 inbound 路径）。

## 成功标准

- **单条链路**：originate 拨 1000 → 软电话振铃接听 → AI 对话流水线正常（ASR/LLM/TTS/barge-in）→ `call_session` / `call_turn` 正确落库且**关联到 `call_task`** → 挂断后录音归档。
- **并发 + 重拨**：按 `concurrent_limit` 并发；未接通 / 失败按 `redial_strategy` 自动重拨且次数受控。
- **调度**：按 `allowed_hours` 时段由调度器自动起停。
- **进度可见**：Console 实时展示任务进度（待呼 / 呼叫中 / 已接通 / 失败 / 已重拨 N 次）。

## 边界（不在本期范围）

- 预测式外呼（预判坐席接听节奏）
- 人工坐席转接（human handoff to live agent）
- 号码黑 / 白名单、全局去重（本期仅做任务内最简去重）
- 真实 SIP 中继 / 网关对接（端点抽象到位即可，对接为后期独立变更）
- inbound 呼入路径任何改动（只复用其事件驱动机制，不改其逻辑）

## 验证策略（分阶段里程碑）

| 里程碑 | 验证内容 | 依赖 |
|--------|----------|------|
| **M1** | 单条 originate → 1000 → AI 对话 → 落库（关联 task）+ 录音归档；打通外呼整链路 | 外呼路由解析、A-leg 端点配置（1000）、复用 inbound CHANNEL_ANSWER |
| **M2** | 号码清单表 + Console 录入 / 上传 UI + 手动启动 + `concurrent_limit` 并发 | call_target 表、执行器并发控制 |
| **M3** | `redial_strategy` 重拨 + `allowed_hours` 时段调度 | 重拨状态机、调度器 |
| **M4** | Console 实时进度 / 结果统计 UI | 进度查询 API |

> 注：本变更会**扩展** `call-task-management` 能力规格（移除「执行边界声明」中的执行排除条款），或新增独立能力规格 `outbound-call-execution`——具体在 spec 阶段定。
