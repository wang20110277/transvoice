/** GET /api/users — 全用户列表(跨租户,含归属租户数组)。仅 platform_admin。 */
import { NextResponse } from 'next/server';
import { requirePlatformAdmin, isDenial } from '@/lib/guards';
import { db } from '@/db/client';
import { user, userTenant } from '@/db/schema';
import { asc } from 'drizzle-orm';

export async function GET() {
  const auth = await requirePlatformAdmin();
  if (isDenial(auth)) return auth;
  // 两步查询 + JS 聚合(drizzle 对 json_agg 支持有限,两步更可靠)
  const users = await db
    .select({ id: user.id, email: user.email, name: user.name, role: user.role, tenantId: user.tenantId })
    .from(user)
    .orderBy(asc(user.email));
  const uts = await db.select().from(userTenant);
  const map = new Map<string, { tenantId: string; isPrimary: boolean }[]>();
  for (const ut of uts) {
    if (!map.has(ut.userId)) map.set(ut.userId, []);
    map.get(ut.userId)!.push({ tenantId: ut.tenantId, isPrimary: ut.isPrimary });
  }
  const result = users.map((u) => ({ ...u, tenants: map.get(u.id) ?? [] }));
  return NextResponse.json({ users: result });
}
