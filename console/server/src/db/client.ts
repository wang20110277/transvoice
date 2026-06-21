/**
 * Drizzle client — node-postgres 连接池,连 agent-flow 同一物理库(callbot schema)。
 * 与 agent-flow 共享同一物理表,同一 Redis 实例。
 */
import { drizzle } from 'drizzle-orm/node-postgres';
import pg from 'pg';
import * as schema from './schema';

const url =
  process.env.DATABASE_URL ?? 'postgresql://postgres:postgres@127.0.0.1:5432/callbot';

declare global {
  // 防止 dev 模式 HMR 反复建池
  // eslint-disable-next-line no-var
  var __pgPool: pg.Pool | undefined;
}

const pool: pg.Pool = globalThis.__pgPool ?? new pg.Pool({ connectionString: url, max: 10 });

if (process.env.NODE_ENV !== 'production') {
  globalThis.__pgPool = pool;
}

export const db = drizzle(pool, { schema });
export { schema };
