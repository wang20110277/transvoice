/**
 * 外呼号码清单服务层 — call_target 表 CRUD + 进度聚合。
 *
 * 所有操作按 tenant_id 隔离：先校验 task 属于当前租户（跨租户返回 null → route 层 404）。
 * 录入去重：UNIQUE(task_id, phone_hash) 保证任务内号码唯一，批量插入用 ON CONFLICT DO NOTHING。
 * 列名/维度与 agent-flow SQLAlchemy (src/db/models.py CallTarget) 严格一致。
 */
import { and, eq, sql } from 'drizzle-orm';
import { createHash } from 'node:crypto';
import { db } from '@/db/client';
import { callTarget, callTask } from '@/db/schema';

export interface CallTargetDTO {
  id: number;
  taskId: number;
  phoneMasked: string | null;
  userKey: string;
  status: string;
  attemptCount: number;
  maxAttempts: number;
  nextAttemptTs: Date | null;
  lastHangupCause: string | null;
  updateTime: Date;
}

export interface CallTargetProgress {
  total: number;
  pending: number;
  dialing: number;
  answered: number;
  noAnswer: number;
  failed: number;
  done: number;
}

/** 手机号 → sha256（与 agent-flow _phone_hash 严格一致：去重键，跨端必须同算法）。 */
function phoneHash(phone: string): string {
  return createHash('sha256').update(phone).digest('hex');
}

/** 脱敏：保留前 3 后 4。 */
function maskPhone(phone: string): string {
  const d = phone.replace(/\D/g, '');
  if (d.length < 7) return d;
  return `${d.slice(0, 3)}****${d.slice(-4)}`;
}

type Row = typeof callTarget.$inferSelect;

function toDTO(row: Row): CallTargetDTO {
  return {
    id: row.id,
    taskId: row.taskId,
    phoneMasked: row.phoneMasked,
    userKey: row.userKey,
    status: row.status,
    attemptCount: row.attemptCount,
    maxAttempts: row.maxAttempts,
    nextAttemptTs: row.nextAttemptTs,
    lastHangupCause: row.lastHangupCause,
    updateTime: row.updateTime,
  };
}

/** 校验 task 属于指定租户；不存在/跨租户返回 false。 */
async function taskInTenant(taskId: number, tenantId: string): Promise<boolean> {
  const rows = await db
    .select({ id: callTask.id })
    .from(callTask)
    .where(and(eq(callTask.id, taskId), eq(callTask.tenantId, tenantId)));
  return rows.length > 0;
}

export async function listByTask(taskId: number, tenantId: string): Promise<CallTargetDTO[]> {
  if (!(await taskInTenant(taskId, tenantId))) return [];
  const rows = await db
    .select()
    .from(callTarget)
    .where(eq(callTarget.taskId, taskId));
  return rows.map(toDTO);
}

export async function create(
  taskId: number,
  tenantId: string,
  phone: string,
  maxAttempts: number,
  userEmail: string,
): Promise<CallTargetDTO | null> {
  if (!(await taskInTenant(taskId, tenantId))) return null;
  const [row] = await db
    .insert(callTarget)
    .values({
      taskId,
      tenantId,
      phoneHash: phoneHash(phone),
      phoneMasked: maskPhone(phone),
      userKey: phone,
      status: 'pending',
      maxAttempts,
      createUser: userEmail,
      updateUser: userEmail,
    })
    .onConflictDoNothing()
    .returning();
  return row ? toDTO(row) : null; // 去重命中 onConflict → null（调用方按已存在处理）
}

/**
 * 批量录入（CSV 文本，每行一个号码）。返回 {inserted, skipped}。
 * 去重：任务内已存在的 phone_hash 跳过（onConflictDoNothing）。
 */
export async function bulkCreateFromCsv(
  taskId: number,
  tenantId: string,
  csvText: string,
  maxAttempts: number,
  userEmail: string,
): Promise<{ inserted: number; skipped: number }> {
  if (!(await taskInTenant(taskId, tenantId))) return { inserted: 0, skipped: 0 };
  const phones = csvText
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l.length > 0 && !/^phone|号码/i.test(l)); // 去表头/空行
  if (phones.length === 0) return { inserted: 0, skipped: 0 };

  const values = phones.map((p) => ({
    taskId,
    tenantId,
    phoneHash: phoneHash(p),
    phoneMasked: maskPhone(p),
    userKey: p,
    status: 'pending',
    maxAttempts,
    createUser: userEmail,
    updateUser: userEmail,
  }));
  const result = await db
    .insert(callTarget)
    .values(values)
    .onConflictDoNothing()
    .returning({ id: callTarget.id });
  return { inserted: result.length, skipped: phones.length - result.length };
}

export async function remove(targetId: number, tenantId: string): Promise<boolean> {
  // 跨租户防护：只删属于该 tenant 的 target
  const [row] = await db
    .delete(callTarget)
    .where(and(eq(callTarget.id, targetId), eq(callTarget.tenantId, tenantId)))
    .returning({ id: callTarget.id });
  return !!row;
}

export async function progress(taskId: number, tenantId: string): Promise<CallTargetProgress | null> {
  if (!(await taskInTenant(taskId, tenantId))) return null;
  const rows = await db
    .select({ status: callTarget.status, n: sql<number>`count(*)::int` })
    .from(callTarget)
    .where(eq(callTarget.taskId, taskId))
    .groupBy(callTarget.status);
  const p: CallTargetProgress = {
    total: 0, pending: 0, dialing: 0, answered: 0, noAnswer: 0, failed: 0, done: 0,
  };
  for (const r of rows) {
    p.total += r.n;
    switch (r.status) {
      case 'pending': p.pending += r.n; break;
      case 'dialing': p.dialing += r.n; break;
      case 'answered': p.answered += r.n; break;
      case 'no_answer': p.noAnswer += r.n; break;
      case 'failed': p.failed += r.n; break;
      case 'done': p.done += r.n; break;
    }
  }
  return p;
}
