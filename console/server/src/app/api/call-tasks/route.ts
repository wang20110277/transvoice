/** GET /api/call-tasks — 列表(按 tenant);POST — 新建。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { listByTenant, create, PromptTenantMismatchError, type CallTaskInput } from '@/lib/call-tasks-service';

export async function GET() {
  const auth = await requirePermission('calltask:view');
  if (isDenial(auth)) return auth;
  const rows = await listByTenant(auth.tenantId);
  return NextResponse.json({ tasks: rows });
}

export async function POST(req: Request) {
  const auth = await requirePermission('calltask:create');
  if (isDenial(auth)) return auth;
  const body = (await req.json()) as Partial<CallTaskInput>;
  if (!body.name?.trim() || !body.promptId) {
    return NextResponse.json({ error: 'name / promptId 必填' }, { status: 400 });
  }
  try {
    const row = await create(body as CallTaskInput, auth.tenantId, auth.email);
    return NextResponse.json(row, { status: 201 });
  } catch (e) {
    if (e instanceof PromptTenantMismatchError) {
      return NextResponse.json({ error: e.message }, { status: 400 });
    }
    throw e;
  }
}
