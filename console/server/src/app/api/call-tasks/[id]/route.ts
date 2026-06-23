/** GET / PUT / DELETE /api/call-tasks/:id。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import {
  getById,
  update,
  remove,
  PromptTenantMismatchError,
  type CallTaskInput,
} from '@/lib/call-tasks-service';

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('calltask:view');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const row = await getById(Number(id), auth.tenantId);
  if (!row) return NextResponse.json({ error: 'not found' }, { status: 404 });
  return NextResponse.json(row);
}

export async function PUT(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('calltask:update');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const body = (await req.json()) as Partial<CallTaskInput>;
  try {
    const row = await update(Number(id), body, auth.tenantId, auth.email);
    if (!row) return NextResponse.json({ error: 'not found' }, { status: 404 });
    return NextResponse.json(row);
  } catch (e) {
    if (e instanceof PromptTenantMismatchError) {
      return NextResponse.json({ error: e.message }, { status: 400 });
    }
    throw e;
  }
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('calltask:delete');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const ok = await remove(Number(id), auth.tenantId);
  if (!ok) return NextResponse.json({ error: 'not found' }, { status: 404 });
  return NextResponse.json({ ok: true });
}
