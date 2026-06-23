# Spec: 提示词配置管理(Console 管理端)

> 能力:管理员通过 Console 对提示词模板进行全生命周期管理(创建/编辑/克隆/发布/回滚/联调),数据落 PostgreSQL,由 Better Auth(ADFS + 本地账密)守护。

## ADDED Requirements

### Requirement: 提示词维度模型统一

系统 SHALL 以 `(tenant_id, biz_type, scenario)` 三元组作为提示词的唯一业务键,Console(Drizzle)与 agent-flow(SQLAlchemy)操作同一张物理表 `callbot.prompt_config` 且列名完全一致。`biz_system` 列 MUST 重命名为 `tenant_id`,并新增 `scenario` 列。

#### Scenario: 唯一键约束
- **WHEN** 管理员为已存在的 `(tenant_id, biz_type, scenario)` 组合再创建一条 active 记录
- **THEN** 系统 SHALL 拒绝并返回唯一约束冲突错误

#### Scenario: 双端模型一致
- **WHEN** 任一端读取/写入提示词
- **THEN** Drizzle 与 SQLAlchemy SHALL 使用完全相同的列名与维度,不存在 `biz_system`/`tenantId` 双词汇表

### Requirement: 提示词模板生命周期管理

系统 SHALL 支持提示词的创建、编辑、克隆操作。每个提示词 MUST 携带 `title`、`content`(含 `{变量}` 占位符)、`category`、`variables[]`(声明的变量元数据,存 `extra`)、`version`、`deptId`(映射 biz_type)。编辑保存时 `version` MUST 自增并生成一条 `prompt_version` 版本快照。

#### Scenario: 编辑保存触发版本快照
- **WHEN** 管理员编辑已存在提示词的 content 并保存
- **THEN** 系统 SHALL 将 `version` 自增,在 `prompt_version` 表写入该版本的完整快照(标题/category/variables/content),并更新 `prompt_config` 主表

#### Scenario: 克隆提示词
- **WHEN** 管理员克隆一个提示词
- **THEN** 系统 SHALL 以 `version=1` 创建新记录,继承被克隆模板的 content/variables/category,归属同一 `tenant_id`

### Requirement: 发布与生效

系统 SHALL 区分"草稿"与"发布"态。`publish` 动作 MUST 将目标版本置为 `is_active=true`(同 `(tenant_id, biz_type, scenario)` 下其余版本置 `is_active=false`),并在写入后**同步失效** agent-flow 侧 Redis 缓存 `cb:prompt:{tenant_id}:{biz_type}:{scenario}`,使配置零延迟生效。

#### Scenario: 发布清缓存零延迟
- **WHEN** 管理员发布某提示词
- **THEN** 系统 SHALL 写主表 + 版本快照,并 `DEL` 对应 Redis key
- **AND** 下一次呼入命中该键时 MUST 直接从 DB 取到新内容,而非旧缓存

#### Scenario: 同键唯一 active
- **WHEN** 发布后同 `(tenant_id, biz_type, scenario)` 存在历史 active 版本
- **THEN** 系统 SHALL 将历史版本置 `is_active=false`,保证同键仅一条 active

### Requirement: 版本回滚

系统 SHALL 支持从 `prompt_version` 历史快照回滚。回滚 MUST 将主表 content/version 恢复为指定历史快照,并以新版本号写入(不覆盖历史),回滚动作跨重启有效。

#### Scenario: 回滚历史版本
- **WHEN** 管理员选择某历史版本执行回滚
- **THEN** 系统 SHALL 以新 `version` 把该快照内容写回 `prompt_config`,并在 `prompt_version` 记录回滚快照
- **AND** 回滚后触发与 publish 相同的缓存失效

### Requirement: 提示词联调测试

系统 SHALL 提供联调测试能力:管理员填入示例变量值,系统渲染模板并调用 LLM 返回样例回复,用于离线验证提示词效果。联调 MUST 在管理员所属 `tenant_id` 下执行,不得跨租户。

#### Scenario: 联调渲染验证
- **WHEN** 管理员在联调面板填入 `{customer_name}=测试客户` 并提交
- **THEN** 系统 SHALL 渲染模板后调用 LLM,返回 AI 回复文本供管理员评估

### Requirement: 多租户隔离(管理端)

系统 SHALL 按 `tenant_id` 隔离提示词。管理员只能查询/操作其所属租户的提示词;跨租户访问 MUST 被拒绝。

#### Scenario: 跨租户访问拒绝
- **WHEN** A 租户管理员请求 B 租租户的提示词
- **THEN** 系统 SHALL 返回 403/404,不泄露 B 租户任何提示词内容

### Requirement: 认证与 RBAC

系统 SHALL 使用 Better Auth 提供认证,支持 ADFS(OAuth)与本地账密(email/password)双 provider。所有提示词管理 API MUST 经认证;写操作(create/update/delete/publish/rollback)与联调(test)MUST 受 RBAC 权限码(`prompt:create`/`prompt:update`/`prompt:delete`/`prompt:test`)守护。审计列 `create_user`/`update_user` MUST 取自认证会话用户。

#### Scenario: 未认证拒绝
- **WHEN** 未登录用户调用任意提示词 API
- **THEN** 系统 SHALL 返回 401

#### Scenario: 权限不足拒绝
- **WHEN** 仅有 `prompt:view` 权限的用户调用 publish
- **THEN** 系统 SHALL 返回 403
