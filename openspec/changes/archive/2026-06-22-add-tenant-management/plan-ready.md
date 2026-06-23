# 实现计划:add-tenant-management

## 来源

- 提案:`openspec/changes/add-tenant-management/proposal.md`
- 设计:`openspec/changes/add-tenant-management/design.md`
- 规格:`openspec/changes/add-tenant-management/specs/tenant-management/spec.md`
- 任务:`openspec/changes/add-tenant-management/tasks.md`

> 执行顺序严格按依赖:先建表 + 迁移(DB)→ 认证/会话改造(隔离键地基)→ 租户管理 API → UI → 收尾。每步标注改动文件 + 验证方式。工作目录 `console/server`,npm 命令在其下执行;服务用 pm2 管理(`pm2 restart console`)。

---

## Task 1: DB Schema 与存量迁移(console_auth)

> 基础地基:tenant/user_tenant 表与 session 加列必须先于代码改造,否则旧 session 无 active_tenant_id 列可读。

### Step 1.1 — Drizzle schema 加 tenant 主表
- **改动文件**:`console/server/src/db/schema.ts`
- **做什么**:`consoleAuth.table('tenant', { id: text PK, name: text notNull, status: text default 'active', quota: jsonb default '{}', description: text, create_time/create_user/update_time/update_user 审计 })`;`UNIQUE(name)`
- **验证**:`cd console/server && npm run lint`(`tsc --noEmit`)无类型错误

### Step 1.2 — Drizzle schema 加 user_tenant 关联表
- **改动文件**:`console/server/src/db/schema.ts`
- **做什么**:`consoleAuth.table('user_tenant', { id: bigserial PK, user_id FK→user.id cascade, tenant_id FK→tenant.id cascade, is_primary: boolean default false, create_time })`;`UNIQUE(user_id,tenant_id)`;`INDEX(tenant_id)`;部分唯一索引 `UNIQUE(user_id) WHERE is_primary`(drizzle 用 `uniqueIndex().on().where()`)
- **验证**:`npm run lint` 无错误

### Step 1.3 — session 加 active_tenant_id 列
- **改动文件**:`console/server/src/db/schema.ts`(session 表)
- **做什么**:session 表加 `activeTenantId: text('active_tenant_id')`(可空)
- **验证**:`npm run lint`

### Step 1.4 — 编写迁移 SQL
- **改动文件**:`console/server/src/db/migrations/0002_tenant_management.sql`(新建)
- **做什么**:`CREATE TABLE IF NOT EXISTS console_auth.tenant(...)`、`CREATE TABLE IF NOT EXISTS console_auth.user_tenant(... 含部分唯一索引)`、`ALTER TABLE console_auth.session ADD COLUMN IF NOT EXISTS active_tenant_id TEXT`;列定义与 `0001_console_auth.sql` 风格一致
- **验证**:`docker exec -i callbot-postgres psql -U postgres -d callbot < console/server/src/db/migrations/0002_tenant_management.sql` 无报错;**重跑**(幂等)无报错

### Step 1.5 — seed-tenants 脚本
- **改动文件**:`console/server/src/db/seed-tenants.ts`(新建)+ `console/server/package.json`(加 `"db:seed-tenants": "tsx src/db/seed-tenants.ts"`)
- **做什么**:(1) `SELECT DISTINCT tenant_id FROM console_auth."user"` → `INSERT INTO tenant(id,name,status) ... ON CONFLICT(id) DO NOTHING`;(2) 遍历 user → `INSERT INTO user_tenant(user_id,tenant_id=user.tenant_id,is_primary=true) ON CONFLICT(user_id,tenant_id) DO NOTHING`;(3) `auth.api.signUpEmail` 建 `platform@transvoice.local`/`platform123`,再 `UPDATE user SET role='platform_admin'` + 插 `user_tenant(default,is_primary=true)`;参考 `seed.ts` 的 signUp+update 模式
- **验证**:`set -a && source .env.local && set +a && npm run db:seed-tenants` 成功;`docker exec callbot-postgres psql -U postgres -d callbot -c 'SELECT id,name,status FROM console_auth.tenant'` 见 default/galaxy_fin;`SELECT email,role FROM console_auth."user"` 见 platform 用户 role=platform_admin

### Step 1.6 — 迁移完整性验证
- **改动文件**:无(验证步骤)
- **验证**:重跑 0002 SQL + `db:seed-tenants` 不报错(幂等);`SELECT COUNT(*) FROM console_auth.user_tenant` ≥ 用户数(每用户至少 1 主归属)

---

## Task 2: 认证/会话改造(Better Auth + session + guards)

> 隔离键地基:session/guards 改造后,业务 API 经 requirePermission 自动跟随。依赖 Task 1 列存在。

### Step 2.1 — Better Auth session 注入 activeTenantId
- **改动文件**:`console/server/src/auth.ts`
- **做什么**:betterAuth 配置加 `session: { additionalFields: { activeTenantId: { type: 'string', required: false, defaultValue: null } } }`;user additionalFields(tenantId/role)不动
- **验证**:`npm run lint`;登录后 `getSession()` 返回对象含 `activeTenantId` 字段(初始 null)

### Step 2.2 — permissions 加 platform_admin 角色 + 权限码
- **改动文件**:`console/server/src/lib/permissions.ts`
- **做什么**:`PermissionCode` 联合类型加 `'menu:tenant'|'tenant:view'|'tenant:create'|'tenant:update'|'tenant:delete'|'user:view'|'user:assign-tenant'`;`ROLE_PERMISSIONS` 加 `platform_admin: [...admin 全部权限, ...新增 tenant/*/user:*]`
- **验证**:`npm run lint`;node repl 或单测:`hasPermission('platform_admin','tenant:create')===true`、`hasPermission('admin','tenant:create')===false`

### Step 2.3 — session.ts 隔离键优先级
- **改动文件**:`console/server/src/auth/session.ts`
- **做什么**:`requireTenantId()` 改 `return session.activeTenantId ?? user.tenantId ?? 'default'`;新增导出 `requireActiveTenantId()`(同逻辑别名);`requireUserEmail()` 的 tenantId 同步改
- **验证**:`npm run lint`;登录后(未切换)调 `requireTenantId()` 返回 user.tenantId(fallback)

### Step 2.4 — guards.ts 活跃租户 + platform_admin 守卫
- **改动文件**:`console/server/src/lib/guards.ts`
- **做什么**:`ctxFromSession()` 的 tenantId 改读 `activeTenantId ?? user.tenantId ?? 'default'`;`AuthCtx.tenantId` 语义=活跃租户;新增 `requirePlatformAdmin(): Promise<AuthCtx | NextResponse>`(`requireAuth` + `role!=='platform_admin'` → 403)
- **验证**:`npm run lint`;platform_admin 登录调 `requirePlatformAdmin()` 返回 AuthCtx;普通 admin 调返回 403 NextResponse(`isDenial` true)

### Step 2.5 — 隔离链路验证
- **改动文件**:无(验证步骤)
- **验证**:`pm2 restart console`;admin@ 登录 `GET /api/prompts` 返回 default 数据;`(Step 3.5 后)` 切到 galaxy_fin 返回 galaxy_fin 数据;普通 admin 触发 `requirePlatformAdmin()` 守卫返回 403

---

## Task 3: 租户管理 API

> 依赖 Task 2 守卫就绪。租户管理 API 用 `requirePlatformAdmin()`(design.md §6);switch-tenant 用 `requireAuth`(普通用户也要切自己归属)。

### Step 3.1 — /api/tenants 列表 + 新建
- **改动文件**:`console/server/src/app/api/tenants/route.ts`(新建)
- **做什么**:GET → `requirePlatformAdmin()` → `SELECT * FROM console_auth.tenant ORDER BY name`;POST → `requirePlatformAdmin()` → 校验 id `/^[a-z0-9-]+$/` + 查重 → `INSERT`;`isUniqueViolation` → 409
- **验证**:platform_admin session `POST /api/tenants {id:'test-co',name:'测试'}` → 201;重复 id → 409;admin@ session → 403

### Step 3.2 — /api/tenants/:id 详情/改/删
- **改动文件**:`console/server/src/app/api/tenants/[id]/route.ts`(新建)
- **做什么**:GET 详情;PUT 改 name/status(可选 quota);DELETE → 先 `SELECT COUNT(*) FROM user_tenant WHERE tenant_id=id`,`>0` 则 409,否则 `DELETE FROM tenant`
- **验证**:PUT 置某 tenant status=disabled 后查询确认;DELETE 有 user_tenant 关联的 tenant → 409

### Step 3.3 — /api/users 跨租户列表
- **改动文件**:`console/server/src/app/api/users/route.ts`(新建)
- **做什么**:`requirePlatformAdmin()` → `SELECT u.id,u.email,u.name,u.role, array_agg(ut.tenant_id) tenants FROM console_auth."user" u LEFT JOIN console_auth.user_tenant ut ON ut.user_id=u.id GROUP BY u.id`
- **验证**:platform_admin `GET /api/users` 返回所有用户含 tenants 数组;admin@ → 403

### Step 3.4 — /api/users/:id/tenants 分配
- **改动文件**:`console/server/src/app/api/users/[id]/tenants/route.ts`(新建)
- **做什么**:`requirePlatformAdmin()`;body `{tenantId, action:'add'|'remove'|'setPrimary'}`;add→`INSERT ON CONFLICT DO NOTHING`;setPrimary→事务(`UPDATE user_tenant SET is_primary=false WHERE user_id`,`UPDATE ... is_primary=true WHERE user_id AND tenant_id`,`UPDATE user SET tenant_id=tenantId`);remove→`DELETE`(若是主租户,先重置 user.tenant_id 到次选或拒绝)
- **验证**:setPrimary 后 `SELECT tenant_id FROM console_auth."user" WHERE id=?` 与 `user_tenant.is_primary=true` 行一致

### Step 3.5 — /api/session/switch-tenant 切换
- **改动文件**:`console/server/src/app/api/session/switch-tenant/route.ts`(新建)
- **做什么**:`requireAuth()`;body `{tenantId}`;校验:普通用户 `EXISTS user_tenant(user_id,tenant_id)`,platform_admin 跳过归属校验;校验 `tenant.status='active'` 否则 409;`UPDATE console_auth.session SET active_tenant_id=tenantId WHERE id=<当前 session id>`(从 getSession 取 session.id)
- **验证**:platform_admin 切任意 tenant → 200;admin@ 切非归属 → 403;切 disabled tenant → 409;切换后 `GET /api/prompts` 数据随之变

### Step 3.6 — API 守卫验证
- **改动文件**:无(验证步骤)
- **验证**:admin@ `GET /api/tenants`→403、`GET /api/users`→403;`POST /api/session/switch-tenant`(自己的归属)→200;platform_admin 全部 200

---

## Task 4: 租户管理 UI

> 依赖 Task 3 API。

### Step 4.1 — ConsoleShell 菜单 + 顶栏切换器
- **改动文件**:`console/server/src/components/ConsoleShell.tsx`
- **做什么**:MENUS 加 `{key:'tenants',label:'租户管理',icon:Building2,href:'/tenants'}`;菜单渲染逻辑改为:`hasPermission(role,'menu:tenant')` 为 false 时**不渲染该项**(区别于现有"下期"灰项——平台菜单直接隐藏);header 加 `<TenantSwitcher />`
- **验证**:platform_admin 登录侧栏见「租户管理」+ 顶栏切换器;普通 admin 不见菜单项

### Step 4.2 — TenantSwitcher 组件
- **改动文件**:`console/server/src/components/TenantSwitcher.tsx`(新建)
- **做什么**:client component;平台用户 `GET /api/tenants` 取全部、普通用户取 session 归属;下拉选中当前 activeTenantId;`onChange` → `POST /api/session/switch-tenant` → `router.refresh()`;样式与 header 一致(slate/indigo、text-xs)
- **验证**:组件渲染当前活跃租户标识;切换后 header 租户标签变化 + prompts 页数据刷新

### Step 4.3 — /tenants 页 + TenantsManager
- **改动文件**:`console/server/src/app/tenants/page.tsx`(新建)+ `console/server/src/components/TenantsManager.tsx`(新建)
- **做什么**:page.tsx `requireAuth` + `<ConsoleShell>` 包裹;TenantsManager:`GET /api/tenants` 列表表格(id/name/status/quota 摘要)+ 新建/编辑表单(POST/PUT)+ 停用按钮;参考 `InboundRoutesManager.tsx` 的 CRUD + flash toast 模式
- **验证**:platform_admin 访问 `/tenants` 见列表;新建租户后列表刷新;普通 admin 访问(API 403)显示错误态

### Step 4.4 — 用户分配子面板
- **改动文件**:`console/server/src/components/TenantsManager.tsx`(同文件扩展)
- **做什么**:每租户行展开 → 列出该 tenant 归属用户 + 全部用户勾选;勾选/取消调 `POST /api/users/:id/tenants {action}`;设主租户 radio;操作后刷新
- **验证**:给用户分配新租户 → 该用户登录后切换器多一项;设主租户后该用户 `user.tenant_id` 变

### Step 4.5 — UI 全流程验证
- **改动文件**:无(验证步骤)
- **验证**:platform_admin 全流程:建租户 → 分配用户 → 该用户登录切换 → prompts 数据隔离正确;普通 admin 无「租户管理」入口

---

## Task 5: 收尾验证

> 全链路与回归。

### Step 5.1 — 多租户隔离端到端
- **改动文件**:无
- **验证**:platform_admin 切 default→galaxy_fin;`GET /api/prompts` 返回 galaxy_fin 提示词;`GET /api/inbound-routes` 同理;切回 default 数据还原

### Step 5.2 — 存量兼容回归
- **改动文件**:无
- **验证**:清浏览器 cookie 重新登录 admin@(active_tenant_id 为空)→ fallback user.tenant_id=default → `GET /api/prompts` 返回 default 数据,功能不中断

### Step 5.3 — OpenSpec 校验
- **改动文件**:无
- **验证**:`openspec validate add-tenant-management --strict` 通过

### Step 5.4 — 索引与测试
- **改动文件**:`console/server/tests/lib/`(新增 session/guards 单测)
- **做什么**:`codegraph sync`(console 目录首次纳入索引);CRG `build_or_update_graph`;为 `requireTenantId` 优先级 + `requirePlatformAdmin` 补 vitest 单测(参考 `tests/lib/prompt-template.test.ts` 风格)
- **验证**:`cd console/server && npm test` 通过;`codegraph_status` 健康
