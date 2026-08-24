/** GET /api/tenants — 全租户列表;POST — 新建。仅 platform_admin。 */
import { NextResponse } from 'next/server';
import { requirePlatformAdmin, isDenial, conflict, isUniqueViolation } from '@/lib/guards';
import { db } from '@/db/client';
import { tenant } from '@/db/schema';
import { asc } from 'drizzle-orm';

export async function GET() {
  const auth = await requirePlatformAdmin();
  if (isDenial(auth)) return auth;
  const rows = await db.select().from(tenant).orderBy(asc(tenant.name));
  return NextResponse.json({ tenants: rows });
}

export async function POST(req: Request) {
  const auth = await requirePlatformAdmin();
  if (isDenial(auth)) return auth;
  const body = (await req.json()) as { id?: string; name?: string; description?: string };
  if (!body.id || !body.name) {
    return NextResponse.json({ error: 'id / name 必填' }, { status: 400 });
  }
  if (!/^[a-z0-9-]+$/.test(body.id)) {
    return NextResponse.json(
      { error: 'id 必须为 kebab-case(小写字母/数字/连字符)' },
      { status: 400 },
    );
  }
  try {
    const [row] = await db
      .insert(tenant)
      .values({
        id: body.id,
        name: body.name,
        description: body.description,
        createUser: auth.email,
        updateUser: auth.email,
      })
      .returning();
    return NextResponse.json(row, { status: 201 });
  } catch (e) {
    if (isUniqueViolation(e)) return conflict('租户 id 或 name 已存在');
    throw e;
  }
}
