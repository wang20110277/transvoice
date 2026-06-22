# Tasks: Console 租户管理 — tenant 主表 + 多租户归属 + 平台管理员

> 按执行依赖排序(先依赖,后依赖方)。每个任务可独立验证。

## 1. DB Schema 与存量迁移(console_auth)

- [ ] 1.1 Drizzle schema 新增 `tenant` 主表(id TEXT PK / name / status default 'active' / quota JSONB / description / 审计;UNIQUE(name))
- [ ] 1.2 Drizzle schema 新增 `user_tenant` 关联表(user_id FK / tenant_id FK / is_primary;UNIQUE(user_id,tenant_id);部分唯一索引 `WHERE is_primary`)
- [ ] 1.3 `console_auth.session` 加 `active_tenant_id TEXT`(可空);Drizzle session 模型同步
- [ ] 1.4 编写 `src/db/migrations/0002_tenant_management.sql`:建 2 表 + session 加列(均 `IF NOT EXISTS`)
- [ ] 1.5 编写 `src/db/seed-tenants.ts`:回填 tenant(DISTINCT user.tenant_id → tenant 表)+ 建立 user_tenant 关联(每用户 is_primary=true)+ seed platform_admin 账号(platform@transvoice.local);全部 `ON CONFLICT DO NOTHING` 幂等;package.json 加 `db:seed-tenants` 脚本
- [ ] 1.6 验证:`docker exec callbot-postgres psql -U postgres -d callbot -f .../0002_tenant_management.sql` + `npm run db:seed-tenants` 成功;重跑不报错;tenant/user_tenant 行数符合预期

## 2. 认证/会话改造(Better Auth + session + guards)

- [ ] 2.1 `src/auth.ts`:Better Auth `session.additionalFields` 注入 `activeTenantId`(会话级);user additionalFields 保留 tenantId(主租户缓存)
- [ ] 2.2 `src/lib/permissions.ts`:新增 `platform_admin` 角色(继承 admin 全部权限 + `menu:tenant`/`tenant:*`/`user:*`);扩展 `PermissionCode` 联合类型
- [ ] 2.3 `src/auth/session.ts`:`requireTenantId()` 改按优先级 `session.activeTenantId ?? user.tenantId ?? 'default'`;新增 `requireActiveTenantId()` 显式别名
- [ ] 2.4 `src/lib/guards.ts`:`ctxFromSession().tenantId` 改为活跃租户;新增 `requirePlatformAdmin()` 守卫(role≠platform_admin → 403)
- [ ] 2.5 验证:platform_admin / admin 登录后 `requireTenantId()` 返回各自活跃租户;普通 admin 触发 `requirePlatformAdmin()` 返回 403

## 3. 租户管理 API(platform_admin 守卫)

- [ ] 3.1 `src/app/api/tenants/route.ts`:GET 列表(`tenant:view`)/ POST 新建(`tenant:create`,id kebab-case 唯一校验)
- [ ] 3.2 `src/app/api/tenants/[id]/route.ts`:GET 详情 / PUT 改名·停用 / DELETE(校验无 user_tenant 关联,有则 409)
- [ ] 3.3 `src/app/api/users/route.ts`:GET 跨租户用户列表(`user:view`)
- [ ] 3.4 `src/app/api/users/[id]/tenants/route.ts`:POST 分配/取消租户、设主(同步更新 user.tenant_id 缓存)(`user:assign-tenant`)
- [ ] 3.5 `src/app/api/session/switch-tenant/route.ts`:POST 切换活跃租户(普通用户查 user_tenant 归属,platform_admin 任意;disabled tenant → 409;更新 session.active_tenant_id)
- [ ] 3.6 验证:platform_admin 可 CRUD tenant/分配用户/切换;普通 admin 调租户管理 API(除 switch-tenant)返回 403;切 disabled 租户返回 409

## 4. 租户管理 UI

- [ ] 4.1 `src/components/ConsoleShell.tsx`:MENUS 加「租户管理」项(enabled = hasPermission(menu:tenant),非平台用户不渲染);header 加 `<TenantSwitcher>` 下拉(可切换租户列表,切换调 /api/session/switch-tenant 后 router.refresh)
- [ ] 4.2 `src/app/tenants/page.tsx` + `src/components/TenantsManager.tsx`:租户列表(名称/status/quota 摘要)+ 新建/编辑/停用表单
- [ ] 4.3 用户分配子面板:每租户展开用户归属勾选 + 设主租户;调 /api/users/:id/tenants
- [ ] 4.4 验证:platform_admin 登录见「租户管理」菜单 + 顶栏切换器;普通 admin 不见菜单;切换租户后 prompts 页数据随之变化

## 5. 收尾验证

- [ ] 5.1 多租户隔离端到端:platform_admin 切 default→galaxy_fin,prompts/inbound-routes 数据隔离正确
- [ ] 5.2 存量兼容:旧 session(active_tenant_id 空)请求 fallback 到 user.tenant_id,不中断
- [ ] 5.3 `openspec validate add-tenant-management --strict` 通过(若 CLI 可用)
- [ ] 5.4 `codegraph sync` + CRG 索引更新;session/guards 改造点测试覆盖
