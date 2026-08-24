/** GET /api/session/tenants — 当前用户可切换的租户列表。
 *  platform_admin:全部 tenant;普通用户:其 user_tenant 归属(按 active 过滤留给前端)。 */
import { NextResponse } from 'next/server';
import { requireAuth, isDenial } from '@/lib/guards';
import { getSession } from '@/auth/session';
import { db } from '@/db/client';
import { tenant, userTenant } from '@/db/schema';
import { eq, asc } from 'drizzle-orm';

export async function GET() {
  const auth = await requireAuth();
  if (isDenial(auth)) return auth;
  const session = await getSession();
  if (!session) return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  const userId = session.user.id;

  const rows =
    auth.role === 'platform_admin'
      ? await db.select({ id: tenant.id, name: tenant.name }).from(tenant).orderBy(asc(tenant.name))
      : await db
          .select({ id: tenant.id, name: tenant.name })
          .from(tenant)
          .innerJoin(userTenant, eq(userTenant.tenantId, tenant.id))
          .where(eq(userTenant.userId, userId))
          .orderBy(asc(tenant.name));

  return NextResponse.json({ tenants: rows });
}
