/**
 * 外呼任务服务层 — call_task 表 CRUD（定义层，不含执行态）。
 *
 * 任务绑定 promptId（引用 prompt_config.id）。绑定时 MUST 校验目标提示词属于
 * 同一 tenant_id（跨租户绑定拒绝）。策略字段（concurrentLimit/allowedHours/
 * redialStrategy）仅为声明性存储，本期无执行器消费。
 *
 * 列名/维度与 agent-flow SQLAlchemy (src/db/models.py CallTask) 严格一致。
 */
import { and, eq } from 'drizzle-orm';
import { db } from '@/db/client';
import { callTask, promptConfig } from '@/db/schema';

export interface CallTaskDTO {
  id: number;
  tenantId: string;
  name: string;
  promptId: number;
  kbIds: unknown[];
  status: string;
  concurrentLimit: number;
  allowedHours: string | null;
  redialStrategy: Record<string, unknown>;
  deptId: string | null;
  description: string | null;
  createUser: string;
  updateUser: string;
  updateTime: Date;
}

export interface CallTaskInput {
  name: string;
  promptId: number;
  kbIds?: unknown[];
  status?: string;
  concurrentLimit?: number;
  allowedHours?: string | null;
  redialStrategy?: Record<string, unknown>;
  deptId?: string | null;
  description?: string | null;
}

/** 绑定的提示词不属于当前租户时抛出，由 route 层转 400。 */
export class PromptTenantMismatchError extends Error {
  constructor() {
    super('绑定的提示词不存在或不属于当前租户');
    this.name = 'PromptTenantMismatchError';
  }
}

type Row = typeof callTask.$inferSelect;

function toDTO(row: Row): CallTaskDTO {
  return {
    id: row.id,
    tenantId: row.tenantId,
    name: row.name,
    promptId: row.promptId,
    kbIds: row.kbIds as unknown[],
    status: row.status,
    concurrentLimit: row.concurrentLimit,
    allowedHours: row.allowedHours,
    redialStrategy: row.redialStrategy as Record<string, unknown>,
    deptId: row.deptId,
    description: row.description,
    createUser: row.createUser,
    updateUser: row.updateUser,
    updateTime: row.updateTime,
  };
}

/** 校验 promptId 属于指定租户；不存在或跨租户则抛 PromptTenantMismatchError。 */
async function assertPromptInTenant(promptId: number, tenantId: string): Promise<void> {
  const rows = await db
    .select({ id: promptConfig.id })
    .from(promptConfig)
    .where(and(eq(promptConfig.id, promptId), eq(promptConfig.tenantId, tenantId)));
  if (rows.length === 0) throw new PromptTenantMismatchError();
}

export async function listByTenant(tenantId: string): Promise<CallTaskDTO[]> {
  const rows = await db.select().from(callTask).where(eq(callTask.tenantId, tenantId));
  return rows.map(toDTO);
}

export async function getById(id: number, tenantId: string): Promise<CallTaskDTO | null> {
  const rows = await db
    .select()
    .from(callTask)
    .where(and(eq(callTask.id, id), eq(callTask.tenantId, tenantId)));
  return rows[0] ? toDTO(rows[0]) : null;
}

export async function create(
  input: CallTaskInput,
  tenantId: string,
  userEmail: string,
): Promise<CallTaskDTO> {
  await assertPromptInTenant(input.promptId, tenantId);
  const [row] = await db
    .insert(callTask)
    .values({
      tenantId,
      name: input.name,
      promptId: input.promptId,
      kbIds: input.kbIds ?? [],
      status: input.status ?? 'idle',
      concurrentLimit: input.concurrentLimit ?? 1,
      allowedHours: input.allowedHours ?? null,
      redialStrategy: input.redialStrategy ?? {},
      deptId: input.deptId ?? null,
      description: input.description ?? null,
      createUser: userEmail,
      updateUser: userEmail,
    })
    .returning();
  return toDTO(row);
}

export async function update(
  id: number,
  input: Partial<CallTaskInput>,
  tenantId: string,
  userEmail: string,
): Promise<CallTaskDTO | null> {
  const existing = await getById(id, tenantId);
  if (!existing) return null;
  // 绑定的提示词变更时，重新校验同租户归属
  if (input.promptId !== undefined && input.promptId !== existing.promptId) {
    await assertPromptInTenant(input.promptId, tenantId);
  }
  const [row] = await db
    .update(callTask)
    .set({
      name: input.name ?? existing.name,
      promptId: input.promptId ?? existing.promptId,
      kbIds: input.kbIds ?? existing.kbIds,
      status: input.status ?? existing.status,
      concurrentLimit: input.concurrentLimit ?? existing.concurrentLimit,
      allowedHours: input.allowedHours ?? existing.allowedHours,
      redialStrategy: input.redialStrategy ?? existing.redialStrategy,
      deptId: input.deptId ?? existing.deptId,
      description: input.description ?? existing.description,
      updateUser: userEmail,
      updateTime: new Date(),
    })
    .where(eq(callTask.id, id))
    .returning();
  return toDTO(row);
}

export async function remove(id: number, tenantId: string): Promise<boolean> {
  const existing = await getById(id, tenantId);
  if (!existing) return false;
  await db.delete(callTask).where(eq(callTask.id, id));
  return true;
}
