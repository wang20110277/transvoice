/**
 * 首页统计服务层 — call_session 只读聚合（按 activeTenantId 隔离）。
 *
 * 返回 overview（今日/累计/平均时长/biz_type 分布）+ trend（近7天每日通话量）。
 * 5 个轻查询 Promise.all 并发。avgDuration / date_trunc 用 sql 模板（Drizzle 不直接支持 PG interval 运算）。
 * byBizType / trend 在 JS 层补全（无通话的 biz_type 与日期填 0），保证返回结构稳定。
 */
import { and, count, eq, gte, sql } from 'drizzle-orm';
import { db } from '@/db/client';
import { callSession } from '@/db/schema';

export interface StatsOverview {
  today: number;
  total: number;
  avgDurationMs: number;
  byBizType: Record<string, number>;
}

export interface StatsTrendPoint {
  date: string; // YYYY-MM-DD
  count: number;
}

export interface StatsResult {
  overview: StatsOverview;
  trend: StatsTrendPoint[]; // 近7天，日期升序，恒为 7 个点
}

const BIZ_TYPES = ['customer_service', 'collection', 'marketing'];

function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export async function getStats(tenantId: string): Promise<StatsResult> {
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const trendStart = new Date(todayStart);
  trendStart.setDate(trendStart.getDate() - 6); // 含今天，往前 6 天

  const tenantCond = eq(callSession.tenantId, tenantId);

  const [totalRows, todayRows, avgRows, byBizRows, trendRows] = await Promise.all([
    // 累计通话数
    db.select({ n: count() }).from(callSession).where(tenantCond),
    // 今日通话数（start_ts ≥ 当日 0 点）
    db.select({ n: count() }).from(callSession).where(and(tenantCond, gte(callSession.startTs, todayStart))),
    // 平均通话时长（ms，仅已结束通话；无则 0）
    db.select({
      avg: sql<number>`coalesce(avg(extract(epoch from (end_ts - start_ts)) * 1000), 0)`,
    }).from(callSession).where(and(tenantCond, sql`end_ts is not null`)),
    // biz_type 分布
    db.select({ bizType: callSession.bizType, n: count() })
      .from(callSession).where(tenantCond).groupBy(callSession.bizType),
    // 近 7 天每日通话量
    db.select({
      date: sql<string>`to_char(date_trunc('day', start_ts), 'YYYY-MM-DD')`,
      n: count(),
    }).from(callSession)
      .where(and(tenantCond, gte(callSession.startTs, trendStart)))
      .groupBy(sql`date_trunc('day', start_ts)`)
      .orderBy(sql`date_trunc('day', start_ts)`),
  ]);

  // byBizType 补全 3 个 biz_type（无通话的为 0）
  const byBizType: Record<string, number> = {};
  for (const b of BIZ_TYPES) byBizType[b] = 0;
  for (const r of byBizRows) byBizType[r.bizType] = Number(r.n);

  // trend 补全近 7 天（无通话的日期填 0），保证 7 个点
  const trendMap = new Map<string, number>();
  for (const r of trendRows) trendMap.set(r.date, Number(r.n));
  const trend: StatsTrendPoint[] = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date(trendStart);
    d.setDate(d.getDate() + i);
    const key = fmtDate(d);
    trend.push({ date: key, count: trendMap.get(key) ?? 0 });
  }

  return {
    overview: {
      today: Number(todayRows[0]?.n ?? 0),
      total: Number(totalRows[0]?.n ?? 0),
      avgDurationMs: Math.round(Number(avgRows[0]?.avg ?? 0)),
      byBizType,
    },
    trend,
  };
}
