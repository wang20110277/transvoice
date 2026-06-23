# Close Issues: align-prompt-config-pipeline

> close 阶段一致性核验发现的不一致项。close 阶段不改代码，按 openflow/close.md 流程记录并交用户定夺。

## 核验范围

4 个 capability specs：prompt-config-management / prompt-config-consumption / inbound-routing / call-task-management。

## 核验结论总览

| Capability | 状态 | 判定 |
|---|---|---|
| prompt-config-management | prompt_config/prompt_version 表 + Console /api/prompts(含 clone/publish/rollback/test/versions) + /prompts 页 | ✅ 完整 |
| prompt-config-consumption | get_system_prompt(tenant_id,biz_type,scenario) + cb:prompt Redis key + render.py + 全链路透传 + 多租户隔离 | ✅ 完整 |
| inbound-routing | inbound_route 表 + Console /api/inbound-routes + /inbound-routes 页 + agent-flow _resolve_inbound_dimensions | ✅ 完整 |
| **call-task-management** | call_task DB 表(alembic 0003 + models.py)已建；Console API/UI/schema 映射/perms **全缺** | ❌ 部分 |

## 缓存失效契约（断点 ⑤）——已实现，非问题

design.md §6 要求 Console publish 触发 `DEL cb:prompt:{tenant_id}:{biz_type}:{scenario}` 零延迟生效。核验确认**已实现**：
- agent-flow 侧：`prompt_config.py:81 invalidate_prompt_cache(tenant_id, biz_type, scenario)`
- Console 侧：`prompts-service.ts` import `invalidatePromptCache` from `./redis`，在 `publish()`(L173)、rollback(L247)、update(L288) 三处调用
- 满足 spec "缓存失效接口" Requirement 与 "发布触发失效" Scenario

---

## ❌ Issue 1：call-task-management capability 的 Console CRUD 未实现

**Spec 要求**（`specs/call-task-management/spec.md` "外呼任务 CRUD(定义层)"）：

> 系统 SHALL 在 Console 提供外呼任务的创建/查询/编辑/删除 API,受 Better Auth 认证与 `calltask:*` RBAC 守护,按 `tenant_id` 隔离。

**实际实现状态**：

| 产物 | 状态 |
|---|---|
| `callbot.call_task` DB 表 | ✅ 已建（`alembic/versions/0003_prompt_pipeline_align.py` + `agent-flow/src/db/models.py:324-349`） |
| Console `/api/call-tasks` route | ❌ 不存在（`console/server/src/app/api/` 下无 call-tasks 目录） |
| Console `/call-tasks` 页 | ❌ 不存在（`console/server/src/app/` 下无 call-tasks page） |
| `schema.ts` callTask Drizzle 映射 | ❌ 未声明 |
| `permissions.ts` `calltask:*` 权限码 | ❌ 未声明 |
| ConsoleShell「外呼任务」菜单 | enabled:false（"下期"标记，`ConsoleShell.tsx:34`） |

**影响**：管理员无法在 Console 创建/编辑外呼任务定义；`call_task` 表虽存在但无运营入口。design.md §3.4 称该表"本期仅定义,执行另算"——DB 定义层已落地，但 spec 要求的 **Console 运营 CRUD** 这一半未做。

**根因推测**：实现时聚焦 prompt-config 主链路（管理+消费+呼入路由），call-task 的 Console CRUD 被推迟（与菜单"下期"标记一致）。spec 的 SHALL 未完全兑现。

**严重度**：中。不影响已实现的 prompt-config 核心链路；但属 spec 显式 SHALL 未满足，归档前应如实记录。

## 建议处理

二选一（交用户定夺）：

1. **归档 + 开新变更补齐**：本变更按现状归档（3/4 capability 完整），call-task Console CRUD 开 `add-call-task-console-crud` 新变更实现。适合：call-task 运营非当前急务，优先推进 add-call-records-and-recording。

2. **暂不归档，先补齐**：用 `/openflow build` 先实现 call-task CRUD（API + page + schema 映射 + perms，约 1 个 Task 工作量），再 close。适合：希望本变更 spec 完整兑现。
