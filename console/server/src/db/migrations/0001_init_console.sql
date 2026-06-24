-- Console 管理端全量初始化（合并旧 0001_console_auth.sql + 0002_tenant_management.sql）
-- 独立 schema console（Better Auth + 多租户管理），与 agent-flow 的 callbot schema 同库隔离。
-- 数据可丢重建：DROP SCHEMA console CASCADE 后重跑本文件。
-- 列与 src/db/schema.ts 严格一致；全字段中文 COMMENT。

CREATE SCHEMA IF NOT EXISTS console;

-- ============================================================
-- user — 用户表（Better Auth + 租户归属/角色扩展）
-- ============================================================
CREATE TABLE IF NOT EXISTS console."user" (
    id            TEXT        PRIMARY KEY,
    name          TEXT        NOT NULL,
    email         TEXT        NOT NULL UNIQUE,
    email_verified BOOLEAN     NOT NULL DEFAULT FALSE,
    image         TEXT,
    tenant_id     TEXT        NOT NULL DEFAULT 'default',
    role          TEXT        NOT NULL DEFAULT 'admin',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE console."user" IS '用户表（Better Auth + 租户归属/角色扩展）';
COMMENT ON COLUMN console."user".id IS '用户ID';
COMMENT ON COLUMN console."user".name IS '用户名';
COMMENT ON COLUMN console."user".email IS '邮箱(唯一)';
COMMENT ON COLUMN console."user".email_verified IS '邮箱是否已验证';
COMMENT ON COLUMN console."user".image IS '头像URL';
COMMENT ON COLUMN console."user".tenant_id IS '归属租户ID(多租户隔离键)';
COMMENT ON COLUMN console."user".role IS 'RBAC角色: admin/editor/viewer/platform_admin';
COMMENT ON COLUMN console."user".created_at IS '创建时间';
COMMENT ON COLUMN console."user".updated_at IS '更新时间';

-- ============================================================
-- session — 会话表（Better Auth + 活跃租户）
-- ============================================================
CREATE TABLE IF NOT EXISTS console.session (
    id                TEXT        PRIMARY KEY,
    expires_at        TIMESTAMPTZ NOT NULL,
    token             TEXT        NOT NULL UNIQUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ip_address        TEXT,
    user_agent        TEXT,
    user_id           TEXT        NOT NULL REFERENCES console."user"(id) ON DELETE CASCADE,
    active_tenant_id  TEXT
);
COMMENT ON TABLE console.session IS '会话表（Better Auth + 活跃租户）';
COMMENT ON COLUMN console.session.id IS '会话ID';
COMMENT ON COLUMN console.session.expires_at IS '会话过期时间';
COMMENT ON COLUMN console.session.token IS '会话令牌(唯一)';
COMMENT ON COLUMN console.session.created_at IS '创建时间';
COMMENT ON COLUMN console.session.updated_at IS '更新时间';
COMMENT ON COLUMN console.session.ip_address IS '登录IP地址';
COMMENT ON COLUMN console.session.user_agent IS 'User-Agent';
COMMENT ON COLUMN console.session.user_id IS '关联用户ID';
COMMENT ON COLUMN console.session.active_tenant_id IS '会话级活跃租户ID(空时fallback user.tenant_id 主租户)';

-- ============================================================
-- account — 第三方/凭据账号表（Better Auth）
-- ============================================================
CREATE TABLE IF NOT EXISTS console.account (
    id                        TEXT        PRIMARY KEY,
    account_id                TEXT        NOT NULL,
    provider_id               TEXT        NOT NULL,
    user_id                   TEXT        NOT NULL REFERENCES console."user"(id) ON DELETE CASCADE,
    access_token              TEXT,
    refresh_token             TEXT,
    id_token                  TEXT,
    access_token_expires_at   TIMESTAMPTZ,
    refresh_token_expires_at  TIMESTAMPTZ,
    scope                     TEXT,
    password                  TEXT,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE console.account IS '账号/凭据表（Better Auth：本地账密 + OAuth 第三方）';
COMMENT ON COLUMN console.account.id IS '账号ID';
COMMENT ON COLUMN console.account.account_id IS '提供方账号ID(本地账密=user.id)';
COMMENT ON COLUMN console.account.provider_id IS '提供方标识(credential/email/oauth 提供商)';
COMMENT ON COLUMN console.account.user_id IS '关联用户ID';
COMMENT ON COLUMN console.account.access_token IS 'OAuth access_token';
COMMENT ON COLUMN console.account.refresh_token IS 'OAuth refresh_token';
COMMENT ON COLUMN console.account.id_token IS 'OAuth id_token';
COMMENT ON COLUMN console.account.access_token_expires_at IS 'access_token 过期时间';
COMMENT ON COLUMN console.account.refresh_token_expires_at IS 'refresh_token 过期时间';
COMMENT ON COLUMN console.account.scope IS 'OAuth scope';
COMMENT ON COLUMN console.account.password IS '本地账密哈希(credential provider)';
COMMENT ON COLUMN console.account.created_at IS '创建时间';
COMMENT ON COLUMN console.account.updated_at IS '更新时间';

-- ============================================================
-- verification — 验证码表（Better Auth）
-- ============================================================
CREATE TABLE IF NOT EXISTS console.verification (
    id          TEXT        PRIMARY KEY,
    identifier  TEXT        NOT NULL,
    value       TEXT        NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
COMMENT ON TABLE console.verification IS '验证码表（Better Auth：邮箱验证/找回密码等）';
COMMENT ON COLUMN console.verification.id IS '验证ID';
COMMENT ON COLUMN console.verification.identifier IS '验证标识(邮箱/手机号)';
COMMENT ON COLUMN console.verification.value IS '验证值(验证码/token)';
COMMENT ON COLUMN console.verification.expires_at IS '过期时间';
COMMENT ON COLUMN console.verification.created_at IS '创建时间';
COMMENT ON COLUMN console.verification.updated_at IS '更新时间';

-- ============================================================
-- tenant — 租户主表
-- ============================================================
CREATE TABLE IF NOT EXISTS console.tenant (
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
CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_name ON console.tenant (name);
COMMENT ON TABLE console.tenant IS '租户主表 — tenant_id 字符串升为一等公民';
COMMENT ON COLUMN console.tenant.id IS '租户ID';
COMMENT ON COLUMN console.tenant.name IS '租户名称(唯一)';
COMMENT ON COLUMN console.tenant.status IS '状态: active/disabled';
COMMENT ON COLUMN console.tenant.quota IS '配额(JSON，如并发/号码上限)';
COMMENT ON COLUMN console.tenant.description IS '描述说明';
COMMENT ON COLUMN console.tenant.create_time IS '记录创建时间';
COMMENT ON COLUMN console.tenant.create_user IS '记录创建人';
COMMENT ON COLUMN console.tenant.update_time IS '记录更新时间';
COMMENT ON COLUMN console.tenant.update_user IS '记录更新人';

-- ============================================================
-- user_tenant — 用户-租户关联表
-- ============================================================
CREATE TABLE IF NOT EXISTS console.user_tenant (
    id          BIGSERIAL    PRIMARY KEY,
    user_id     TEXT         NOT NULL REFERENCES console."user"(id) ON DELETE CASCADE,
    tenant_id   TEXT         NOT NULL REFERENCES console.tenant(id) ON DELETE CASCADE,
    is_primary  BOOLEAN      NOT NULL DEFAULT FALSE,
    create_time TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_tenant_user_tenant UNIQUE (user_id, tenant_id)
);
CREATE INDEX IF NOT EXISTS ix_user_tenant_tenant ON console.user_tenant (tenant_id);
-- 每用户仅一条 is_primary=true(部分唯一索引)
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_tenant_primary ON console.user_tenant (user_id) WHERE is_primary;
COMMENT ON TABLE console.user_tenant IS '用户-租户关联表 — 1 用户多租户';
COMMENT ON COLUMN console.user_tenant.id IS '自增主键';
COMMENT ON COLUMN console.user_tenant.user_id IS '用户ID';
COMMENT ON COLUMN console.user_tenant.tenant_id IS '租户ID';
COMMENT ON COLUMN console.user_tenant.is_primary IS '是否主租户(每用户仅一条 true，登录默认活跃租户来源)';
COMMENT ON COLUMN console.user_tenant.create_time IS '记录创建时间';
