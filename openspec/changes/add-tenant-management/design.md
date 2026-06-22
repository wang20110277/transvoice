# Design: Console 租户管理 — tenant 主表 + 多租户归属 + 平台管理员

> 本文件记录 spec 阶段确认的技术方案。proposal.md 的 4 项决策(平台管理员角色 / 1 用户多租户 / quota 只存不校验 / 仅 console 单侧)为本设计输入。

## 1. 核心决策(5 条)

| # | 决策 | 取舍 |
|---|------|------|
| 1 | 新增 `tenant` 主表 + `user_tenant` 关联表,均放 `console_auth` schema | 租户成一等公民,有元数据;归认证/管理域,agent-flow 不引用 |
| 2 | 1 用户多租户:`user_tenant` 关联 + `session.active_tenant_id` 会话级活跃租户 | 会话级隔离,多标签/多设备互不干扰;优于 cookie(防篡改)与 user 级(并发冲突) |
| 3 | 新增 `platform_admin` 角色跨租户管理;普通 admin 仅本租户 | 平台运维 vs 租户运营分离,最小权限 |
| 4 | `quota` JSON 字段本期只存不校验 | 预留扩展点,不膨胀本期范围 |
| 5 | 隔离键 `session.user.tenantId` → `session.activeTenantId`;改造集中在 session.ts/guards.ts | 影响面收敛,prompts/inbound-routes API 经 guards 自动跟随 |

## 2. 数据模型(`console_auth` schema,Drizzle)

### 2.1 `tenant`(新增,主表)

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | TEXT PK | 租户标识(kebab-case,如 `default`/`galaxy_fin`),值与现有业务表 `tenant_id` 字符串一致 |
| `name` | TEXT NOT NULL | 显示名(星河金融) |
| `status` | TEXT NOT NULL DEFAULT `'active'` | `active`/`disabled`;disabled 租户的用户登录/切换时拒绝 |
| `quota` | JSONB NOT NULL DEFAULT `'{}'` | 配额 `{max_users,max_prompts,max_routes}`(本期只存) |
| `description` | TEXT | |
| `create_time`/`create_user`/`update_time`/`update_user` | TIMESTAMPTZ/TEXT | 审计 |

- `UNIQUE(name)`;`id` 即业务表 `tenant_id` 的值
- **不对业务表建 FK**(agent-flow 侧 `tenant_id` 仍是自由字符串,本期不强约束)

### 2.2 `user_tenant`(新增,多归属关联)

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | BIGSERIAL PK | |
| `user_id` | TEXT NOT NULL REFERENCES `console_auth.user(id)` ON DELETE CASCADE | |
| `tenant_id` | TEXT NOT NULL REFERENCES `console_auth.tenant(id)` ON DELETE CASCADE | |
| `is_primary` | BOOLEAN NOT NULL DEFAULT FALSE | 主租户(登录默认 activeTenantId 来源) |
| `create_time` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

- `UNIQUE(user_id, tenant_id)` 防重复归属
- `INDEX(tenant_id)` 便于"租户下有哪些用户"查询
- 部分唯一索引 `UNIQUE(user_id) WHERE is_primary` 保证一用户仅一主租户
- 不设租户内 `role` 字段(本期平台级 platform_admin/admin 已够,租户内细分下期)

### 2.3 `console_auth.user` / `session` 改造

- **`user.tenant_id` 保留**,语义降级为"主租户缓存"(= 该用户 `user_tenant.is_primary=true` 的 tenant_id);Better Auth `additionalFields.tenantId` 不动,向后兼容,fallback 兜底
- **`session` 增 `active_tenant_id` 列**(TEXT,可空):会话级活跃租户;空时 fallback 到 `user.tenant_id`

## 3. 存量迁移(幂等,可重复执行)

新增 `src/db/migrations/0002_tenant_management.sql` + `src/db/seed-tenants.ts`:

1. 建 `tenant` / `user_tenant` 表(§2.1/2.2);`session` 加 `active_tenant_id` 列(`ADD COLUMN IF NOT EXISTS`)
2. **回填 tenant 记录**:`SELECT DISTINCT tenant_id FROM console_auth."user"` 得现有值(`default`/`galaxy_fin`),逐个 `INSERT INTO tenant(id,name,status) VALUES (v,v,'active') ON CONFLICT (id) DO NOTHING`
3. **建立 user_tenant 关联**:遍历 `console_auth."user"`,每用户 `INSERT INTO user_tenant(user_id,tenant_id,is_primary) VALUES (u.id,u.tenant_id,TRUE) ON CONFLICT (user_id,tenant_id) DO NOTHING`
4. seed 一个 `platform_admin` 账号(`platform@transvoice.local` / `platform123`,`role=platform_admin`,归属 `default` 租户 is_primary)
5. 验证:重新跑脚本不报错(幂等);`SELECT` 确认 tenant/user_tenant 行数符合预期

## 4. 会话活跃租户机制

**存储**:better-auth `session.active_tenant_id`(会话级,每浏览器会话独立)。

**取值优先级**(`session.ts`):
```
activeTenantId = session.active_tenant_id ?? user.tenant_id(主) ?? 'default'
```

**切换** `POST /api/session/switch-tenant { tenantId }`:
- 权限校验:目标 tenant 必须在用户可访问集合内
  - 普通用户:`EXISTS user_tenant(user_id, tenant_id)` 必须命中
  - `platform_admin`:可切任意 tenant(平台级,不限于 user_tenant 归属)
- 状态校验:目标 tenant `status='active'`(disabled 拒绝,返回 409)
- 写入:`UPDATE session SET active_tenant_id=? WHERE token=?`

**登录默认**:新建 session 时 `active_tenant_id` 留空 → 首次请求 fallback 到 `user.tenant_id`(主租户)。无需登录时额外查询。

## 5. platform_admin 角色与权限码扩展

`permissions.ts`:

- 新增角色 `platform_admin` = admin 全部权限 + 跨租户管理权限
- 新增 `PermissionCode`:`menu:tenant` / `tenant:view` / `tenant:create` / `tenant:update` / `tenant:delete` / `user:view` / `user:assign-tenant`
- 「租户管理」菜单 `enabled` 由 `hasPermission(role,'menu:tenant')` 决定(仅 platform_admin 可见,非平台用户直接隐藏,区别于"下期"灰色项)

## 6. 隔离键改造影响面(精确到文件)

| 文件 | 改动 |
|------|------|
| `src/auth/session.ts` | `requireTenantId()` 改按 §4 优先级读 activeTenantId;导出 `requireActiveTenantId()` 作显式别名 |
| `src/lib/guards.ts` | `ctxFromSession()` 的 `tenantId` 改为 activeTenantId;`AuthCtx.tenantId` 语义=活跃租户(非 user.tenant_id) |
| `src/lib/permissions.ts` | 加 `platform_admin` 角色 + `tenant:*`/`user:*` 权限码 |
| `src/auth.ts` | Better Auth `session.additionalFields` 注入 `activeTenantId`(会话级字段) |

**业务 API 自动跟随**:`/api/prompts/*`、`/api/inbound-routes/*` 经 `requirePermission()` 取 `auth.tenantId`,guards 改造后隔离键天然切到活跃租户,**无需逐个改业务 route**。新增 `requirePlatformAdmin()` 守卫用于 `/api/tenants`、`/api/users`。

## 7. 租户管理 API(platform_admin 守卫)

| Method | Path | 权限码 | 行为 |
|--------|------|--------|------|
| GET | `/api/tenants` | `tenant:view` | 全租户列表(跨租户) |
| POST | `/api/tenants` | `tenant:create` | 新建租户(id kebab-case 唯一) |
| GET/PUT/DELETE | `/api/tenants/:id` | `tenant:*` | 详情/改名/停用/删除(删除前校验无 user_tenant 关联) |
| GET | `/api/users` | `user:view` | 全用户列表(跨租户) |
| POST | `/api/users/:id/tenants` | `user:assign-tenant` | 分配/取消租户、设主租户(同步更新 user.tenant_id 缓存) |
| POST | `/api/session/switch-tenant` | (已登录) | 切换活跃租户(§4) |

普通 admin 调 `/api/tenants` / `/api/users` → 403。

## 8. UI(ConsoleShell + 新页面)

- **菜单**:`MENUS` 加 `{ key:'tenants', label:'租户管理', icon:Building2, href:'/tenants' }`,enabled 按 `menu:tenant` 权限;非 platform_admin 不渲染该项
- **顶栏切换器**:`ConsoleShell` header 加 `<TenantSwitcher>`,下拉显示用户可切换租户(platform_admin 显示全部,普通用户显示其 user_tenant);切换调 `/api/session/switch-tenant` 后 `router.refresh()`
- **/tenants 页**:租户列表(名称/状态/quota 摘要)+ 新建/编辑/停用;每租户展开用户分配(勾选归属、设主租户)
- **platform_admin 跨租户视角**:切到任意 tenant 后,prompts/inbound-routes 页经 activeTenantId 显示该租户数据

## 9. 边界与风险

- **破坏性**:session 隔离键语义变化(user.tenant_id → activeTenantId)。迁移脚本须先于代码部署跑(保证 active_tenant_id 列存在);否则旧 session 取值 fallback 到 user.tenant_id,功能不中断
- **agent-flow 零改动**:继续把 tenant_id 当字符串;本期不校验存在性(若 Console 删了某 tenant,agent-flow 仍能按字符串加载该租户历史提示词 —— 已记为后续风险)
- **删除租户约束**:有 user_tenant 关联时拒绝删除(先解除归属);业务表(prompt_config/inbound_route)数据保留(历史数据,不级联删)
