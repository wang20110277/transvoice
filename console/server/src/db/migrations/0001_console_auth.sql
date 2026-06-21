-- Console 管理端认证表(Better Auth)。
-- 独立 schema console_auth,不污染 agent-flow 的 callbot。
-- 列与 src/db/schema.ts 一致;由 Console 自行维护,不走 agent-flow alembic。

CREATE SCHEMA IF NOT EXISTS console_auth;

CREATE TABLE IF NOT EXISTS console_auth."user" (
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

CREATE TABLE IF NOT EXISTS console_auth.session (
    id          TEXT        PRIMARY KEY,
    expires_at  TIMESTAMPTZ NOT NULL,
    token       TEXT        NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ip_address  TEXT,
    user_agent  TEXT,
    user_id     TEXT        NOT NULL REFERENCES console_auth."user"(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS console_auth.account (
    id                        TEXT        PRIMARY KEY,
    account_id                TEXT        NOT NULL,
    provider_id               TEXT        NOT NULL,
    user_id                   TEXT        NOT NULL REFERENCES console_auth."user"(id) ON DELETE CASCADE,
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

CREATE TABLE IF NOT EXISTS console_auth.verification (
    id          TEXT        PRIMARY KEY,
    identifier  TEXT        NOT NULL,
    value       TEXT        NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ
);
