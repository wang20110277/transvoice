/**
 * Redis — 与 agent-flow 同实例,publish/rollback 时直删 prompt 缓存实现零延迟生效。
 *
 * key 命名与 agent-flow src/graph/prompt_config.py._redis_key 完全一致:
 *   cb:prompt:{tenant_id}:{biz_type}:{scenario}
 */
import Redis from 'ioredis';

let _client: Redis | null = null;

export function getRedis(): Redis {
  if (!_client) {
    _client = new Redis(process.env.REDIS_URL ?? 'redis://127.0.0.1:6379/0', {
      lazyConnect: false,
      maxRetriesPerRequest: 2,
    });
  }
  return _client;
}

export function promptCacheKey(tenantId: string, bizType: string, scenario: string): string {
  return `cb:prompt:${tenantId}:${bizType}:${scenario}`;
}

/**
 * 失效提示词缓存。与 agent-flow invalidate_prompt_cache 等价,
 * 但由 Console 在 publish/rollback 写库后直调(共享同一 Redis 实例)。
 * 失败仅记录,不阻断主流程 —— 缓存自然 TTL(5min)过期兜底。
 */
export async function invalidatePromptCache(
  tenantId: string,
  bizType: string,
  scenario: string,
): Promise<void> {
  const key = promptCacheKey(tenantId, bizType, scenario);
  try {
    await getRedis().del(key);
  } catch (e) {
    // 缓存失效失败不致命:5min TTL 自然过期;记日志便于排查
    console.error('[redis] invalidate prompt cache failed', key, e);
  }
}
