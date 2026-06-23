/**
 * 通话记录服务层 — call_session/turn/event/artifact 四表只读聚合。
 *
 * 按 activeTenantId 隔离；跨租户详情/recording 返回 null（route 转 404，不泄漏存在性）。
 * 录音链接存 call_artifact(kind='recording')（与 call_session 一对多），非 call_session 列。
 * phone_hash 不下发前端，只给 phone_masked。
 */
import { and, asc, count, desc, eq, gte, like, lte } from 'drizzle-orm';
import { db } from '@/db/client';
import { callSession, callTurn, callEvent, callArtifact } from '@/db/schema';
import { presignedRecordingUrl } from './minio-client';

export interface SessionDTO {
  id: number;
  callId: string;
  fsUuid: string;
  bizType: string;
  tenantId: string | null;
  scenario: string | null;
  phoneMasked: string | null;
  userKey: string;
  startTs: Date;
  endTs: Date | null;
  hangupCause: string | null;
  resultCode: string | null;
  identityVerified: boolean;
  recordingNoticePlayed: boolean;
  durationMs: number | null;
}

type SessionRow = typeof callSession.$inferSelect;

// 剥 phone_hash（脱敏哈希不下发前端，只给 phone_masked）
export function toSessionDTO(row: SessionRow): SessionDTO {
  const durationMs = row.endTs && row.startTs
    ? row.endTs.getTime() - row.startTs.getTime()
    : null;
  return {
    id: row.id, callId: row.callId, fsUuid: row.fsUuid, bizType: row.bizType,
    tenantId: row.tenantId, scenario: row.scenario, phoneMasked: row.phoneMasked,
    userKey: row.userKey, startTs: row.startTs, endTs: row.endTs,
    hangupCause: row.hangupCause, resultCode: row.resultCode,
    identityVerified: row.identityVerified, recordingNoticePlayed: row.recordingNoticePlayed,
    durationMs,
  };
}

export interface ListParams {
  tenantId: string;
  bizType?: string;
  phoneMasked?: string;
  startFrom?: Date;
  startTo?: Date;
  page: number;
  pageSize: number;
}

export async function listCalls(p: ListParams): Promise<{ calls: SessionDTO[]; total: number }> {
  const conds = [eq(callSession.tenantId, p.tenantId)];
  if (p.bizType) conds.push(eq(callSession.bizType, p.bizType));
  if (p.phoneMasked) conds.push(like(callSession.phoneMasked, `%${p.phoneMasked}%`));
  if (p.startFrom) conds.push(gte(callSession.startTs, p.startFrom));
  if (p.startTo) conds.push(lte(callSession.startTs, p.startTo));
  const where = and(...conds);

  const [rows, totalRows] = await Promise.all([
    db.select().from(callSession).where(where).orderBy(desc(callSession.startTs))
      .limit(p.pageSize).offset((p.page - 1) * p.pageSize),
    db.select({ n: count() }).from(callSession).where(where),
  ]);
  return { calls: rows.map(toSessionDTO), total: totalRows[0]?.n ?? 0 };
}

export interface CallDetail {
  session: SessionDTO;
  turns: (typeof callTurn.$inferSelect)[];
  events: (typeof callEvent.$inferSelect)[];
  artifacts: (typeof callArtifact.$inferSelect)[];
}

export async function getCallDetail(id: number, tenantId: string): Promise<CallDetail | null> {
  const sess = await db.select().from(callSession)
    .where(and(eq(callSession.id, id), eq(callSession.tenantId, tenantId)));
  if (sess.length === 0) return null; // 跨租户也走这里 → 404，不泄漏存在性
  const session = sess[0];
  const callId = session.callId;
  const [turns, events, artifacts] = await Promise.all([
    db.select().from(callTurn).where(eq(callTurn.callId, callId)).orderBy(asc(callTurn.ts)),
    db.select().from(callEvent).where(eq(callEvent.callId, callId)).orderBy(asc(callEvent.ts)),
    db.select().from(callArtifact).where(eq(callArtifact.callId, callId)),
  ]);
  return { session: toSessionDTO(session), turns, events, artifacts };
}

export async function getRecordingUrl(id: number, tenantId: string): Promise<string | null> {
  // 先校验 session 归属（跨租户不泄漏），再查 kind='recording' artifact
  const sess = await db.select({ callId: callSession.callId }).from(callSession)
    .where(and(eq(callSession.id, id), eq(callSession.tenantId, tenantId)));
  if (sess.length === 0) return null;
  const arts = await db.select().from(callArtifact)
    .where(and(eq(callArtifact.callId, sess[0].callId), eq(callArtifact.kind, 'recording')));
  if (arts.length === 0) return null;
  return await presignedRecordingUrl(arts[0].uri); // 1h presigned；MinIO 未配置返回 null
}
