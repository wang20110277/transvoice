/**
 * DID 路由服务层 — inbound_route 表 CRUD。
 *
 * DID(精确号)或号段(did_pattern 正则)→ (tenant_id, biz_type, scenario)。
 * agent-flow CHANNEL_ANSWER 查此表解析三元组(精确号优先,号段兜底)。
 * did 精确号全局唯一(UNIQUE 约束);按 tenant_id 隔离运营。
 *
 * 无缓存层:路由查询直查 DB,变更即生效(呼入每次实时查)。
 */
import { and, eq } from 'drizzle-orm';
import { db } from '@/db/client';
import { inboundRoute } from '@/db/schema';

export interface RouteDTO {
  id: number;
  did: string;
  didPattern: string | null;
  tenantId: string;
  bizType: string;
  scenario: string;
  isActive: boolean;
  description: string | null;
  createUser: string;
  updateUser: string;
  updateTime: Date;
}

export interface RouteInput {
  did: string;
  didPattern?: string | null;
  bizType: string;
  scenario?: string;
  isActive?: boolean;
  description?: string | null;
}

type Row = typeof inboundRoute.$inferSelect;

function toDTO(row: Row): RouteDTO {
  return {
    id: row.id,
    did: row.did,
    didPattern: row.didPattern,
    tenantId: row.tenantId,
    bizType: row.bizType,
    scenario: row.scenario,
    isActive: row.isActive,
    description: row.description,
    createUser: row.createUser,
    updateUser: row.updateUser,
    updateTime: row.updateTime,
  };
}

export async function listByTenant(tenantId: string): Promise<RouteDTO[]> {
  const rows = await db.select().from(inboundRoute).where(eq(inboundRoute.tenantId, tenantId));
  return rows.map(toDTO);
}

export async function getById(id: number, tenantId: string): Promise<RouteDTO | null> {
  const rows = await db
    .select()
    .from(inboundRoute)
    .where(and(eq(inboundRoute.id, id), eq(inboundRoute.tenantId, tenantId)));
  return rows[0] ? toDTO(rows[0]) : null;
}

export async function create(input: RouteInput, tenantId: string, userEmail: string): Promise<RouteDTO> {
  const [row] = await db
    .insert(inboundRoute)
    .values({
      tenantId,
      did: input.did,
      didPattern: input.didPattern ?? null,
      bizType: input.bizType,
      scenario: input.scenario ?? 'default',
      isActive: input.isActive ?? true,
      description: input.description ?? null,
      createUser: userEmail,
      updateUser: userEmail,
    })
    .returning();
  return toDTO(row);
}

export async function update(
  id: number,
  input: Partial<RouteInput>,
  tenantId: string,
  userEmail: string,
): Promise<RouteDTO | null> {
  const existing = await getById(id, tenantId);
  if (!existing) return null;
  const [row] = await db
    .update(inboundRoute)
    .set({
      did: input.did ?? existing.did,
      didPattern: input.didPattern ?? existing.didPattern,
      bizType: input.bizType ?? existing.bizType,
      scenario: input.scenario ?? existing.scenario,
      isActive: input.isActive ?? existing.isActive,
      description: input.description ?? existing.description,
      updateUser: userEmail,
      updateTime: new Date(),
    })
    .where(eq(inboundRoute.id, id))
    .returning();
  return toDTO(row);
}

export async function remove(id: number, tenantId: string): Promise<boolean> {
  const existing = await getById(id, tenantId);
  if (!existing) return false;
  await db.delete(inboundRoute).where(eq(inboundRoute.id, id));
  return true;
}
