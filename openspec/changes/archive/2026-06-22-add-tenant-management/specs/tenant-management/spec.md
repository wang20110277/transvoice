# Spec: 租户管理(Console 管理端)

> 能力:平台管理员通过 Console 对租户全生命周期管理(新建/改名/停用/删除)、给用户分配多个租户并设主租户;用户登录后可在归属租户间切换活跃租户。tenant 从字符串升级为有元数据的一等公民,放 `console_auth` schema,agent-flow 侧不动。

## ADDED Requirements

### Requirement: 租户主表与元数据

系统 SHALL 在 `console_auth` schema 新增 `tenant` 主表,以 `id`(kebab-case) 为主键且与现有业务表 `tenant_id` 字符串值一致,携带 `name`/`status`(active|disabled)/`quota`(JSONB)/`description` 及审计列。`quota` 本期只存储不强制校验。

#### Scenario: 存量租户自动建记录
- **WHEN** 迁移脚本执行
- **THEN** 系统 SHALL 从 `console_auth.user.tenant_id` 去重得现有值,逐个 INSERT 进 tenant 表
- **AND** 脚本 MUST 幂等,重复执行不报错(`ON CONFLICT DO NOTHING`)

#### Scenario: 租户停用阻断
- **WHEN** 管理员将某 tenant 置为 `disabled`
- **THEN** 该租户的用户登录或切换至该租户时 SHALL 被拒绝

### Requirement: 用户多租户归属

系统 SHALL 新增 `user_tenant` 关联表(user_id, tenant_id, is_primary),支持一个用户归属多个租户。`UNIQUE(user_id, tenant_id)` 防重复;部分唯一索引保证每用户仅一条 `is_primary=true`。`console_auth.user.tenant_id` 保留作主租户缓存(= is_primary 的 tenant_id),向后兼容。

#### Scenario: 存量归属回填
- **WHEN** 迁移脚本执行
- **THEN** 系统 SHALL 为每个现有用户建立 `(user_id, tenant_id=user.tenant_id, is_primary=true)` 关联
- **AND** 后续分配新租户时 MUST 插入新 user_tenant 行,不动 user.tenant_id(除非设为主)

#### Scenario: 主租户唯一
- **WHEN** 平台管理员将用户的某归属设为主租户
- **THEN** 系统 SHALL 将该用户其他 user_tenant 行置 `is_primary=false`,保证仅一主

### Requirement: 会话活跃租户与切换

系统 SHALL 在 `console_auth.session` 增加 `active_tenant_id`(可空) 作为会话级活跃租户。隔离键取值优先级:`session.active_tenant_id ?? user.tenant_id ?? 'default'`。`POST /api/session/switch-tenant` SHALL 校验目标租户可访问性后更新当前 session 的 active_tenant_id。

#### Scenario: 登录默认活跃租户
- **WHEN** 用户登录,session 为新建(active_tenant_id 为空)
- **THEN** 首次业务请求 SHALL fallback 到 user.tenant_id(主租户)
- **AND** 不要求登录时额外查询

#### Scenario: 切换租户权限校验
- **WHEN** 普通用户请求切换到非其 user_tenant 归属的租户
- **THEN** 系统 SHALL 拒绝(403)
- **WHEN** platform_admin 请求切换到任意租户
- **THEN** 系统 SHALL 允许(平台级,不限于 user_tenant 归属)

#### Scenario: 切换到已停用租户拒绝
- **WHEN** 用户切换至 status=disabled 的租户
- **THEN** 系统 SHALL 返回 409 并保持原活跃租户不变

### Requirement: 平台管理员角色

系统 SHALL 新增 `platform_admin` 角色,继承 admin 全部权限并增加跨租户管理权限(`tenant:*`/`user:*`/`menu:tenant`)。「租户管理」菜单仅 platform_admin 可见;普通 admin 调租户管理 API SHALL 返回 403。

#### Scenario: 普通管理员无法管理租户
- **WHEN** role=admin 的用户调用 POST /api/tenants
- **THEN** 系统 SHALL 返回 403

#### Scenario: 平台管理员菜单可见
- **WHEN** platform_admin 登录
- **THEN** 侧边栏 SHALL 显示「租户管理」菜单项
- **WHEN** 普通 admin 登录
- **THEN** 侧边栏 SHALL 不渲染「租户管理」项(隐藏,非"下期"灰色)

### Requirement: 租户 CRUD

系统 SHALL 提供 `/api/tenants` GET/POST 与 `/api/tenants/:id` GET/PUT/DELETE,均受 platform_admin 守卫。新建租户 id MUST kebab-case 且唯一。删除租户前 MUST 校验无 user_tenant 关联(有则拒绝)。

#### Scenario: 删除有关联用户的租户
- **WHEN** platform_admin 删除仍有 user_tenant 关联的租户
- **THEN** 系统 SHALL 拒绝删除(409),提示先解除用户归属

#### Scenario: 业务表数据保留
- **WHEN** 租户被删除
- **THEN** 该 tenant_id 的 prompt_config/inbound_route 历史数据 SHALL 保留(不级联删),仅 tenant 主表记录移除

### Requirement: 用户租户分配

系统 SHALL 提供 `/api/users` GET(跨租户列表) 与 `/api/users/:id/tenants` POST(分配/取消租户、设主)。设主租户时 MUST 同步更新 user.tenant_id 缓存。

#### Scenario: 分配租户同步缓存
- **WHEN** platform_admin 将用户 A 的主租户改为 galaxy_fin
- **THEN** 系统 SHALL 更新 user_tenant.is_primary + user.tenant_id='galaxy_fin'
- **AND** A 下次新会话默认活跃租户为 galaxy_fin

### Requirement: 业务 API 按活跃租户隔离

系统 SHALL 将 prompts/inbound-routes 等业务 API 的隔离键从 session.user.tenantId 改为活跃租户(activeTenantId)。改造集中在 session.ts/guards.ts,业务 route 经 requirePermission 自动跟随。

#### Scenario: 切换租户后业务数据隔离
- **WHEN** platform_admin 将活跃租户从 default 切到 galaxy_fin,再请求 GET /api/prompts
- **THEN** 系统 SHALL 返回 galaxy_fin 的提示词列表,而非 default 的

#### Scenario: 隔离键改造向后兼容
- **WHEN** 旧 session(active_tenant_id 为空)发起请求
- **THEN** 系统 SHALL fallback 到 user.tenant_id,功能不中断
