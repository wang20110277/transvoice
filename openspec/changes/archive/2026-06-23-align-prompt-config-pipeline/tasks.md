# Tasks: 提示词配置管理 → 呼入提示词使用链路对齐

> 按执行依赖排序(先依赖,后依赖方)。每个任务可独立验证。

## 1. DB Schema 重构(agent-flow SQLAlchemy + alembic)

- [ ] 1.1 重构 `PromptConfig` 模型:`biz_system` 列重命名为 `tenant_id`,新增 `scenario` 列;`UNIQUE(tenant_id, biz_type, scenario)`;新增 `INDEX(tenant_id, biz_type)`
- [ ] 1.2 新增 `PromptVersion` 模型(版本快照表:`tenant_id`/`biz_type`/`scenario`/`system_prompt`/`version`/`snapshot JSONB`/审计)
- [ ] 1.3 新增 `InboundRoute` 模型(DID 路由表:`did`/`did_pattern`/`tenant_id`/`biz_type`/`scenario`/`is_active`;`UNIQUE(did)`)
- [ ] 1.4 新增 `CallTask` 模型(外呼任务定义层:`tenant_id`/`name`/`prompt_id` FK/`kb_ids`/`status`/`concurrent_limit`/`allowed_hours`/`redial_strategy`/`dept_id`/审计)
- [ ] 1.5 `CallSession` 增 `tenant_id`/`scenario` 列(可空,向后过渡)
- [ ] 1.6 编写 alembic migration:drop 旧 `uq_prompt_config_system_type` + `biz_system` 列,rename + add cols,create 3 张新表,迁移现有 seed 数据(为现有 3 条提示词补 `tenant_id='default'`/`scenario='default'`)
- [ ] 1.7 验证:`alembic upgrade head` 成功,表结构与 Drizzle 侧一致

## 2. agent-flow 提示词加载 / 渲染 / 缓存(消费端)

- [ ] 2.1 `prompt_config.py`:`get_system_prompt(tenant_id, biz_type, scenario)` 三元组签名;Redis key 改 `cb:prompt:{tenant_id}:{biz_type}:{scenario}`;DB 查询条件含三元组 + `is_active`
- [ ] 2.2 `invalidate_prompt_cache(tenant_id, biz_type, scenario)`:DEL 对应 key
- [ ] 2.3 新增 `src/graph/render.py`:`render(template, vars_context)`,基于 `extra.variables` 声明替换;缺失→保留占位符 + WARNING
- [ ] 2.4 `prompt.py` `build_messages` 前接入渲染:接收 `vars_context`,先 `render` 再组装

## 3. agent-flow 呼入维度全链路透传

- [ ] 3.1 `registry.py` `ActiveCall` 增 `tenant_id`/`scenario` 字段;`register()` 签名增三元组
- [ ] 3.2 `main.py` CHANNEL_ANSWER:读 `variable_did`,查 `inbound_route` 解析 `(tenant_id, biz_type, scenario)`;预留 dialplan 静态 channel var 回退
- [ ] 3.3 `handler.py`:`handle()` / WS 路径透传四元组到 `CallGraphState`
- [ ] 3.4 `flow.py` `run_streaming_pipeline`:从 `state` 取三元组调 `get_system_prompt`,聚合 `vars_context`(MCP 身份 ‖ 记忆 ‖ call_task.vars)后渲染
- [ ] 3.5 验证:呼入日志可见 `tenant_id`/`biz_type`/`scenario` 透传,不再出现 `biz_system='default'` 硬编码

## 4. Console 后端骨架(Next.js 15 + Drizzle + Better Auth)

- [ ] 4.1 初始化 Next.js 15 App Router 项目(于 `console/server` 或新目录,与现有前端共存决策)
- [ ] 4.2 接入 Drizzle ORM,配置 PostgreSQL 连接(复用 `callbot` schema)
- [ ] 4.3 Drizzle schema:4 张表(`prompt_config`/`prompt_version`/`inbound_route`/`call_task`),列名与 SQLAlchemy 完全一致
- [ ] 4.4 Better Auth 配置:`socialProviders.adfs`(OAuth)+ `credentialProvider`(email/password);session 注入 `user.tenantId`
- [ ] 4.5 RBAC 中间件:守护 `prompt:*`/`calltask:*`/`route:*` 权限码
- [ ] 4.6 验证:ADFS 登录与本地账密登录均可建立 session,`tenantId` 注入生效

## 5. Console API(Route Handlers)

- [ ] 5.1 `/api/prompts`:GET 列表/POST 新建;`/:id` PUT 编辑(版本自增 + 写 `prompt_version` 快照)
- [ ] 5.2 `/api/prompts/:id/clone` 克隆;`/api/prompts/:id/rollback` 回滚(从快照恢复 + 新版本 + 清缓存)
- [ ] 5.3 `/api/prompts/:id/publish`:置 active + 同库互斥 + 写快照 + **DEL Redis**(零延迟生效)
- [ ] 5.4 `/api/prompts/:id/test` 联调:渲染 + 调 LLM 返回样例回复;`/:id/versions` 历史列表
- [ ] 5.5 `/api/inbound-routes`:CRUD(DID/号段运营),按 `tenant_id` 隔离
- [ ] 5.6 `/api/call-tasks`:定义层 CRUD,`prompt_id` 校验同租户
- [ ] 5.7 验证:发布后 agent-flow 侧 Redis key 被清,呼入取到新版本(端到端)

## 6. 收尾验证

- [ ] 6.1 `openspec validate align-prompt-config-pipeline --strict` 通过
- [ ] 6.2 多租户隔离验证:A 租户配置不影响 B 租户呼入
- [ ] 6.3 变量渲染验证:正常替换 + 缺失占位保留 + WARNING
- [ ] 6.4 `codegraph sync` + CRG 索引更新;关键路径(呼入提示词加载)测试覆盖
