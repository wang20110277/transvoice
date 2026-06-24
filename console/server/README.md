# 智能外呼控制台 (console/server)

Next.js 15 (App Router) + Drizzle ORM + Better Auth 后端,管理提示词配置。
与 agent-flow 共用同一物理库(`callbot` schema)、同一 Redis 实例,发布即零延迟生效。

## 架构

- **后端**:Next.js Route Handlers(`/api/prompts/*`) + Drizzle ORM(node-postgres)
- **认证**:Better Auth(本地账密 email/password;ADFS OAuth 走 `CONSOLE_ADFS_ENABLED` 预留)
- **多租户**:session.user.tenantId 隔离,跨租户 404
- **缓存失效**:publish/rollback 直删 Redis key `cb:prompt:{tenant_id}:{biz_type}:{scenario}`(与 agent-flow 共享)

## 数据模型(与 agent-flow SQLAlchemy 同表同列名)

- `callbot.prompt_config` — 主表,UNIQUE(tenant_id, biz_type, scenario),单行即当前内容
- `callbot.prompt_version` — 版本快照(支撑回滚)
- `callbot.inbound_route` — DID/号段 → (tenant_id, biz_type, scenario),呼入解析
- `console_auth.*` — Better Auth 自带(user/session/account/verification)

> `prompt_config`/`prompt_version`/`inbound_route` 由 **agent-flow alembic** 建表(0002/0003);`console_auth` 由 Console 自行建表(`src/db/migrations/0001_console_auth.sql`)。

## DID 呼入路由(与 FreeSWITCH dialplan 的关系)

三元组 `(tenant_id, biz_type, scenario)` **不在 dialplan 硬编码**。链路:

```
呼入 → FS catch-all 拨号计划(仅 answer + user_key + 保活)
     → agent-flow CHANNEL_ANSWER 读 Caller-Destination-Number = DID
     → 查 callbot.inbound_route(精确 did 优先,号段 did_pattern 兜底)
     → (tenant_id, biz_type, scenario) → 命中 prompt_config
```

- **新增 DID / 租户 / scenario**:Console「DID 路由」页加一行 → 即时生效,不动 FreeSWITCH。
- dialplan 改为 catch-all 后,需在 FS 执行一次 `fs_cli -x "reloadxml"`(仅首次切换时;之后增删路由无需再 reload)。


## API

| Method | Path | 行为 |
|---|---|---|
| POST | `/api/auth/sign-in/email` | 登录 |
| GET / POST | `/api/prompts` | 列表 / 新建(草稿) |
| GET / PUT / DELETE | `/api/prompts/:id` | 详情 / 编辑(version++) / 删除 |
| POST | `/api/prompts/:id/clone` | 克隆到新 scenario |
| POST | `/api/prompts/:id/publish` | 置 is_active=true + 清缓存 |
| POST | `/api/prompts/:id/rollback` | 从版本快照恢复 + 清缓存 |
| POST | `/api/prompts/:id/test` | 渲染变量 + 调 ollama 返回样例 |
| GET | `/api/prompts/:id/versions` | 版本历史 |
| GET / POST | `/api/inbound-routes` | DID 路由列表 / 新增 |
| GET / PUT / DELETE | `/api/inbound-routes/:id` | 详情 / 编辑 / 删除 |

## 本地开发

```bash
# 1. 配置(默认连本地 callbot 库 + 本地 redis + 本地 ollama)
cp .env.example .env.local   # 按需修改

# 2. 建认证表 + seed 用户
PGPASSWORD=postgres psql -h 127.0.0.1 -U postgres -d callbot -f src/db/migrations/0001_console_auth.sql
npm run db:seed

# 3. 前置:agent-flow 的 0003 迁移必须已应用(prompt_config 需有 tenant_id/scenario 列)
cd ../../agent-flow && CALLBOT_PG_DSN=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/callbot PYTHONPATH=$(pwd)/src alembic upgrade head

# 4. 启动
npm run dev    # http://localhost:3001
```

### 测试账号
- `admin@transvoice.local` / `admin123`(tenant=default,可见现有 3 条提示词)
- `fin@transvoice.local` / `admin123`(tenant=galaxy_fin,空,演示多租户隔离)

### 联调 LLM
默认连本地 ollama(`CONSOLE_LLM_BASE_URL`/`CONSOLE_LLM_MODEL`),模型 `qwen3:4b-instruct`。
联调沙箱渲染 `{变量}` 后调 LLM 返回样例回复。

## 测试

```bash
npm test       # 纯逻辑单测(变量提取/渲染)
```
