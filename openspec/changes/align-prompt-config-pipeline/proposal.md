# Proposal: 提示词配置管理 → 呼入提示词使用链路对齐

## 背景

当前两端各自实现、中间断开:

- **Console/server(管理端)**:仅 React 19 + Vite 前端(`mockDb.ts` 全量 mock),**无后端**。`PromptManager` 已支持新建/保存/克隆/回滚/联调,但数据是内存假数据。
- **agent-flow(呼入消费端)**:`prompt_config` 表按 `(biz_system, biz_type)` 加载,Redis(`cb:prompt:{biz_system}:{biz_type}`,5min TTL)→ DB 两级降级;`flow.run_streaming_pipeline()` 呼入时取用。

链路存在 6 个断点(详见"待解决问题")。本期将 Console 提示词+外呼任务后端落地,并与 agent-flow 呼入链路彻底对齐。

## 需求

1. 搭建 Console 管理后端:Next.js 15 + Drizzle ORM + Better Auth(ADFS + 本地账密兜底),支撑提示词配置管理与外呼任务管理。
2. 建立**两端统一的规范维度模型**,把提示词维度从路由级扩到场景级 `(tenant_id, biz_type, scenario)`,本期同时落地多租户隔离。
3. 打通"配置管理 → 呼入使用"完整链路:变量渲染、版本历史/回滚持久化、跨服务缓存失效。

## 规范维度模型(两端单一事实源)

项目初期,可自由增删字段。**优先设计一致性**:Console(Drizzle)与 agent-flow(SQLAlchemy)共用同一物理表、相同列名、相同维度,杜绝双词汇表。

| 规范列 | 含义 | 来源/对应 |
|--------|------|-----------|
| `tenant_id` | 租户/业务系统(星河金融/智灵医疗) | ← agent-flow 现有 `biz_system`(**重命名**) |
| `biz_type` | 路由业务场景(客服/催收/营销,驱动 LLM 节点分支如征信查询) | ← Console `deptId` |
| `scenario` | 话术场景(温和催收/高意向激活,提示词级选择器) | ← Console `category` |

- 唯一键 = `(tenant_id, biz_type, scenario)`,每个组合一条 `is_active=true` 的发布版本。
- Redis key 统一为 `cb:prompt:{tenant_id}:{biz_type}:{scenario}`。
- agent-flow 现有 `biz_system` 列重命名为 `tenant_id`,新增 `scenario` 列(初期重设计,无兼容负担)。

## 范围

### 包含

- **Console 后端**:提示词 CRUD/克隆/发布/回滚/联调测试 API;外呼任务(CallTask)管理 API(promptId 引用提示词)。
- **认证**:Better Auth + ADFS(OAuth)+ 本地账密(email/password)兜底;RBAC 守护 `prompt:*` 与 `calltask:*` 权限。
- **统一 schema(Drizzle + SQLAlchemy 同步)**:`prompt_config`(主表,新维度 `tenant_id`/`biz_type`/`scenario` + 租户隔离)、`prompt_version`(版本历史快照表)。
- **agent-flow 链路对齐**:全链路透传 `tenant_id` + `scenario`(dialplan → ESL handler → ActiveCallRegistry → CallGraphState → flow → prompt_config 查询);新增变量渲染步骤;缓存失效契约。

### 不包含

- Console 其他模块后端(知识库 / 记忆系统 / 通话记录后端)——仅保留现有前端。
- agent-flow 的 MCP / RAG / TTS / ASR / barge-in 等既有逻辑——不改动。

## 关键决策(已与用户确认)

| 决策点 | 选择 | 影响 |
|--------|------|------|
| 范围边界 | 提示词 + 外呼任务 | CallTask.promptId 引用提示词 |
| 维度对齐 | 扩展到场景级 `(tenant_id, biz_type, scenario)` | 全链路透传 scenario;主键/索引重设计 |
| 多租户 | 本期落地 `tenant_id ↔ biz_system` 隔离 | 全链路透传 tenantId,Redis key/查询按租户隔离 |
| 认证第二 provider | ADFS + 本地账密 | Better Auth 同时配 OAuth + credential |
| Schema 自由度 | **初期可自由增删,优先一致性** | 两端共用同一规范模型,重命名 `biz_system→tenant_id`,杜绝双词汇表 |

## 待解决问题(6 个断点)

1. **biz_system 呼入侧未透传** — `flow.py:366` 仅传 `biz_type`,biz_system 恒为 `default`。
2. **主键维度错位** — Console `(tenantId, deptId, category)` vs agent-flow `(biz_system, biz_type)`;UNIQUE 只允许每场景一条 → 改为统一 `(tenant_id, biz_type, scenario)`。
3. **`{变量}` 占位符无渲染** — Console content 含 `{customer_name}` 等,`get_system_prompt` 返回原文,`build_messages` 无替换。
4. **版本历史/回滚表结构不足** — DB 仅 `version(int)` + UNIQUE;回滚靠应用层 `history[]`,跨服务不可持久化 → 新增 `prompt_version` 快照表。
5. **跨服务缓存失效无契约** — `invalidate_prompt_cache` 存在但无人调用 → 发布即清缓存。
6. **多租户隔离粒度** — prompt_config 无 tenant 列 → `tenant_id` 全链路隔离。

## 约束

- **技术栈**:Console 后端 Next.js 15(App Router / Route Handlers)+ Drizzle ORM + Better Auth。
- **数据库**:复用现有 PostgreSQL 17(`callbot` schema);Drizzle 与 SQLAlchemy **操作同一张物理表、相同列名**。
- **缓存**:复用现有 Redis;Console 与 agent-flow 连同一 Redis 实例。
- **认证**:Better Auth dual provider(ADFS OAuth + 本地账密),守护管理面;呼入路径(FreeSWITCH ESL 触发)不经 Console 鉴权。
- **设计一致性优先**:不为旧表兼容牺牲一致性;不为假设的未来需求加抽象。

## 成功标准

1. 管理员通过 Console(ADFS 或本地账密登录)创建/编辑/克隆/发布/回滚提示词,数据落 PostgreSQL。
2. 发布生效后,新呼入通话按 `(tenant_id, biz_type, scenario)` 命中对应提示词,**零缓存延迟**(发布即失效旧缓存)。
3. 提示词内 `{变量}` 在呼入时被正确渲染(变量来自外呼任务导入 + MCP 身份查询)。
4. 多租户:A 租户配置不影响 B 租户呼入。
5. 外呼任务(CallTask)可绑定 promptId,呼出/呼入均能取到对应提示词。
6. 版本回滚:从 `prompt_version` 快照恢复,跨重启有效。
7. Console(Drizzle)与 agent-flow(SQLAlchemy)提示词模型列名/维度完全一致,无双词汇表。

## 下一步

`/openflow spec` 生成完整规格(含 Drizzle/SQLAlchemy 双端 schema、API 契约、agent-flow 全链路透传方案、缓存失效与变量渲染细节),或 `/openflow brainstorming` 先做深度设计探索。
