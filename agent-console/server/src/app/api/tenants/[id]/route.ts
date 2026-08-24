/** GET / PUT / DELETE /api/tenants/:id。仅 platform_admin。 */
import { NextResponse } from 'next/server';
import { requirePlatformAdmin, isDenial, conflict, isUniqueViolation } from '@/lib/guards';
import { db } from '@/db/client';
import { tenant, userTenant, type tenant as TenantT } from '@/db/schema';
import { eq, sql } from 'drizzle-orm';

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePlatformAdmin();
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const [row] = await db.select().from(tenant).where(eq(tenant.id, id));
  if (!row) return NextResponse.json({ error: 'not found' }, { status: 404 });
  return NextResponse.json(row);
}

export async function PUT(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePlatformAdmin();
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const body = (await req.json()) as Partial<Pick<typeof TenantT.$inferInsert, 'name' | 'status' | 'description' | 'quota'>>;
  const updates: Record<string, unknown> = { updateUser: auth.email, updateTime: new Date() };
  if (body.name !== undefined) updates.name = body.name;
  if (body.status !== undefined) updates.status = body.status;
  if (body.description !== undefined) updates.description = body.description;
  if (body.quota !== undefined) updates.quota = body.quota;
  try {
    const [row] = await db
      .update(tenant)
      .set(updates)
      .where(eq(tenant.id, id))
      .returning();
    if (!row) return NextResponse.json({ error: 'not found' }, { status: 404 });
    return NextResponse.json(row);
  } catch (e) {
    if (isUniqueViolation(e)) return conflict('租户 name 已存在');
    throw e;
  }
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePlatformAdmin();
  if (isDenial(auth)) return auth;
  const { id } = await params;
  // 删除前校验:无 user_tenant 关联
  const [{ count }] = await db
    .select({ count: sql<number>`count(*)::int` })
    .from(userTenant)
    .where(eq(userTenant.tenantId, id));
  if (Number(count) > 0) {
    return NextResponse.json(
      { error: `仍有 ${count} 个用户归属此租户,请先解除` },
      { status: 409 },
    );
  }
  const [row] = await db.delete(tenant).where(eq(tenant.id, id)).returning();
  if (!row) return NextResponse.json({ error: 'not found' }, { status: 404 });
  return NextResponse.json({ ok: true });
}
