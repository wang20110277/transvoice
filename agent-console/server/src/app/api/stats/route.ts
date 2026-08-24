/** GET /api/stats — 首页统计（按 activeTenantId 隔离的 call_session 聚合：overview + 近7天 trend）。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { getStats } from '@/lib/stats-service';

export async function GET() {
  const auth = await requirePermission('call:view');
  if (isDenial(auth)) return auth;
  const result = await getStats(auth.tenantId);
  return NextResponse.json(result);
}
