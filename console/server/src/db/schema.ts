/**
 * Drizzle schema — 列名/维度与 agent-flow SQLAlchemy (src/db/models.py) 严格一致,
 * 杜绝双词汇表。prompt_config / prompt_version 由 agent-flow alembic 建表,
 * Console 仅作类型映射,不改 DDL。
 *
 * Better Auth 自带表放 console_auth 独立 schema,不污染 callbot。
 */
import { sql } from 'drizzle-orm';
import {
  bigserial,
  boolean,
  integer,
  jsonb,
  pgSchema,
  pgTable,
  text,
  timestamp,
  uniqueIndex,
  index,
} from 'drizzle-orm/pg-core';

// ── callbot schema(与 agent-flow 共用) ──────────────────────────────

export const callbot = pgSchema('callbot');

/** 提示词主表 — 唯一键 (tenant_id, biz_type, scenario),extra 存 variables[]/category */
export const promptConfig = callbot.table(
  'prompt_config',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    tenantId: text('tenant_id').notNull().default('default'),
    bizType: text('biz_type').notNull(),
    scenario: text('scenario').notNull().default('default'),
    systemPrompt: text('system_prompt').notNull(),
    maxReplyLength: integer('max_reply_length').notNull().default(80),
    extra: jsonb('extra').notNull().default(sql`'{}'::jsonb`),
    isActive: boolean('is_active').notNull().default(true),
    version: integer('version').notNull().default(1),
    description: text('description'),
    createTime: timestamp('create_time', { withTimezone: true }).notNull().defaultNow(),
    createUser: text('create_user').notNull().default('system'),
    updateTime: timestamp('update_time', { withTimezone: true }).notNull().defaultNow(),
    updateUser: text('update_user').notNull().default('system'),
  },
  (t) => [
    uniqueIndex('uq_prompt_config_tenant_biz_scenario').on(t.tenantId, t.bizType, t.scenario),
    index('ix_prompt_config_tenant_biz').on(t.tenantId, t.bizType),
    index('ix_prompt_config_biz_type').on(t.bizType),
  ],
);

/** 版本快照表 — 支撑跨重启回滚 */
export const promptVersion = callbot.table(
  'prompt_version',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    tenantId: text('tenant_id').notNull(),
    bizType: text('biz_type').notNull(),
    scenario: text('scenario').notNull(),
    systemPrompt: text('system_prompt').notNull(),
    version: integer('version').notNull(),
    snapshot: jsonb('snapshot').notNull().default(sql`'{}'::jsonb`),
    updateUser: text('update_user').notNull().default('system'),
    updateTime: timestamp('update_time', { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [index('ix_prompt_version_lookup').on(t.tenantId, t.bizType, t.scenario, t.version)],
);

export type PromptConfig = typeof promptConfig.$inferSelect;
export type PromptVersion = typeof promptVersion.$inferSelect;
export type NewPromptConfig = typeof promptConfig.$inferInsert;

/** DID 路由表 — 被叫号/号段 → (tenant_id, biz_type, scenario)。agent-flow CHANNEL_ANSWER 查表解析。 */
export const inboundRoute = callbot.table(
  'inbound_route',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    did: text('did').notNull(),
    didPattern: text('did_pattern'),
    tenantId: text('tenant_id').notNull(),
    bizType: text('biz_type').notNull(),
    scenario: text('scenario').notNull().default('default'),
    isActive: boolean('is_active').notNull().default(true),
    description: text('description'),
    createTime: timestamp('create_time', { withTimezone: true }).notNull().defaultNow(),
    createUser: text('create_user').notNull().default('system'),
    updateTime: timestamp('update_time', { withTimezone: true }).notNull().defaultNow(),
    updateUser: text('update_user').notNull().default('system'),
  },
  (t) => [uniqueIndex('uq_inbound_route_did').on(t.did)],
);

export type InboundRoute = typeof inboundRoute.$inferSelect;

// ── console_auth schema(Better Auth 自带) ───────────────────────────

export const consoleAuth = pgSchema('console_auth');

export const user = consoleAuth.table('user', {
  id: text('id').primaryKey(),
  name: text('name').notNull(),
  email: text('email').notNull().unique(),
  emailVerified: boolean('email_verified').notNull().default(false),
  image: text('image'),
  // 自定义:租户归属,多租户隔离的隔离键
  tenantId: text('tenant_id').notNull().default('default'),
  // 自定义:RBAC 角色 → 权限码集合(seeds 时写入)
  role: text('role').notNull().default('admin'),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
});

export const session = consoleAuth.table('session', {
  id: text('id').primaryKey(),
  expiresAt: timestamp('expires_at', { withTimezone: true }).notNull(),
  token: text('token').notNull().unique(),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
  ipAddress: text('ip_address'),
  userAgent: text('user_agent'),
  userId: text('user_id')
    .notNull()
    .references(() => user.id, { onDelete: 'cascade' }),
});

export const account = consoleAuth.table('account', {
  id: text('id').primaryKey(),
  accountId: text('account_id').notNull(),
  providerId: text('provider_id').notNull(),
  userId: text('user_id')
    .notNull()
    .references(() => user.id, { onDelete: 'cascade' }),
  accessToken: text('access_token'),
  refreshToken: text('refresh_token'),
  idToken: text('id_token'),
  accessTokenExpiresAt: timestamp('access_token_expires_at', { withTimezone: true }),
  refreshTokenExpiresAt: timestamp('refresh_token_expires_at', { withTimezone: true }),
  scope: text('scope'),
  password: text('password'),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
});

export const verification = consoleAuth.table('verification', {
  id: text('id').primaryKey(),
  identifier: text('identifier').notNull(),
  value: text('value').notNull(),
  expiresAt: timestamp('expires_at', { withTimezone: true }).notNull(),
  createdAt: timestamp('created_at', { withTimezone: true }).defaultNow(),
  updatedAt: timestamp('updated_at', { withTimezone: true }).defaultNow(),
});

export type User = typeof user.$inferSelect;
