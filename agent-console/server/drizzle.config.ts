import { defineConfig } from 'drizzle-kit';

// Drizzle 操作与 agent-flow(SQLAlchemy)同一物理库同一张表。
// prompt_config / prompt_version 由 agent-flow 的 alembic 0002/0003 建表并维护 DDL;
// Console 侧 schema 仅作类型映射,不另起迁移链(避免双迁移源),故 migrations.disabled。
export default defineConfig({
  schema: './src/db/schema.ts',
  out: './src/db/migrations',
  dialect: 'postgresql',
  dbCredentials: {
    url: process.env.DATABASE_URL ?? 'postgresql://postgres:postgres@127.0.0.1:5432/callbot',
  },
  schemaFilter: ['callbot', 'console'],
  verbose: true,
  strict: true,
});
