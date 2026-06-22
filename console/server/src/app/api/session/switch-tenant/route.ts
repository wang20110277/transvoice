/** POST /api/session/switch-tenant — 切换活跃租户(更新当前 session.active_tenant_id)。 */
import { NextResponse } from 'next/server';
import { requireAuth, isDenial } from '@/lib/guards';
import { getSession } from '@/auth/session';
import { db } from '@/db/client';
import { tenant, userTenant, session as sessionTable } from '@/db/schema';
import { eq, and } from 'drizzle-orm';

export async function POST(req: Request) {
  const authCtx = await requireAuth();
  if (isDenial(authCtx)) return authCtx;
  const body = (await req.json()) as { tenantId?: string };
  if (!body.tenantId) {
    return NextResponse.json({ error: 'tenantId 必填' }, { status: 400 });
  }

  const session = await getSession();
  if (!session) return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  const userId = session.user.id;
  const sessionId = (session as { session?: { id?: string } }).session?.id;

  // 目标 tenant 存在且 active
  const [target] = await db.select().from(tenant).where(eq(tenant.id, body.tenantId));
  if (!target) return NextResponse.json({ error: 'tenant not found' }, { status: 404 });
  if (target.status !== 'active') {
    return NextResponse.json({ error: '租户已停用' }, { status: 409 });
  }

  // 权限校验:platform_admin 可切任意;普通用户须有 user_tenant 归属
  if (authCtx.role !== 'platform_admin') {
    const [ut] = await db
      .select()
      .from(userTenant)
      .where(and(eq(userTenant.userId, userId), eq(userTenant.tenantId, body.tenantId)));
    if (!ut) {
      return NextResponse.json({ error: '无权切换至此租户' }, { status: 403 });
    }
  }

  if (!sessionId) {
    return NextResponse.json({ error: 'session id not found' }, { status: 500 });
  }
  await db
    .update(sessionTable)
    .set({ activeTenantId: body.tenantId })
    .where(eq(sessionTable.id, sessionId));
  return NextResponse.json({ ok: true, activeTenantId: body.tenantId });
}
