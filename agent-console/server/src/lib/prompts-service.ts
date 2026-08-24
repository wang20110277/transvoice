/**
 * 提示词服务层 — 所有 DB 读写 + 业务逻辑集中于此,Route Handler 保持薄。
 *
 * 数据模型(对齐 agent-flow 实际 schema,非 spec 原文):
 * - prompt_config: UNIQUE(tenant_id, biz_type, scenario),每个维度三元组**仅一行**;
 *   该行即"当前内容",version 自增,is_active 为启用开关(呼入只取 is_active=true)。
 * - prompt_version: append-only 历史快照,支撑回滚。
 *
 * 因此 spec 里"同 key 多版本互斥 active"在实际 schema 下不适用 —— 单行即唯一,
 * publish 仅翻 is_active=true 并清缓存。本服务按实际 schema 实现。
 *
 * extra(JSONB) 存 { title, category, variables };variables 由服务端正则提取,不信前端。
 */
import { and, desc, eq } from 'drizzle-orm';
import { db } from '@/db/client';
import { promptConfig, promptVersion } from '@/db/schema';
import { extractVariables } from './prompt-template';
import { invalidatePromptCache } from './redis';

interface PromptExtra {
  title?: string;
  category?: string;
  variables?: string[];
  [k: string]: unknown;
}

export interface PromptDTO {
  id: number;
  tenantId: string;
  bizType: string;
  scenario: string;
  systemPrompt: string;
  maxReplyLength: number;
  isActive: boolean;
  version: number;
  description: string | null;
  title: string;
  category: string;
  variables: string[];
  createUser: string;
  updateUser: string;
  updateTime: Date;
}

export interface PromptInput {
  title: string;
  bizType: string;
  scenario: string;
  systemPrompt: string;
  category?: string;
  maxReplyLength?: number;
  description?: string | null;
}

type Row = typeof promptConfig.$inferSelect;

function toDTO(row: Row): PromptDTO {
  const extra = (row.extra ?? {}) as PromptExtra;
  return {
    id: row.id,
    tenantId: row.tenantId,
    bizType: row.bizType,
    scenario: row.scenario,
    systemPrompt: row.systemPrompt,
    maxReplyLength: row.maxReplyLength,
    isActive: row.isActive,
    version: row.version,
    description: row.description,
    title: (extra.title as string) ?? '',
    category: (extra.category as string) ?? '通用',
    variables: Array.isArray(extra.variables) ? extra.variables : extractVariables(row.systemPrompt),
    createUser: row.createUser,
    updateUser: row.updateUser,
    updateTime: row.updateTime,
  };
}

/** 写一条版本快照(标题/category/variables 随 snapshot 一并留存)。 */
async function writeVersionSnapshot(row: Row, userEmail: string) {
  await db.insert(promptVersion).values({
    tenantId: row.tenantId,
    bizType: row.bizType,
    scenario: row.scenario,
    systemPrompt: row.systemPrompt,
    version: row.version,
    snapshot: row.extra as Record<string, unknown>,
    updateUser: userEmail,
  });
}

export async function listByTenant(tenantId: string): Promise<PromptDTO[]> {
  const rows = await db.select().from(promptConfig).where(eq(promptConfig.tenantId, tenantId));
  return rows.map(toDTO);
}

/** tenant 作用域查询;跨租户返回 null(调用方 404)。 */
export async function getById(id: number, tenantId: string): Promise<PromptDTO | null> {
  const rows = await db
    .select()
    .from(promptConfig)
    .where(and(eq(promptConfig.id, id), eq(promptConfig.tenantId, tenantId)));
  return rows[0] ? toDTO(rows[0]) : null;
}

export async function create(input: PromptInput, tenantId: string, userEmail: string): Promise<PromptDTO> {
  const variables = extractVariables(input.systemPrompt);
  const extra: PromptExtra = {
    title: input.title,
    category: input.category ?? '通用',
    variables,
  };
  const [row] = await db
    .insert(promptConfig)
    .values({
      tenantId,
      bizType: input.bizType,
      scenario: input.scenario,
      systemPrompt: input.systemPrompt,
      maxReplyLength: input.maxReplyLength ?? 80,
      extra,
      isActive: false, // 草稿;publish 后才生效
      version: 1,
      description: input.description ?? null,
      createUser: userEmail,
      updateUser: userEmail,
    })
    .returning();
  await writeVersionSnapshot(row, userEmail);
  return toDTO(row);
}

/** 编辑:仅改内容/标题/分类/长度,version 自增 + 写快照。biz_type/scenario 为身份键不可改。 */
export async function update(
  id: number,
  input: Partial<PromptInput> & { systemPrompt: string },
  tenantId: string,
  userEmail: string,
): Promise<PromptDTO | null> {
  const existing = await getById(id, tenantId);
  if (!existing) return null;
  const variables = extractVariables(input.systemPrompt);
  const extra: PromptExtra = {
    title: input.title ?? existing.title,
    category: input.category ?? existing.category,
    variables,
  };
  const [row] = await db
    .update(promptConfig)
    .set({
      systemPrompt: input.systemPrompt,
      maxReplyLength: input.maxReplyLength ?? existing.maxReplyLength,
      description: input.description ?? existing.description,
      extra,
      version: existing.version + 1,
      updateUser: userEmail,
      updateTime: new Date(),
    })
    .where(eq(promptConfig.id, id))
    .returning();
  await writeVersionSnapshot(row, userEmail);
  return toDTO(row);
}

/** 发布:翻 is_active=true 并清 Redis 缓存(零延迟生效)。 */
export async function publish(id: number, tenantId: string, userEmail: string): Promise<PromptDTO | null> {
  const existing = await getById(id, tenantId);
  if (!existing) return null;
  const [row] = await db
    .update(promptConfig)
    .set({ isActive: true, updateUser: userEmail, updateTime: new Date() })
    .where(eq(promptConfig.id, id))
    .returning();
  await invalidatePromptCache(existing.tenantId, existing.bizType, existing.scenario);
  return toDTO(row);
}

/** 克隆:同 biz_type 下新 scenario(身份键必须唯一)。newScenario 缺省 `${src}-copy`。 */
export async function clone(
  id: number,
  tenantId: string,
  userEmail: string,
  newScenario?: string,
): Promise<PromptDTO | null> {
  const src = await getById(id, tenantId);
  if (!src) return null;
  const scenario = newScenario?.trim() || `${src.scenario}-copy`;
  const variables = extractVariables(src.systemPrompt);
  const extra: PromptExtra = {
    title: `${src.title} (克隆)`,
    category: src.category,
    variables,
  };
  const [row] = await db
    .insert(promptConfig)
    .values({
      tenantId,
      bizType: src.bizType,
      scenario,
      systemPrompt: src.systemPrompt,
      maxReplyLength: src.maxReplyLength,
      extra,
      isActive: false,
      version: 1,
      description: src.description,
      createUser: userEmail,
      updateUser: userEmail,
    })
    .returning();
  await writeVersionSnapshot(row, userEmail);
  return toDTO(row);
}

/** 回滚:从 prompt_version 快照恢复内容 → 新 version 写主表 + 写快照 + 清缓存。 */
export async function rollback(
  id: number,
  targetVersion: number,
  tenantId: string,
  userEmail: string,
): Promise<PromptDTO | null> {
  const existing = await getById(id, tenantId);
  if (!existing) return null;
  const snaps = await db
    .select()
    .from(promptVersion)
    .where(
      and(
        eq(promptVersion.tenantId, tenantId),
        eq(promptVersion.bizType, existing.bizType),
        eq(promptVersion.scenario, existing.scenario),
        eq(promptVersion.version, targetVersion),
      ),
    );
  const snap = snaps[0];
  if (!snap) return null;
  const [row] = await db
    .update(promptConfig)
    .set({
      systemPrompt: snap.systemPrompt,
      extra: (snap.snapshot ?? {}) as PromptExtra,
      version: existing.version + 1,
      updateUser: userEmail,
      updateTime: new Date(),
    })
    .where(eq(promptConfig.id, id))
    .returning();
  await writeVersionSnapshot(row, userEmail);
  await invalidatePromptCache(existing.tenantId, existing.bizType, existing.scenario);
  return toDTO(row);
}

export interface VersionDTO {
  id: number;
  version: number;
  systemPrompt: string;
  snapshot: PromptExtra;
  updateUser: string;
  updateTime: Date;
}

export async function getVersions(id: number, tenantId: string): Promise<VersionDTO[] | null> {
  const existing = await getById(id, tenantId);
  if (!existing) return null;
  const rows = await db
    .select()
    .from(promptVersion)
    .where(
      and(
        eq(promptVersion.tenantId, tenantId),
        eq(promptVersion.bizType, existing.bizType),
        eq(promptVersion.scenario, existing.scenario),
      ),
    )
    .orderBy(desc(promptVersion.version));
  return rows.map((r) => ({
    id: r.id,
    version: r.version,
    systemPrompt: r.systemPrompt,
    snapshot: (r.snapshot ?? {}) as PromptExtra,
    updateUser: r.updateUser,
    updateTime: r.updateTime,
  }));
}

export async function remove(id: number, tenantId: string): Promise<boolean> {
  const existing = await getById(id, tenantId);
  if (!existing) return false;
  await db.delete(promptConfig).where(eq(promptConfig.id, id));
  await invalidatePromptCache(existing.tenantId, existing.bizType, existing.scenario);
  return true;
}
