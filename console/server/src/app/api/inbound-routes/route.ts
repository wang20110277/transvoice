/** GET /api/inbound-routes — 列表(按 tenant);POST — 新建。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial, conflict, isUniqueViolation } from '@/lib/guards';
import { listByTenant, create, type RouteInput } from '@/lib/routes-service';

export async function GET() {
  const auth = await requirePermission('route:view');
  if (isDenial(auth)) return auth;
  const rows = await listByTenant(auth.tenantId);
  return NextResponse.json({ routes: rows });
}

export async function POST(req: Request) {
  const auth = await requirePermission('route:create');
  if (isDenial(auth)) return auth;
  const body = (await req.json()) as Partial<RouteInput>;
  if (!body.did || !body.bizType) {
    return NextResponse.json({ error: 'did / bizType 必填' }, { status: 400 });
  }
  try {
    const row = await create(body as RouteInput, auth.tenantId, auth.email);
    return NextResponse.json(row, { status: 201 });
  } catch (e) {
    if (isUniqueViolation(e)) return conflict('该 DID 已存在(精确号全局唯一)');
    throw e;
  }
}
