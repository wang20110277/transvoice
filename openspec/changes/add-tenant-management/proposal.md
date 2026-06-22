# Proposal: Console 租户管理 — tenant 主表 + 多租户归属 + 平台管理员

## 背景

当前 `tenant_id` 是散落在 `console_auth.user` / `callbot.prompt_config` / `callbot.prompt_version` / `callbot.inbound_route` 等表的 `text` 字符串,**无独立租户主表、无 CRUD API、无管理 UI、无用户分配界面**。

具体缺口:

- **无租户主表**:`tenant_id` 是自由字符串,无元数据(名称/状态/配额),无法对租户本身做启用·停用、命名、配额管理。
- **1 用户硬绑 1 租户**:`console_auth.user.tenant_id` 单值字段,用户无法归属多个租户、无法切换;新增租户需手动改 SQL 或 seed。
- **无跨租户管理角色**:所有用户 `role=admin`,但只能看本租户;没有平台管理员能统筹管理所有租户。
- **管理靠 SQL/seed**:无 `/api/tenants`、无「租户管理」菜单;增删租户/分配用户只能 `docker exec psql` 或改 `seed.ts`。

本期把 `tenant_id` 从字符串升级为**有主表、可管理、可多归属**的一等公民,补齐 console 的租户管理能力。

## 需求

1. **租户主表**:新增 `tenant`(id/名称/status/quota JSON,本期 quota 只存不校验)+ 存量 `tenant_id` 字符串值(`'default'`/`'galaxy_fin'` 等)迁移成 tenant 记录。
2. **1 用户多租户**:新增 `user_tenant` 关联表,用户可归属多个租户;session 携带 `activeTenantId`,登录后可切换;业务 API 隔离键从 `session.user.tenantId` 改为 `session.activeTenantId`。
3. **平台管理员角色**:新增 `role=platform_admin`,跨租户管理所有租户(CRUD tenant + 用户↔租户分配);普通 admin 仅在自己所属租户内操作。
4. **管理 UI**:新增「租户管理」菜单(仅 platform_admin 可见)— 租户 CRUD + 用户↔租户分配;非平台管理员登录后增加「切换租户」入口。

## 范围

### 包含

- **数据模型**:`tenant` 主表(status/quota JSON)+ `user_tenant` 关联表;存量 `tenant_id` 字符串迁移脚本(回填 tenant 记录 + 建立 user↔tenant 关联)。
- **API**:`/api/tenants` CRUD(platform_admin)+ 用户↔租户分配 API(platform_admin)+ session 切换租户端点。
- **认证/会话**:Better Auth 增加 `platform_admin` 角色;session 增加 `activeTenantId`(可切换);`requireTenantId()` 等隔离工具改为读 `activeTenantId`。
- **UI**:「租户管理」页(tenant CRUD + 用户分配)+ `ConsoleShell` 菜单(平台管理员可见「租户管理」)+ 顶栏「切换租户」选择器。
- **迁移幂等**:可重复执行,已存在的 tenant/user_tenant 记录跳过。

### 不包含

- **agent-flow 侧改动**:agent-flow 继续把 `tenant_id` 当字符串消费,不建 tenant 模型、不校验存在性(本期仅 console 单侧)。
- **配额强制校验**:`quota` 字段本期只存储,不实现「超额拒绝」逻辑(后续迭代)。
- **租户级计费/用量统计**:本期不做用量采集与计费。
- **Console 其他模块**(知识库/记忆系统/通话记录)后端 —— 不在本次范围。

## 决策记录(proposal 阶段确认)

| 决策点 | 选择 | 备注 |
|--------|------|------|
| 管理权限 | 新增 `platform_admin` 角色 | 跨租户管理;普通 admin 仅本租户 |
| 用户-租户关系 | 1 用户多租户 | 新增 `user_tenant` 关联表,session 带 `activeTenantId` 可切换(**破坏性变更**) |
| 配额实现 | 只存不校验 | `quota` JSON 字段预留,本期不强制 |
| 变更边界 | 仅 console | 存量 `tenant_id` 字符串迁移成记录,agent-flow 不动 |
