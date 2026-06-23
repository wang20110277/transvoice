# Design: 提示词配置管理 → 呼入提示词使用链路对齐

> 本文件记录 brainstorming 阶段确认的设计方向。规格细节(Drizzle/SQLAlchemy 完整 DDL、API 契约签名、agent-flow 改动点)由 `/openflow spec` 展开。

## 1. 核心决策(5 条,均经用户确认)

| # | 决策 | 取舍 |
|---|------|------|
| 1 | 维度模型 `(tenant_id, biz_type, scenario)` 两端单一事实源;`biz_system→tenant_id` 重命名 | 一致性优先,项目初期重设计无兼容负担 |
| 2 | 外呼范围:仅 CallTask **定义层**(模型 + Console API + promptId 绑定);originate/调度/重拨拆到下个变更 | agent-flow 现无 originate 能力,避免膨胀成造拨号器 |
| 3 | 呼入解析:DID 路由表 `inbound_route`(DID/号段 → tenant_id + biz_type + scenario),agent-flow 查表 | Console 可运营,多租户/多场景可扩展 |
| 4 | 变量渲染:统一上下文(MCP 身份 ‖ 记忆 ‖ CallTask.vars)+ `extra.variables` 声明 + 缺失→占位 + 告警 | 呼入呼出统一,缺失可观测不崩 |
| 5 | 认证:Better Auth(ADFS OAuth + 本地账密兜底),守护管理面;呼入(ESL 触发)不经 Console 鉴权 | 双 provider,企业内网主 + 兜底 |

## 2. 规范维度模型(两端单一事实源)

Console(Drizzle)与 agent-flow(SQLAlchemy)操作**同一张物理表、相同列名**,杜绝双词汇表。

| 规范列 | 含义 | 来源/对应 |
|--------|------|-----------|
| `tenant_id` | 租户/业务系统(星河金融/智灵医疗) | ← agent-flow 现 `biz_system`(**重命名**) |
| `biz_type` | 路由业务场景(customer_service/collection/marketing),驱动 LLM 节点分支(如征信查询) | ← Console `deptId` |
| `scenario` | 话术场景(温和催收/高意向激活),提示词级选择器 | ← Console `category` |

- 唯一键 = `(tenant_id, biz_type, scenario)`,每个组合一条 `is_active=true` 发布版本。
- Redis key 统一为 `cb:prompt:{tenant_id}:{biz_type}:{scenario}`。

## 3. Schema 设计(PostgreSQL `callbot`,Drizzle + SQLAlchemy 同表同列名)

### 3.1 `prompt_config`(重构)

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | BIGSERIAL PK | |
| `tenant_id` | TEXT NOT NULL | 租户(原 `biz_system` 重命名) |
| `biz_type` | TEXT NOT NULL | 业务场景 |
| `scenario` | TEXT NOT NULL | 话术场景(**新增**) |
| `system_prompt` | TEXT NOT NULL | |
| `max_reply_length` | INTEGER NOT NULL DEFAULT 80 | |
| `extra` | JSONB NOT NULL DEFAULT '{}' | 存 `variables[]` / `category` / 渲染元数据 |
| `is_active` | BOOLEAN NOT NULL DEFAULT TRUE | 发布态 |
| `version` | INTEGER NOT NULL DEFAULT 1 | |
| `description` | TEXT | |
| `create_time`/`create_user`/`update_time`/`update_user` | TIMESTAMPTZ/TEXT | 审计(`create_user`← Better Auth session) |

- `UNIQUE(tenant_id, biz_type, scenario)`
- `INDEX(tenant_id, biz_type)`、`INDEX(biz_type)`
- **破坏性变更**:删旧 `UNIQUE(biz_system, biz_type)`、`biz_system` 列;新增 `scenario`。初期重建。

### 3.2 `prompt_version`(新增,支撑回滚)

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | BIGSERIAL PK | |
| `tenant_id` / `biz_type` / `scenario` | TEXT | 归属 |
| `system_prompt` | TEXT | 该版本内容 |
| `version` | INTEGER | 版本号 |
| `snapshot` | JSONB | 完整 PromptTemplate 快照(标题/category/variables 等) |
| `update_user` / `update_time` | | 审计 |

- `INDEX(tenant_id, biz_type, scenario, version)`

### 3.3 `inbound_route`(新增,DID 路由表)

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | BIGSERIAL PK | |
| `did` | TEXT | 精确被叫号(8001) |
| `did_pattern` | TEXT NULL | 号段正则(可选,精确号优先) |
| `tenant_id` / `biz_type` / `scenario` | TEXT | 解析目标 |
| `is_active` | BOOLEAN | |
| `description` | TEXT | |

- `UNIQUE(did)`(精确号);`did_pattern` 为可选号段匹配,精确号优先于号段。

### 3.4 `call_task`(新增,定义层)

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | BIGSERIAL PK | |
| `tenant_id` | TEXT | |
| `name` | TEXT | 任务名 |
| `prompt_id` | BIGINT → prompt_config.id | 绑定提示词(**三维度来源**) |
| `kb_ids` | JSONB | 绑定知识库 |
| `status` | TEXT | idle/running/paused/completed(本期仅定义,执行另算) |
| `concurrent_limit` / `allowed_hours` / `redial_strategy` | | 策略(本期仅存储,不执行) |
| `dept_id` | TEXT | 映射 biz_type |
| 审计列 | | |

> **不含执行态**:不建 scheduler 表、不存 imported_targets 执行进度。originate/调度/重拨 = 下个变更。

## 4. 呼入全链路透传

```
dialplan 取 destination_number(DID) → 设 channel var did / user_key=${caller_id_number}
  → answer → playback silence_stream://-1
ESL CHANNEL_ANSWER
  → main.py 读 variable_did / variable_user_key
  → 查 inbound_route 解析 (tenant_id, biz_type, scenario)   ★ 新增
  → registry.register(uuid, tenant_id, biz_type, scenario, user_key)
  → WS /media/{uuid}: StreamingCallHandler
  → CallGraphState 携带 (tenant_id, biz_type, scenario, user_key)
  → flow.run_streaming_pipeline:
      get_system_prompt(tenant_id, biz_type, scenario)   # Redis(5min) → DB 两级
        = render(system_prompt, vars_context)             # ★ 新增渲染步骤
      build_messages(...)
```

**派生改动点(agent-flow)**:
- `prompt_config.py`:签名改 `get_system_prompt(tenant_id, biz_type, scenario)`;Redis key 改格式;`invalidate_prompt_cache(tenant_id, biz_type, scenario)`。
- 新增 `render.py`(或 prompt.py 内):`render(template, vars_context)`。
- `registry.py` / `ActiveCall`:增 `tenant_id` / `scenario` 字段。
- `handler.py` / `main.py`:透传四元组;CHANNEL_ANSWER 加 `inbound_route` 查询。
- `call_session` 表:增 `tenant_id` / `scenario` 列(初期可加);`biz_type` CHECK 约束保留。

**路由解析时机(推荐)**:agent-flow 查 `inbound_route`(dialplan 只取 DID,保持哑)。预留 dialplan 静态回退(旧 8001/8002/8003 模式可临时保留过渡)。

## 5. 变量渲染(断点 ③)

**变量上下文来源**(fan-out 聚合):
- `mcp_identity(user_key)` ← MCP `user_identity_query`(已有:user_id/phone_masked/id_card_last_four)
- `memory(user_key)` ← Redis 热记忆 + PG 长期记忆(已有)
- `call_task.vars` ← 外呼任务导入的目标变量(**仅外呼**,本期定义层已有数据,执行另算)

**渲染策略**:
- `extra.variables[]` 声明模板所需变量(元数据)。
- `render(template, vars_context)`:`{name}` → `vars_context[name]`。
- **未声明占位符 / 运行时缺失** → 占位符**原样保留** + `WARNING` 日志(可观测,不崩)。

## 6. 缓存失效(断点 ⑤)

**机制(推荐)**:Console 与 agent-flow **共享同一 Redis**;`publish` 动作直接 `DEL cb:prompt:{tenant_id}:{biz_type}:{scenario}`。
- 零延迟生效,无需 agent-flow 暴露 HTTP 失效接口。
- Console `publish` Route Handler 内,Drizzle 写主表 + `prompt_version` 快照后,同步 Redis DEL。

## 7. Console 后端(Next.js 15 + Drizzle + Better Auth)

**认证**:
- Better Auth `socialProviders.adfs`(企业 OAuth)+ `credentialProvider`(email/password 兜底)。
- session 注入 `user.tenantId`(用户归属租户);Drizzle 写入 `create_user`/`update_user`。
- RBAC 中间件守护:`prompt:view/create/update/delete/test`、`calltask:*`、`route:*`。

**Route Handlers**:
- `/api/auth/[...all]` — Better Auth
- `/api/prompts` — GET/POST;`/:id` PUT;`/:id/clone`、`/:id/publish`(★ 写主表+版本快照+清缓存)、`/:id/rollback`、`/:id/test`(联调 LLM)、`/:id/versions`
- `/api/call-tasks` — 定义层 CRUD(GET/POST/PUT/DELETE)
- `/api/inbound-routes` — DID 路由运营 CRUD

## 8. 范围边界(重申)

**包含**:Console 提示词 + 外呼任务(定义层)+ DID 路由运营后端;agent-flow 提示词链路对齐(透传 + 渲染 + 缓存失效);统一 schema 双端重构。

**不包含**:Console 其他模块后端(知识库/记忆/通话记录);agent-flow MCP/RAG/TTS/ASR/barge-in 既有逻辑;**外呼执行(originate/调度/重拨)** → 下个变更。

## 9. 成功标准(对齐 proposal,补充设计维度)

1. Console(ADFS 或本地账密)管理提示词/外呼任务/DID 路由,落 PostgreSQL。
2. 发布生效后,新呼入按 `(tenant_id, biz_type, scenario)` 命中,**零缓存延迟**。
3. `{变量}` 呼入时正确渲染(缺失→占位+告警)。
4. 多租户隔离:A 租户配置不影响 B 租户。
5. 外呼任务可绑定 promptId(定义层持久化)。
6. 版本回滚从 `prompt_version` 快照恢复,跨重启有效。
7. Drizzle 与 SQLAlchemy 提示词模型列名/维度完全一致。

## 10. 下一步

`/openflow spec` — 展开完整规格:
- Drizzle schema 完整 DDL + migration
- SQLAlchemy 双端对齐(alembic migration)
- agent-flow 改动点逐文件清单
- Next.js Route Handler 契约签名 + Better Auth 配置
- `render()` / `inbound_route` 查询 / 缓存失效的完整规格
