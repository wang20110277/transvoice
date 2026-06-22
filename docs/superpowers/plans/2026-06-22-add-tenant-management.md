# 执行 checkpoint:add-tenant-management

> 详细步骤、改动文件、验证方式见 `openspec/changes/add-tenant-management/plan-ready.md`。
> 本文件仅跟踪执行进度(断点恢复依据)。每 Task 一个 commit。

## Task 1: DB Schema 与存量迁移
- [x] 1.1 schema.ts 加 tenant 主表
- [x] 1.2 schema.ts 加 user_tenant 关联表
- [x] 1.3 schema.ts session 加 active_tenant_id
- [x] 1.4 0002_tenant_management.sql
- [x] 1.5 seed-tenants.ts + package.json 脚本
- [x] 1.6 迁移完整性验证

## Task 2: 认证/会话改造
- [x] 2.1 auth.ts session additionalFields
- [x] 2.2 permissions.ts platform_admin + 权限码
- [x] 2.3 session.ts 隔离键优先级
- [x] 2.4 guards.ts 活跃租户 + requirePlatformAdmin
- [x] 2.5 隔离链路验证(fallback 通过;platform_admin 守卫端到端留 Task 3)

## Task 3: 租户管理 API
- [x] 3.1 /api/tenants 列表+新建
- [x] 3.2 /api/tenants/:id 详情/改/删
- [x] 3.3 /api/users 跨租户列表
- [x] 3.4 /api/users/:id/tenants 分配
- [x] 3.5 /api/session/switch-tenant 切换
- [x] 3.6 API 守卫验证(端到端 8 项全过)

## Task 4: 租户管理 UI
- [x] 4.1 ConsoleShell 菜单+切换器
- [x] 4.2 TenantSwitcher 组件
- [x] 4.3 /tenants 页 + TenantsManager
- [x] 4.4 用户分配子面板
- [x] 4.5 UI 全流程验证(tsc + page 守卫 + session/tenants 归属隔离)

## Task 5: 收尾验证
- [x] 5.1 多租户隔离端到端(Task 3 验证:switch→galaxy_fin prompts=[])
- [x] 5.2 存量兼容回归(Task 2 验证:旧 session fallback user.tenantId)
- [x] 5.3 OpenSpec 校验(valid)
- [x] 5.4 单测 17 passed;codegraph sync 待用户触发(console 首次索引)
