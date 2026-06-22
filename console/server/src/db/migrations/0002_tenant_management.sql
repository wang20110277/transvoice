-- 租户管理 — tenant 主表 + user_tenant 关联表 + session 活跃租户列。
-- 独立于 agent-flow alembic,由 Console 自行维护(同 0001_console_auth.sql 风格)。
-- 幂等:所有对象 IF NOT EXISTS,可重复执行。配套 seed-tenants.ts 回填存量数据。
-- 列定义与 src/db/schema.ts 严格一致(consoleAuth schema)。

-- ── tenant 主表(tenant_id 字符串升为一等公民)──
CREATE TABLE IF NOT EXISTS console_auth.tenant (
    id            TEXT        PRIMARY KEY,
    name          TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'active',
    quota         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    description   TEXT,
    create_time   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    create_user   TEXT        NOT NULL DEFAULT 'system',
    update_time   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    update_user   TEXT        NOT NULL DEFAULT 'system'
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_name ON console_auth.tenant (name);

-- ── user_tenant 关联表(1 用户多租户)──
CREATE TABLE IF NOT EXISTS console_auth.user_tenant (
    id          BIGSERIAL    PRIMARY KEY,
    user_id     TEXT         NOT NULL REFERENCES console_auth."user"(id) ON DELETE CASCADE,
    tenant_id   TEXT         NOT NULL REFERENCES console_auth.tenant(id) ON DELETE CASCADE,
    is_primary  BOOLEAN      NOT NULL DEFAULT FALSE,
    create_time TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_tenant_user_tenant UNIQUE (user_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS ix_user_tenant_tenant ON console_auth.user_tenant (tenant_id);
-- 每用户仅一条 is_primary=true(部分唯一索引)
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_tenant_primary ON console_auth.user_tenant (user_id) WHERE is_primary;

-- ── session 加活跃租户列(会话级 activeTenantId,可空)──
ALTER TABLE console_auth.session ADD COLUMN IF NOT EXISTS active_tenant_id TEXT;
