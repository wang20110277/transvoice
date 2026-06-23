# 实现计划:align-prompt-config-pipeline

## 来源

- 提案:`openspec/changes/align-prompt-config-pipeline/proposal.md`
- 设计:`openspec/changes/align-prompt-config-pipeline/design.md`
- 规格:`openspec/changes/align-prompt-config-pipeline/specs/`(4 个能力)
- 任务:`openspec/changes/align-prompt-config-pipeline/tasks.md`

> 执行顺序严格按依赖:先建表(DB)→ 后所有读写都依赖表存在。每个步骤标注改动文件 + 验证方式。

---

## Task 1: DB Schema 重构(agent-flow SQLAlchemy + alembic)

> 基础地基,所有后续任务依赖表结构存在。

### Step 1.1 — 重构 PromptConfig 模型
- **改动文件**:`agent-flow/src/db/models.py`(`PromptConfig` 类)
- **做什么**:`biz_system` 列重命名为 `tenant_id`;新增 `scenario` 列(Text,NOT NULL);改 `UniqueConstraint` 为 `(tenant_id, biz_type, scenario)`;新增 `Index(tenant_id, biz_type)`
- **验证**:`cd agent-flow && python -c "from src.db.models import PromptConfig; print(PromptConfig.__table__.c.keys())"` 输出含 `tenant_id`/`scenario`,无 `biz_system`

### Step 1.2 — 新增 PromptVersion 模型
- **改动文件**:`agent-flow/src/db/models.py`(新增 `PromptVersion` 类,`schema='callbot'`)
- **做什么**:字段 `id`/`tenant_id`/`biz_type`/`scenario`/`system_prompt`/`version`/`snapshot(JSONB)`/`update_user`/`update_time`;`Index(tenant_id, biz_type, scenario, version)`
- **验证**:`python -c "from src.db.models import PromptVersion"` 无 ImportError

### Step 1.3 — 新增 InboundRoute 模型
- **改动文件**:`agent-flow/src/db/models.py`(新增 `InboundRoute` 类)
- **做什么**:字段 `id`/`did`/`did_pattern`/`tenant_id`/`biz_type`/`scenario`/`is_active`/`description`/审计;`UniqueConstraint(did)`
- **验证**:同上 import 检查

### Step 1.4 — 新增 CallTask 模型
- **改动文件**:`agent-flow/src/db/models.py`(新增 `CallTask` 类)
- **做什么**:字段含 `prompt_id` ForeignKey→`prompt_config.id`、`kb_ids(JSONB)`、`redial_strategy(JSONB)`、`status`/`concurrent_limit`/`allowed_hours`/`dept_id`/审计
- **验证**:同上

### Step 1.5 — CallSession 增列
- **改动文件**:`agent-flow/src/db/models.py`(`CallSession` 类)
- **做什么**:新增 `tenant_id`(Text,nullable)、`scenario`(Text,nullable)列;保留 `biz_type` CHECK 约束
- **验证**:import 检查

### Step 1.6 — alembic migration
- **改动文件**:`agent-flow/alembic/versions/0003_prompt_pipeline_align.py`(新建,`down_revision='0002'`)
- **做什么**:drop `uq_prompt_config_system_type`;`ALTER TABLE` rename `biz_system→tenant_id`、add `scenario`;create `prompt_version`/`inbound_route`/`call_task` 三表;`ALTER call_session ADD tenant_id/scenario`;迁移现有 seed(为 3 条默认提示词补 `tenant_id='default'`/`scenario='default'`,删旧 `description` 引用)
- **验证**:`cd agent-flow && PYTHONPATH=$(pwd)/src alembic upgrade head` 成功;`\d callbot.prompt_config` 见新列与约束

### Step 1.7 — 表结构对齐检查
- **改动文件**:无(验证步骤)
- **验证**:对比 Drizzle 侧(Step 4.3)列名一致;`openspec validate align-prompt-config-pipeline --strict` 仍通过

---

## Task 2: agent-flow 提示词加载 / 渲染 / 缓存

> 依赖 Task 1 表结构。

### Step 2.1 — get_system_prompt 三元组签名
- **改动文件**:`agent-flow/src/graph/prompt_config.py`
- **做什么**:`get_system_prompt(tenant_id, biz_type, scenario)`;Redis key `cb:prompt:{tenant_id}:{biz_type}:{scenario}`;查询 `where(tenant_id==, biz_type==, scenario==, is_active)`;返回 `system_prompt` 字符串或 `""`
- **验证**:`pytest tests/ -k prompt -v`(若有);或手动调,确认日志 `tenant_id=... scenario=...`

### Step 2.2 — invalidate_prompt_cache 三元组
- **改动文件**:`agent-flow/src/graph/prompt_config.py`
- **做什么**:签名改 `(tenant_id, biz_type, scenario)`,`DEL` 对应 key
- **验证**:代码 review;Console publish 后(Step 5.3)端到端验证

### Step 2.3 — 新增 render 模块
- **改动文件**:`agent-flow/src/graph/render.py`(新建)
- **做什么**:`render(template: str, vars_context: dict, declared: list[str]) -> str`;按 `declared`(来自 `extra.variables`)替换 `{name}`;缺失则保留原占位符 + `logger.warning(...)`(含 tenant/scenario/var 名)
- **验证**:单测 `render("hi {name}", {"name":"X"}, ["name"])=="hi X"`;`render("hi {y}", {}, ["y"])=="hi {y}"` 且有 warning

### Step 2.4 — build_messages 接入渲染
- **改动文件**:`agent-flow/src/graph/prompt.py`
- **做什么**:`build_messages` 接收 `vars_context` 参数,先 `render(system_prompt, vars_context, declared)` 再组装;`flow.py` 调用处传入(Step 3.4)
- **验证**:flow 调用链 review

---

## Task 3: agent-flow 呼入维度全链路透传

> 依赖 Task 1 + Task 2。

### Step 3.1 — ActiveCall 增维度
- **改动文件**:`agent-flow/src/ws/registry.py`
- **做什么**:`ActiveCall` dataclass 增 `tenant_id`/`scenario`;`register(call_id, tenant_id, biz_type, scenario, user_key)`
- **验证**:`python -c "from src.ws.registry import ActiveCall; ActiveCall.__dataclass_fields__"` 含新字段

### Step 3.2 — CHANNEL_ANSWER 查路由表解析
- **改动文件**:`agent-flow/main.py`(`_on_channel_answer` 约 line 150)
- **做什么**:读 `variable_did`(若无则回落读 `variable_tenant_id/biz_type/scenario` 静态回退);查 `inbound_route`(精确 `did` 优先,号段 `did_pattern` 兜底)得三元组;`register(...)` 传四元组
- **验证**:呼入日志 `[uuid] CHANNEL_ANSWER tenant_id=... biz_type=... scenario=...`;无匹配时 WARNING

### Step 3.3 — handler 透传四元组
- **改动文件**:`agent-flow/src/ws/handler.py`
- **做什么**:`handle(websocket, call_id, tenant_id, biz_type, scenario, user_key)`;`_resolve_active_call`/WS 路径透传;写入 `CallGraphState`
- **验证**:flow 调用链 review;`main.py:342` 调用处同步改

### Step 3.4 — run_streaming_pipeline 取三元组 + 聚合 vars_context
- **改动文件**:`agent-flow/src/graph/flow.py`(约 line 360-376)
- **做什么**:从 `state` 取 `tenant_id`/`biz_type`/`scenario`;聚合 `vars_context = {**mcp_identity(user_key), **memory(user_key), **state.get('call_task_vars', {})}`;`system_prompt = await get_system_prompt(tenant_id, biz_type, scenario)`;`build_messages(..., vars_context=vars_context)`
- **验证**:呼入端到端,日志 `biz_type=... prompt loaded: N chars` 含正确 tenant/scenario;占位符被替换

### Step 3.5 — 透传完整性验证
- **验证**:呼入一通,确认整链路无 `biz_system='default'` 硬编码、无 `scenario` 缺失

---

## Task 4: Console 后端骨架

> 依赖 Task 1(同库同表)。

### Step 4.1 — Next.js 15 项目初始化
- **改动文件**:`console/server/`(目录定位决策:Next.js App Router 与现有 Vite 前端的关系——独立 `server/` 子目录 或 整体迁移;**需在 build 阶段与用户确认目录结构**)
- **做什么**:`next@15` App Router;`package.json` 脚本
- **验证**:`npm run dev` 起服务

### Step 4.2 — Drizzle 接入
- **改动文件**:`console/server/.../db/index.ts`、`drizzle.config.ts`
- **做什么**:Drizzle + `drizzle-orm/node-postgres`;连 PostgreSQL `callbot` schema
- **验证**:`drizzle-kit` 能 introspect 出 `prompt_config` 等表

### Step 4.3 — Drizzle schema(4 表)
- **改动文件**:`console/server/.../db/schema.ts`
- **做什么**:`promptConfig`/`promptVersion`/`inboundRoute`/`callTask`,列名与 Step 1.x SQLAlchemy **完全一致**
- **验证**:列名 diff 对比 `agent-flow/src/db/models.py` 零差异

### Step 4.4 — Better Auth 配置
- **改动文件**:`console/server/.../auth.ts`、`/api/auth/[...all]/route.ts`
- **做什么**:`betterAuth({ socialProviders: { adfs: {...} }, ...credentialProvider() })`;session plugin 注入 `user.tenantId`
- **验证**:ADFS 登录 + 本地账密登录均能建 session;session 含 `tenantId`

### Step 4.5 — RBAC 中间件
- **改动文件**:`console/server/.../middleware.ts`(或 Route Handler 守卫)
- **做什么**:校验权限码 `prompt:*`/`calltask:*`/`route:*`;未认证 401,权限不足 403
- **验证**:curl 未带 session → 401;低权限调 publish → 403

### Step 4.6 — 骨架验证
- **验证**:双 provider 登录通,`tenantId` 注入生效

---

## Task 5: Console API(Route Handlers)

> 依赖 Task 4。

### Step 5.1 — prompts CRUD + clone
- **改动文件**:`console/server/app/api/prompts/route.ts`、`[id]/route.ts`、`[id]/clone/route.ts`
- **做什么**:GET 列表/POST 新建;PUT 编辑(`version` 自增 + 写 `prompt_version` 快照);clone(`version=1` 新记录)
- **验证**:curl 创建→编辑→版本快照写入

### Step 5.2 — rollback
- **改动文件**:`console/server/app/api/prompts/[id]/rollback/route.ts`
- **做什么**:从 `prompt_version` 快照恢复 → 新 `version` 写主表 → DEL Redis
- **验证**:回滚后呼入取到旧内容

### Step 5.3 — publish(★ 核心失效点)
- **改动文件**:`console/server/app/api/prompts/[id]/publish/route.ts`
- **做什么**:置 `is_active=true`(同 key 互斥置 false)+ 写版本快照 + **`DEL cb:prompt:{tenant_id}:{biz_type}:{scenario}`**(共享 Redis)
- **验证**:发布后立即呼入 → agent-flow 取到新版本(零延迟)

### Step 5.4 — test + versions
- **改动文件**:`console/server/app/api/prompts/[id]/test/route.ts`、`[id]/versions/route.ts`
- **做什么**:test=渲染示例变量 + 调 LLM 返回样例;versions=历史快照列表
- **验证**:test 返回 AI 回复

### Step 5.5 — inbound-routes CRUD
- **改动文件**:`console/server/app/api/inbound-routes/route.ts`、`[id]/route.ts`
- **做什么**:DID/号段运营,按 `tenant_id` 隔离
- **验证**:新增 `did=8004` → 呼入 8004 解析到新三元组

### Step 5.6 — call-tasks 定义层 CRUD
- **改动文件**:`console/server/app/api/call-tasks/route.ts`、`[id]/route.ts`
- **做什么**:定义层 CRUD;`prompt_id` 校验同 `tenant_id`
- **验证**:跨租户 promptId 绑定被拒

### Step 5.7 — 端到端验证
- **验证**:发布 → 呼入命中 → 渲染 → 多租户隔离,全链路通

---

## Task 6: 收尾验证

### Step 6.1 — OpenSpec 校验
- **验证**:`openspec validate align-prompt-config-pipeline --strict` 通过

### Step 6.2 — 多租户隔离
- **验证**:A 租户配置不影响 B 租户呼入(集成测试)

### Step 6.3 — 变量渲染
- **验证**:正常替换 + 缺失占位保留 + WARNING 日志

### Step 6.4 — 索引同步 + 测试覆盖
- **改动文件**:测试文件(呼入提示词加载关键路径)
- **验证**:`codegraph sync`;关键路径(WS→state→get_system_prompt→render→build_messages)有测试

---

## 关键风险点(build 阶段需注意)

1. **Step 1.6 migration 是破坏性的** — drop 旧 unique + rename 列,务必确认环境无生产数据或已备份。
2. **Step 4.1 目录结构待定** — Next.js 是独立子目录还是整体迁移现有 Vite 前端,build 阶段需与用户确认。
3. **Step 3.2 DID 解析** — dialplan 需配合(取 DID 设 channel var);FreeSWITCH 配置改动需重启 FS。
4. **缓存失效(Step 5.3)** — Console 与 agent-flow 必须连**同一 Redis 实例**,否则 DEL 无效。
