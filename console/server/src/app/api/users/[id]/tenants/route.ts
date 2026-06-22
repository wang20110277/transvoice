/** POST /api/users/:id/tenants — 分配/取消租户、设主租户。仅 platform_admin。 */
import { NextResponse } from 'next/server';
import { requirePlatformAdmin, isDenial } from '@/lib/guards';
import { db } from '@/db/client';
import { user, userTenant } from '@/db/schema';
import { eq, and } from 'drizzle-orm';

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePlatformAdmin();
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const body = (await req.json()) as {
    tenantId?: string;
    action?: 'add' | 'remove' | 'setPrimary';
  };
  if (!body.tenantId || !body.action) {
    return NextResponse.json({ error: 'tenantId / action 必填' }, { status: 400 });
  }
  const [target] = await db.select({ id: user.id }).from(user).where(eq(user.id, id));
  if (!target) return NextResponse.json({ error: 'user not found' }, { status: 404 });

  if (body.action === 'add') {
    await db
      .insert(userTenant)
      .values({ userId: id, tenantId: body.tenantId, isPrimary: false })
      .onConflictDoNothing({ target: [userTenant.userId, userTenant.tenantId] });
  } else if (body.action === 'remove') {
    // 移除的是主租户 → 重置 user.tenantId 到剩余归属,无则 default
    const [ut] = await db
      .select()
      .from(userTenant)
      .where(and(eq(userTenant.userId, id), eq(userTenant.tenantId, body.tenantId)));
    await db
      .delete(userTenant)
      .where(and(eq(userTenant.userId, id), eq(userTenant.tenantId, body.tenantId)));
    if (ut?.isPrimary) {
      const [rest] = await db
        .select()
        .from(userTenant)
        .where(eq(userTenant.userId, id))
        .limit(1);
      const fallbackTenant = rest?.tenantId ?? 'default';
      await db.update(user).set({ tenantId: fallbackTenant }).where(eq(user.id, id));
      if (rest) {
        await db
          .update(userTenant)
          .set({ isPrimary: true })
          .where(eq(userTenant.id, rest.id));
      }
    }
  } else if (body.action === 'setPrimary') {
    // 清其他主 → 目标置主(存在则 update,不存在则 insert)→ 同步 user.tenantId 缓存
    await db.update(userTenant).set({ isPrimary: false }).where(eq(userTenant.userId, id));
    await db
      .insert(userTenant)
      .values({ userId: id, tenantId: body.tenantId, isPrimary: true })
      .onConflictDoUpdate({
        target: [userTenant.userId, userTenant.tenantId],
        set: { isPrimary: true },
      });
    await db.update(user).set({ tenantId: body.tenantId }).where(eq(user.id, id));
  } else {
    return NextResponse.json(
      { error: 'action 必须为 add / remove / setPrimary' },
      { status: 400 },
    );
  }

  const [updated] = await db
    .select({ id: user.id, email: user.email, role: user.role, tenantId: user.tenantId })
    .from(user)
    .where(eq(user.id, id));
  return NextResponse.json(updated);
}
